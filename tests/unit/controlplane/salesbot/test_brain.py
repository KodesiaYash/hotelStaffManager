"""Unit tests for the SalesBot brain orchestration logic."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from controlplane.control.bot.salesbot import brain as brain_module
from controlplane.control.bot.salesbot import dependencies as dependencies_module
from controlplane.control.bot.salesbot.correction_tracker import PendingCorrection
from controlplane.control.bot.salesbot.services import correction_flow as correction_flow_module
from controlplane.control.bot.salesbot.services import dialogue as dialogue_module
from controlplane.control.bot.salesbot.services import extraction as extraction_module
from controlplane.control.bot.salesbot.services import memory as memory_module
from controlplane.control.bot.salesbot.services import messaging as messaging_module
from controlplane.control.memory.types import RecallBundle


@dataclass
class DummyMemoryService:
    recorded_events: list[Any] = field(default_factory=list)
    opened_tasks: list[dict[str, Any]] = field(default_factory=list)
    closed_tasks: list[dict[str, Any]] = field(default_factory=list)
    episodes: list[dict[str, Any]] = field(default_factory=list)
    learnings: list[dict[str, Any]] = field(default_factory=list)
    summary_refreshes: list[dict[str, Any]] = field(default_factory=list)
    recall_requests: list[Any] = field(default_factory=list)
    recall_bundle: RecallBundle = field(default_factory=RecallBundle)

    def record_event(self, event: Any, *, cache_working_memory: bool = True) -> None:
        self.recorded_events.append((event, cache_working_memory))

    def recall(self, request: Any) -> RecallBundle:
        self.recall_requests.append(request)
        return self.recall_bundle

    def open_task(self, **kwargs: Any) -> None:
        self.opened_tasks.append(kwargs)

    def close_task(self, **kwargs: Any) -> None:
        self.closed_tasks.append(kwargs)

    def remember_sales_correction_episode(self, **kwargs: Any) -> None:
        self.episodes.append(kwargs)

    def remember_sales_learning(self, **kwargs: Any) -> None:
        self.learnings.append(kwargs)

    def refresh_summary(self, **kwargs: Any) -> None:
        self.summary_refreshes.append(kwargs)


class DummyNotificationClient:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    def send_text(self, *, to: str, body: str, quoted: str | None = None) -> None:
        self.sent.append({"to": to, "body": body, "quoted": quoted})


class DummyTracker:
    def __init__(self, pending: PendingCorrection | None = None) -> None:
        self.pending = pending
        self.removed_chat_ids: list[str] = []
        self.added: list[dict[str, Any]] = []
        self.expired: list[PendingCorrection] = []

    def get_pending(self, chat_id: str, sender_id: str | None = None) -> PendingCorrection | None:
        if (
            self.pending
            and self.pending.chat_id == chat_id
            and (sender_id is None or self.pending.sender_id == sender_id)
        ):
            return self.pending
        return None

    def remove_pending(self, chat_id: str, sender_id: str | None = None) -> bool:
        self.removed_chat_ids.append(chat_id)
        if (
            self.pending
            and self.pending.chat_id == chat_id
            and (sender_id is None or self.pending.sender_id == sender_id)
        ):
            self.pending = None
            return True
        return False

    def add_pending(self, **kwargs: Any) -> PendingCorrection:
        self.added.append(kwargs)
        attempt_count = kwargs.pop("attempt_count", 1)
        correction = PendingCorrection(**kwargs)
        correction.attempt_count = attempt_count
        self.pending = correction
        return correction

    def get_and_remove_expired(self) -> list[PendingCorrection]:
        expired = self.expired[:]
        self.expired = []
        return expired


def test_llm_extract_retries_on_parse_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Retry once when the first LLM response cannot be parsed as JSON."""
    responses = ["not json", '{"Service": "Spa"}']

    class DummyLLM:
        def generate(self, _prompt: str) -> str:
            return responses.pop(0)

    monkeypatch.setattr(brain_module, "_get_llm_interface", lambda: DummyLLM())
    monkeypatch.setattr(extraction_module, "get_llm_interface", lambda: DummyLLM())
    result = brain_module.llm_extract("hello")
    assert result == {"Service": "Spa"}


def test_process_message_skips_on_extraction_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip write flow entirely if extraction returns an error payload."""
    monkeypatch.setattr(brain_module, "llm_extract", lambda _msg, **_kwargs: {"error": "bad"})
    monkeypatch.setattr(
        brain_module,
        "_get_sales_audit",
        lambda: (_ for _ in ()).throw(RuntimeError("should not be called")),
    )
    brain_module.process_message("hello")


def test_llm_extract_includes_recalled_memory_context(monkeypatch: pytest.MonkeyPatch) -> None:
    prompts: list[str] = []

    class DummyLLM:
        def generate(self, prompt: str) -> str:
            prompts.append(prompt)
            return '{"Service": "Spa"}'

    monkeypatch.setattr(brain_module, "_get_llm_interface", lambda: DummyLLM())
    monkeypatch.setattr(extraction_module, "get_llm_interface", lambda: DummyLLM())
    monkeypatch.setattr(
        extraction_module,
        "build_sales_memory_context",
        lambda **_kwargs: "## Learned Facts\n- Prefer The Sahara Room naming.",
    )

    result = brain_module.llm_extract("Service: Spa", chat_id="chat-1", sender_id="42", sender_name="alice")

    assert result == {"Service": "Spa"}
    assert "Prefer The Sahara Room naming" in prompts[0]
    assert "Service: Spa" in prompts[0]


def test_send_correction_request_records_memory_and_opens_task(monkeypatch: pytest.MonkeyPatch) -> None:
    memory = DummyMemoryService()
    client = DummyNotificationClient()
    monkeypatch.setattr(memory_module, "get_memory_service", lambda: memory)
    monkeypatch.setattr(messaging_module, "get_notification_client", lambda: client)
    monkeypatch.setattr(
        messaging_module,
        "build_correction_request_message",
        lambda **_kwargs: "Hey, I need a little more detail before I can record this sale.",
    )

    success = brain_module._send_correction_request(
        "chat-1",
        ["Service is empty or missing"],
        {"Service": ""},
        sender_name="alice",
        quoted_message_id="msg-1",
    )

    assert success is True
    assert client.sent[0]["to"] == "chat-1"
    assert client.sent[0]["body"].startswith("Dear alice,")
    assert memory.recorded_events[0][0].event_type == "correction_request"
    assert memory.opened_tasks[0]["task_type"] == brain_module.CORRECTION_TASK_TYPE
    assert memory.summary_refreshes


def test_check_and_handle_correction_reply_resolves_natural_service_reply_and_confirms_recording(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    memory = DummyMemoryService()
    client = DummyNotificationClient()
    pending = PendingCorrection(
        chat_id="chat-1",
        sender_id="42",
        sender_name="alice",
        original_message="hamam vip",
        extracted_data={"Service": "hamam vip"},
        validation_failures=["Service 'hamam vip' not found in price list"],
        service_suggestions=[("Hammam VIP", 0.94), ("Massage VIP", 0.71)],
    )
    tracker = DummyTracker(pending)
    reprocessed: list[dict[str, Any]] = []

    monkeypatch.setattr(memory_module, "get_memory_service", lambda: memory)
    monkeypatch.setattr(correction_flow_module, "get_correction_tracker", lambda: tracker)
    monkeypatch.setattr(messaging_module, "get_notification_client", lambda: client)
    monkeypatch.setattr(
        correction_flow_module,
        "interpret_service_reply",
        lambda **_kwargs: dialogue_module.ServiceReplyInterpretation(
            matched_service="Hammam VIP",
            confidence="high",
        ),
    )
    monkeypatch.setattr(
        brain_module,
        "process_message",
        lambda message, sender_id=None, chat_id=None, message_id=None, sender_name=None: (
            reprocessed.append(
                {"message": message, "sender_id": sender_id, "chat_id": chat_id, "sender_name": sender_name}
            )
            or True
        ),
    )

    handled = brain_module.check_and_handle_correction_reply("I meant hammam vip", "42", "alice", "chat-1")

    assert handled is True
    assert tracker.removed_chat_ids == ["chat-1"]
    assert memory.closed_tasks[0]["status"] == "resolved"
    assert memory.episodes and "Hammam VIP" in memory.episodes[0]["content"]
    assert memory.learnings and "Hammam VIP" in memory.learnings[0]["content"]
    assert reprocessed and "Hammam VIP" in reprocessed[0]["message"]
    assert client.sent and "Thank you, your entry has been recorded." in client.sent[-1]["body"]


def test_check_and_handle_correction_reply_successful_revalidation_promotes_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    memory = DummyMemoryService()
    client = DummyNotificationClient()
    pending = PendingCorrection(
        chat_id="chat-2",
        sender_id="42",
        sender_name="alice",
        original_message="Service:\nDate:",
        extracted_data={"Service": "", "Date": ""},
        validation_failures=["Service is empty or missing", "Date is empty or missing"],
    )
    tracker = DummyTracker(pending)
    reprocessed: list[str] = []

    monkeypatch.setattr(memory_module, "get_memory_service", lambda: memory)
    monkeypatch.setattr(correction_flow_module, "get_correction_tracker", lambda: tracker)
    monkeypatch.setattr(messaging_module, "get_notification_client", lambda: client)
    monkeypatch.setattr(
        correction_flow_module,
        "send_correction_request",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(
        brain_module,
        "llm_extract",
        lambda _msg, **_kwargs: {
            "Service": "Spa",
            "Date": "01/04/2026",
            "Time": "21:00",
            "Room": "Sahara",
            "Guest": "2",
        },
    )
    monkeypatch.setattr(correction_flow_module, "llm_extract", brain_module.llm_extract)
    monkeypatch.setattr(
        brain_module,
        "process_message",
        lambda message, sender_id=None, chat_id=None, message_id=None, sender_name=None: (
            reprocessed.append(message) or True
        ),
    )

    handled = brain_module.check_and_handle_correction_reply(
        "Service: Spa\nDate: 01/04/2026\nTime: 21:00\nRoom: Sahara",
        "42",
        "alice",
        "chat-2",
    )

    assert handled is True
    assert tracker.removed_chat_ids == ["chat-2"]
    assert memory.closed_tasks[0]["status"] == "resolved"
    assert memory.episodes and "Final service `Spa`" in memory.episodes[0]["content"]
    assert memory.learnings and "Service is empty or missing" in memory.learnings[0]["content"]
    assert reprocessed
    assert client.sent and "Thank you, your entry has been recorded." in client.sent[-1]["body"]


def test_check_and_handle_correction_reply_escalates_when_natural_service_reply_is_still_unclear(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    memory = DummyMemoryService()
    client = DummyNotificationClient()
    pending = PendingCorrection(
        chat_id="chat-3",
        sender_id="42",
        sender_name="alice",
        original_message="trans",
        extracted_data={"Service": "trans"},
        validation_failures=["Service 'trans' not found in price list"],
        service_suggestions=[("Transfer To Airport", 0.94), ("Transfer From Airport", 0.91)],
    )
    tracker = DummyTracker(pending)

    monkeypatch.setattr(memory_module, "get_memory_service", lambda: memory)
    monkeypatch.setattr(correction_flow_module, "get_correction_tracker", lambda: tracker)
    monkeypatch.setattr(messaging_module, "get_notification_client", lambda: client)
    monkeypatch.setattr(correction_flow_module, "send_escalation_to_all", lambda _message: True)
    monkeypatch.setattr(
        correction_flow_module,
        "interpret_service_reply",
        lambda **_kwargs: dialogue_module.ServiceReplyInterpretation(),
    )

    handled = brain_module.check_and_handle_correction_reply("something else idk", "42", "alice", "chat-3")

    assert handled is True
    assert tracker.removed_chat_ids == ["chat-3"]
    assert memory.closed_tasks[0]["status"] == "escalated"
    assert client.sent
    assert "I could not record your sale. Please contact *Omar* to add the details." in client.sent[0]["body"]


def test_check_and_handle_correction_reply_ignores_other_user_in_same_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pending = PendingCorrection(
        chat_id="group-1",
        sender_id="42",
        sender_name="alice",
        original_message="hamam",
        extracted_data={"Service": "hamam"},
        validation_failures=["Service 'hamam' not found in price list"],
        service_suggestions=[("One Hour Hammam", 0.94)],
    )
    tracker = DummyTracker(pending)

    monkeypatch.setattr(correction_flow_module, "get_correction_tracker", lambda: tracker)

    handled = brain_module.check_and_handle_correction_reply("I meant hammam", "77", "bob", "group-1")

    assert handled is False
    assert tracker.pending is pending


def test_process_expired_corrections_records_timeout_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    memory = DummyMemoryService()
    client = DummyNotificationClient()
    tracker = DummyTracker()
    tracker.expired = [
        PendingCorrection(
            chat_id="chat-expired",
            sender_id="42",
            sender_name="alice",
            original_message="Service:",
            extracted_data={"Service": ""},
            validation_failures=["Service is empty or missing"],
        )
    ]

    monkeypatch.setattr(memory_module, "get_memory_service", lambda: memory)
    monkeypatch.setattr(correction_flow_module, "get_notification_client", lambda: client)
    monkeypatch.setattr(correction_flow_module, "get_correction_tracker", lambda: tracker)
    monkeypatch.setattr(correction_flow_module, "send_escalation_to_all", lambda _message: True)

    escalated = brain_module.process_expired_corrections()

    assert escalated == 1
    assert client.sent and client.sent[0]["to"] == "chat-expired"
    assert memory.closed_tasks[0]["status"] == "expired"
    assert memory.episodes and "Correction expired" in memory.episodes[0]["content"]


def test_process_message_success_records_user_and_system_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    memory = DummyMemoryService()
    writes: list[list[Any]] = []

    class DummySalesAudit:
        def validate_service(
            self,
            service: str,
            threshold: float = 0.6,
            llm: Any = None,
        ) -> tuple[bool, str | None, list[tuple[str, float]]]:
            _ = threshold, llm
            return True, service, []

        def get_selling_price(self, _service: str, _quantity: float, llm: Any = None) -> float:
            return 200.0

        def calculate_cost(self, _service: str, _quantity: float, llm: Any = None) -> float:
            return 80.0

        def write_details_sheet(self, row: list[Any]) -> None:
            writes.append(row)

    monkeypatch.setattr(memory_module, "get_memory_service", lambda: memory)
    monkeypatch.setattr(brain_module, "_get_sales_audit", lambda: DummySalesAudit())
    monkeypatch.setattr(
        brain_module,
        "llm_extract",
        lambda _msg, **_kwargs: {
            "Service": "Spa",
            "Date": "01/04/2026",
            "Time": "21:00",
            "Room": "Sahara",
            "Guest": "2",
            "confidence": "high",
        },
    )
    monkeypatch.setattr(
        brain_module,
        "_resolve_staff_and_hotel",
        lambda *args, **kwargs: ("Alice", "RIAD Roxanne", False),
    )
    monkeypatch.setattr(dependencies_module, "get_sales_audit", lambda: DummySalesAudit())
    monkeypatch.setattr(
        extraction_module,
        "resolve_staff_and_hotel",
        lambda *args, **kwargs: ("Alice", "RIAD Roxanne", False),
    )
    monkeypatch.setattr(brain_module, "_send_commission_notification", lambda *args, **kwargs: None)
    monkeypatch.setattr(brain_module, "calculate_and_distribute_commissions", lambda **_kwargs: [])
    monkeypatch.setattr(brain_module, "generate_sale_id", lambda: "sale-123")
    monkeypatch.setattr(brain_module, "get_correction_tracker", lambda: DummyTracker())

    brain_module.process_message(
        "Service: Spa\nDate: 01/04/2026\nTime: 21:00\nRoom: Sahara\nRiad Roxanne",
        sender_id="42",
        chat_id="sales-chat",
        message_id="msg-1",
        sender_name="alice",
    )

    assert writes and writes[0][-1] == "sale-123"
    recorded = [event.text for event, _ in memory.recorded_events]
    assert any("Service: Spa" in text for text in recorded)
    assert any("Recorded sale `sale-123`" in text for text in recorded)
    assert memory.summary_refreshes
