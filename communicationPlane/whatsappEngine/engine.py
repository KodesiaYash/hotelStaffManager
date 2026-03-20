from __future__ import annotations

import hashlib
import logging
from typing import Any, Protocol

from models.chat_message import ChatMessage
from models.deduplication import Deduplicator, InMemoryDeduplicator
from models.whapi import WhapiMessage
from shared.logging_context import LogContext

logger = logging.getLogger(__name__)


def build_dedup_id(message: WhapiMessage) -> str:
    if message.message_id:
        return message.message_id
    base = "|".join(
        [
            message.chat_id,
            message.from_id or "",
            str(int(message.timestamp)) if message.timestamp else "0",
            message.message_type or "",
            message.text or "",
        ]
    )
    digest = hashlib.sha256(base.encode("utf-8")).hexdigest()[:16]
    return f"whapi:{message.chat_id}:{message.from_id or 'unknown'}:{int(message.timestamp)}:{digest}"


def _extract_chat_name(raw: dict[str, Any]) -> str | None:
    for key in ("chat_name", "chat_title"):
        value = raw.get(key)
        if value:
            return str(value)
    chat = raw.get("chat") or {}
    for key in ("name", "title", "subject"):
        value = chat.get(key)
        if value:
            return str(value)
    return None


class ChatMessageHandler(Protocol):
    def process(self, message: ChatMessage) -> None: ...


class WhatsAppEngine:
    def __init__(
        self,
        control_plane: ChatMessageHandler,
        *,
        deduplicator: Deduplicator | None = None,
        ignore_from_me: bool = True,
    ) -> None:
        self.control_plane = control_plane
        self.deduplicator = deduplicator or InMemoryDeduplicator()
        self.ignore_from_me = ignore_from_me

    def process_payload(self, payload: dict[str, Any]) -> list[ChatMessage]:
        raw_messages = payload.get("messages") or []
        logger.info("WHAPI payload received with %d message(s)", len(raw_messages))
        processed: list[ChatMessage] = []
        for raw in raw_messages:
            whapi_message = WhapiMessage.from_raw(raw)
            chat_name = _extract_chat_name(raw)
            dedup_id = build_dedup_id(whapi_message)
            with LogContext(
                message_id=dedup_id,
                chat_id=whapi_message.chat_id,
                chat_name=chat_name,
                sender_id=whapi_message.from_id,
                source="whapi",
            ):
                if self.ignore_from_me and raw.get("from_me") is True:
                    logger.info("Skipping message from self")
                    continue
                if self.deduplicator and self.deduplicator.is_duplicate(dedup_id):
                    logger.info("Duplicate message ignored")
                    continue
                chat_message = ChatMessage.from_whapi(whapi_message, dedup_id)
                logger.info(
                    "Dispatching message type=%s text_len=%s",
                    chat_message.message_type,
                    len(chat_message.text or ""),
                )
                logger.info("Message payload: %s", chat_message.raw)
                self.control_plane.process(chat_message)
                processed.append(chat_message)
        logger.info("Processed %d message(s) from payload", len(processed))
        return processed
