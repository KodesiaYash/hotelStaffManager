from __future__ import annotations

from typing import Any

from controlplane.control.bot.salesbot.config import CORRECTION_TASK_TYPE
from controlplane.control.memory import get_memory_service
from controlplane.control.memory.types import MemoryEvent, RecallRequest


def conversation_id(chat_id: str | None, sender_id: str | None = None) -> str | None:
    if not chat_id:
        return None
    if sender_id:
        return f"telegram:{chat_id}:staff:{sender_id}"
    return f"telegram:{chat_id}"


def record_sales_event(
    *,
    role: str,
    text: str,
    chat_id: str | None,
    sender_id: str | None = None,
    sender_name: str | None = None,
    event_type: str = "message",
    metadata: dict[str, Any] | None = None,
) -> None:
    current_conversation_id = conversation_id(chat_id, sender_id)
    if not current_conversation_id or not text.strip():
        return
    get_memory_service().record_event(
        MemoryEvent(
            bot_name="salesbot",
            conversation_id=current_conversation_id,
            chat_id=chat_id,
            user_id=sender_id,
            sender_name=sender_name,
            role=role,  # type: ignore[arg-type]
            text=text.strip(),
            event_type=event_type,
            metadata=metadata or {},
        )
    )


def refresh_sales_summary(chat_id: str | None, sender_id: str | None = None) -> None:
    current_conversation_id = conversation_id(chat_id, sender_id)
    if not current_conversation_id or not chat_id:
        return
    get_memory_service().refresh_summary(
        bot_name="salesbot",
        conversation_id=current_conversation_id,
        chat_id=chat_id,
    )


def open_sales_correction_task(
    *,
    chat_id: str,
    sender_id: str | None,
    content: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    current_conversation_id = conversation_id(chat_id, sender_id)
    if not current_conversation_id:
        return
    get_memory_service().open_task(
        bot_name="salesbot",
        conversation_id=current_conversation_id,
        chat_id=chat_id,
        task_type=CORRECTION_TASK_TYPE,
        content=content,
        metadata=metadata,
    )
    refresh_sales_summary(chat_id, sender_id)


def close_sales_correction_task(
    chat_id: str | None,
    *,
    sender_id: str | None = None,
    status: str,
    resolution_note: str | None = None,
) -> None:
    current_conversation_id = conversation_id(chat_id, sender_id)
    if not current_conversation_id:
        return
    get_memory_service().close_task(
        bot_name="salesbot",
        conversation_id=current_conversation_id,
        task_type=CORRECTION_TASK_TYPE,
        status=status,
        resolution_note=resolution_note,
    )
    refresh_sales_summary(chat_id, sender_id)


def remember_sales_correction_outcome(
    *,
    chat_id: str | None,
    sender_id: str | None = None,
    title: str,
    content: str,
    source_event_id: str | None = None,
    fact_title: str | None = None,
    fact_content: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    current_conversation_id = conversation_id(chat_id, sender_id)
    if not current_conversation_id or not chat_id:
        return
    memory = get_memory_service()
    memory.remember_sales_correction_episode(
        conversation_id=current_conversation_id,
        chat_id=chat_id,
        title=title,
        content=content,
        metadata=metadata,
        source_event_id=source_event_id,
    )
    if fact_title and fact_content:
        memory.remember_sales_learning(
            title=fact_title,
            content=fact_content,
            metadata=metadata,
            source_event_id=source_event_id,
        )


def build_sales_memory_context(
    *,
    message: str,
    chat_id: str | None,
    sender_id: str | None,
    sender_name: str | None,
) -> str:
    current_conversation_id = conversation_id(chat_id, sender_id)
    if not current_conversation_id or not chat_id:
        return "No prior operating context available."
    recall = get_memory_service().recall(
        RecallRequest(
            bot_name="salesbot",
            conversation_id=current_conversation_id,
            chat_id=chat_id,
            query_text=message,
            user_id=sender_id,
            sender_name=sender_name,
        )
    )
    return recall.to_markdown() or "No prior operating context available."
