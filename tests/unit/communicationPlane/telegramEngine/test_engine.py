"""Unit tests for TelegramEngine payload processing and dedup IDs."""

from __future__ import annotations

from typing import Any

from communicationPlane.telegramEngine.engine import TelegramEngine, build_dedup_id
from models.chat_message import ChatMessage
from models.deduplication import InMemoryDeduplicator
from models.telegram import TelegramMessage


class DummyControlPlane:
    def __init__(self) -> None:
        self.messages: list[ChatMessage] = []

    def process(self, message: ChatMessage) -> None:
        self.messages.append(message)


def _raw_update(
    msg_id: int | None,
    *,
    text: str = "hello",
    from_is_bot: bool = False,
    chat_id: int = -100456,
    chat_type: str = "group",
    chat_title: str | None = "Staff",
    from_id: int | None = 999,
    timestamp: int = 1,
) -> dict[str, Any]:
    message: dict[str, Any] = {
        "chat": {"id": chat_id, "type": chat_type},
        "from": {"id": from_id, "first_name": "Tester", "is_bot": from_is_bot},
        "date": timestamp,
        "text": text,
    }
    if chat_title is not None:
        message["chat"]["title"] = chat_title
    if msg_id is not None:
        message["message_id"] = msg_id
    return {"update_id": msg_id or 0, "message": message}


def test_build_dedup_id_prefers_message_id() -> None:
    """Use the Telegram message id directly when available."""
    message = TelegramMessage(
        message_id="abc123",
        message_type="text",
        chat_id="-100",
        chat_type="group",
        chat_title="Staff",
        from_id="user",
        from_name="User",
        from_is_bot=False,
        timestamp=123.0,
        text="hi",
        raw={},
    )
    assert build_dedup_id(message) == "telegram:-100:abc123"


def test_build_dedup_id_hashes_when_missing_id() -> None:
    """Fall back to a deterministic hash when no message id is present."""
    message = TelegramMessage(
        message_id="",
        message_type="text",
        chat_id="-100",
        chat_type="group",
        chat_title=None,
        from_id=None,
        from_name=None,
        from_is_bot=False,
        timestamp=123.0,
        text="hi",
        raw={},
    )
    dedup_id = build_dedup_id(message)
    assert dedup_id.startswith("telegram:-100:unknown:123:")
    assert dedup_id == build_dedup_id(message)


def test_engine_ignores_bot_and_deduplicates_by_id() -> None:
    """Ignore bot-authored messages and skip duplicate ids."""
    control = DummyControlPlane()
    engine = TelegramEngine(control, deduplicator=InMemoryDeduplicator(), ignore_from_me=True)
    payload = {
        "updates": [
            _raw_update(1, text="skip", from_is_bot=True),
            _raw_update(2, text="hello"),
            _raw_update(2, text="hello"),
            _raw_update(3, text="world"),
        ]
    }

    processed = engine.process_payload(payload)
    assert [msg.text for msg in processed] == ["hello", "world"]
    assert [msg.text for msg in control.messages] == ["hello", "world"]


def test_engine_deduplicates_when_id_missing() -> None:
    """Deduplicate using the hashed id when message id is missing."""
    control = DummyControlPlane()
    engine = TelegramEngine(control, deduplicator=InMemoryDeduplicator(), ignore_from_me=True)
    payload = {
        "updates": [
            _raw_update(None, text="same", timestamp=10),
            _raw_update(None, text="same", timestamp=10),
            _raw_update(None, text="different", timestamp=11),
        ]
    }

    processed = engine.process_payload(payload)
    assert len(processed) == 2
    assert processed[0].message_id.startswith("telegram:")
    assert [msg.text for msg in processed] == ["same", "different"]


def test_engine_can_process_from_bot_when_configured() -> None:
    """Allow bot-authored messages when ignore_from_me is disabled."""
    control = DummyControlPlane()
    engine = TelegramEngine(control, deduplicator=InMemoryDeduplicator(), ignore_from_me=False)
    payload = {"updates": [_raw_update(1, text="self", from_is_bot=True)]}

    processed = engine.process_payload(payload)
    assert len(processed) == 1
    assert processed[0].text == "self"
    assert control.messages[0].text == "self"


def test_engine_accepts_single_update_payload() -> None:
    """Telegram webhooks deliver a single Update per POST; engine must handle that."""
    control = DummyControlPlane()
    engine = TelegramEngine(control)
    payload = _raw_update(42, text="single")
    processed = engine.process_payload(payload)
    assert len(processed) == 1
    assert processed[0].text == "single"
    assert processed[0].is_group is True


def test_engine_marks_private_chats_as_not_group() -> None:
    """Private chat updates should yield is_group=False ChatMessages."""
    control = DummyControlPlane()
    engine = TelegramEngine(control)
    payload = _raw_update(1, chat_id=555, chat_type="private", chat_title=None, text="dm")
    processed = engine.process_payload(payload)
    assert len(processed) == 1
    assert processed[0].is_group is False
    assert processed[0].chat_id == "555"
