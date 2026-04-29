from __future__ import annotations

from controlplane.control.control_plane_interface import ControlPlaneInterface
from models.chat_message import ChatMessage


def _build_message(
    *,
    chat_id: str,
    is_group: bool,
    text: str = "hello",
    sender_id: str = "42",
    sender_name: str = "alice",
) -> ChatMessage:
    return ChatMessage(
        message_id=f"telegram:{chat_id}:1",
        source="telegram",
        chat_id=chat_id,
        sender_id=sender_id,
        sender_name=sender_name,
        timestamp=0.0,
        message_type="text",
        text=text,
        is_group=is_group,
        raw={},
    )


def test_test_id_bypasses_group_routing_filter(monkeypatch) -> None:
    sales_calls: list[tuple[str, str | None, str | None, str | None, str | None]] = []

    monkeypatch.setenv("SALES_GROUP_ID", "-1003804308922")
    monkeypatch.setenv("TEST_ID", "-1003946939160")
    monkeypatch.delenv("QUERYBOT_ALLOWED_CHAT_IDS", raising=False)

    control_plane = ControlPlaneInterface(
        sales_bot_handler=lambda *args: sales_calls.append(args),
        query_bot_handler=lambda *args: None,
    )

    control_plane.process(_build_message(chat_id="-1003946939160", is_group=True, text="Service: dinner"))

    assert len(sales_calls) == 1
    assert sales_calls[0][0] == "Service: dinner"


def test_test_id_bypasses_dm_allowlist(monkeypatch) -> None:
    query_calls: list[tuple[str, str, str | None, str | None, str | None]] = []

    monkeypatch.delenv("SALES_GROUP_ID", raising=False)
    monkeypatch.setenv("TEST_ID", "test-dm-chat")
    monkeypatch.setenv("QUERYBOT_ALLOWED_CHAT_IDS", "some-other-chat")

    control_plane = ControlPlaneInterface(
        sales_bot_handler=lambda *args: None,
        query_bot_handler=lambda *args: query_calls.append(args),
    )

    control_plane.process(_build_message(chat_id="test-dm-chat", is_group=False, text="What changed?"))

    assert len(query_calls) == 1
    assert query_calls[0][0] == "What changed?"


def test_non_test_group_still_respects_sales_group_filter(monkeypatch) -> None:
    sales_calls: list[tuple[str, str | None, str | None, str | None, str | None]] = []

    monkeypatch.setenv("SALES_GROUP_ID", "-1003804308922")
    monkeypatch.setenv("TEST_ID", "-1003946939160")
    monkeypatch.delenv("QUERYBOT_ALLOWED_CHAT_IDS", raising=False)

    control_plane = ControlPlaneInterface(
        sales_bot_handler=lambda *args: sales_calls.append(args),
        query_bot_handler=lambda *args: None,
    )

    control_plane.process(_build_message(chat_id="-1001111111111", is_group=True, text="Service: spa"))

    assert sales_calls == []
