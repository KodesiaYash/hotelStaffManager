"""Unit tests for QueryBot memory-aware orchestration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from controlplane.control.bot.querybot import brain as brain_module
from controlplane.control.memory.types import RecallBundle


@dataclass
class DummyMemoryService:
    recall_bundle: RecallBundle = field(default_factory=RecallBundle)
    recall_requests: list[Any] = field(default_factory=list)
    recorded_events: list[Any] = field(default_factory=list)
    summary_refreshes: list[dict[str, Any]] = field(default_factory=list)

    def recall(self, request: Any) -> RecallBundle:
        self.recall_requests.append(request)
        return self.recall_bundle

    def record_event(self, event: Any, *, cache_working_memory: bool = True) -> None:
        self.recorded_events.append((event, cache_working_memory))

    def refresh_summary(self, **kwargs: Any) -> None:
        self.summary_refreshes.append(kwargs)


class DummyReplyClient:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    def send_text(self, *, to: str, body: str, **kwargs: Any) -> None:
        self.sent.append({"to": to, "body": body, **kwargs})


def test_process_message_uses_recall_and_records_both_turns(monkeypatch: pytest.MonkeyPatch) -> None:
    memory = DummyMemoryService(recall_bundle=RecallBundle())
    replies = DummyReplyClient()
    answer_calls: list[dict[str, Any]] = []

    monkeypatch.setattr(brain_module, "get_memory_service", lambda: memory)
    monkeypatch.setattr(brain_module, "_get_reply_client", lambda: replies)
    monkeypatch.setattr(
        brain_module,
        "answer_query",
        lambda question, *, memory_context="": answer_calls.append(
            {"question": question, "memory_context": memory_context}
        )
        or "summary answer",
    )

    brain_module.process_message("What changed?", "dm-1", "42", "msg-1", "alice")

    assert memory.recall_requests and memory.recall_requests[0].conversation_id == "telegram:dm-1"
    assert len(memory.recorded_events) == 2
    assert memory.recorded_events[0][0].role == "user"
    assert memory.recorded_events[1][0].role == "assistant"
    assert replies.sent[0]["body"] == "summary answer"
    assert memory.summary_refreshes
    assert answer_calls[0]["question"] == "What changed?"


def test_process_message_empty_input_skips_user_event_but_records_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    memory = DummyMemoryService()
    replies = DummyReplyClient()

    monkeypatch.setattr(brain_module, "get_memory_service", lambda: memory)
    monkeypatch.setattr(brain_module, "_get_reply_client", lambda: replies)

    brain_module.process_message("   ", "dm-2", "42", "msg-2", "alice")

    assert len(memory.recorded_events) == 1
    assert memory.recorded_events[0][0].role == "assistant"
    assert "empty message" in replies.sent[0]["body"].lower()


def test_answer_query_injects_memory_context_into_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    prompts: list[str] = []

    class DummyLLM:
        def generate(self, prompt: str) -> str:
            prompts.append(prompt)
            return "done"

    monkeypatch.setattr(
        brain_module,
        "build_spreadsheet_context",
        lambda: {"sales_audit_rows": [{"Service": "Spa"}], "sales_audit_row_count": 1},
    )
    monkeypatch.setattr(brain_module, "_get_llm_interface", lambda: DummyLLM())

    answer = brain_module.answer_query("How many?", memory_context="## Learned Facts\n- Use Sahara aliases.")

    assert answer == "done"
    assert "Use Sahara aliases." in prompts[0]
    assert '"Service": "Spa"' in prompts[0]
