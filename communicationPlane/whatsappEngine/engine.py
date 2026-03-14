from __future__ import annotations

import hashlib
from typing import Any, Protocol

from models.chat_message import ChatMessage
from models.deduplication import Deduplicator, InMemoryDeduplicator
from models.whapi import WhapiMessage


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
        processed: list[ChatMessage] = []
        for raw in raw_messages:
            if self.ignore_from_me and raw.get("from_me") is True:
                continue
            whapi_message = WhapiMessage.from_raw(raw)
            dedup_id = build_dedup_id(whapi_message)
            if self.deduplicator and self.deduplicator.is_duplicate(dedup_id):
                continue
            chat_message = ChatMessage.from_whapi(whapi_message, dedup_id)
            self.control_plane.process(chat_message)
            processed.append(chat_message)
        return processed
