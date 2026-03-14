"""Unit tests for WhatsAppEngine payload processing and dedup IDs."""

from __future__ import annotations

from typing import Any

from communicationPlane.whatsappEngine.engine import WhatsAppEngine, build_dedup_id
from models.chat_message import ChatMessage
from models.deduplication import InMemoryDeduplicator
from models.whapi import WhapiMessage


class DummyControlPlane:
    def __init__(self) -> None:
        self.messages: list[ChatMessage] = []

    def process(self, message: ChatMessage) -> None:
        self.messages.append(message)


def _raw_message(
    msg_id: str | None,
    *,
    text: str = "hello",
    from_me: bool = False,
    chat_id: str = "120@c.us",
    from_id: str | None = "999",
    timestamp: int = 1,
    message_type: str = "text",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "type": message_type,
        "chat_id": chat_id,
        "from": from_id,
        "from_name": "Tester",
        "timestamp": timestamp,
        "from_me": from_me,
        "text": {"body": text},
    }
    if msg_id is not None:
        payload["id"] = msg_id
    return payload


def test_build_dedup_id_prefers_message_id() -> None:
    """Use the WHAPI message id directly when available."""
    message = WhapiMessage(
        message_id="abc123",
        message_type="text",
        chat_id="chat",
        from_id="user",
        from_name="User",
        timestamp=123.0,
        text="hi",
        raw={},
    )
    assert build_dedup_id(message) == "abc123"


def test_build_dedup_id_hashes_when_missing_id() -> None:
    """Fall back to a deterministic hash when no message id is present."""
    message = WhapiMessage(
        message_id="",
        message_type="text",
        chat_id="chat",
        from_id=None,
        from_name=None,
        timestamp=123.0,
        text="hi",
        raw={},
    )
    dedup_id = build_dedup_id(message)
    assert dedup_id.startswith("whapi:chat:unknown:123:")
    assert dedup_id == build_dedup_id(message)


def test_engine_ignores_from_me_and_deduplicates_by_id() -> None:
    """Ignore self-sent messages and skip duplicate ids."""
    control = DummyControlPlane()
    engine = WhatsAppEngine(control, deduplicator=InMemoryDeduplicator(), ignore_from_me=True)
    payload = {
        "messages": [
            _raw_message("id-1", text="skip", from_me=True),
            _raw_message("id-1", text="hello"),
            _raw_message("id-1", text="hello"),
            _raw_message("id-2", text="world"),
        ]
    }

    processed = engine.process_payload(payload)
    assert [msg.text for msg in processed] == ["hello", "world"]
    assert [msg.text for msg in control.messages] == ["hello", "world"]


def test_engine_deduplicates_when_id_missing() -> None:
    """Deduplicate using the hashed id when message id is missing."""
    control = DummyControlPlane()
    engine = WhatsAppEngine(control, deduplicator=InMemoryDeduplicator(), ignore_from_me=True)
    payload = {
        "messages": [
            _raw_message(None, text="same", timestamp=10),
            _raw_message(None, text="same", timestamp=10),
            _raw_message(None, text="different", timestamp=11),
        ]
    }

    processed = engine.process_payload(payload)
    assert len(processed) == 2
    assert processed[0].message_id.startswith("whapi:")
    assert [msg.text for msg in processed] == ["same", "different"]


def test_engine_can_process_from_me_when_configured() -> None:
    """Allow self-sent messages when ignore_from_me is disabled."""
    control = DummyControlPlane()
    engine = WhatsAppEngine(control, deduplicator=InMemoryDeduplicator(), ignore_from_me=False)
    payload = {"messages": [_raw_message("id-1", text="self", from_me=True)]}

    processed = engine.process_payload(payload)
    assert len(processed) == 1
    assert processed[0].text == "self"
    assert control.messages[0].text == "self"
