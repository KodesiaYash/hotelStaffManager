from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from models.whapi import WhapiMessage


@dataclass(frozen=True)
class ChatMessage:
    message_id: str
    source: str
    chat_id: str
    sender_id: str | None
    sender_name: str | None
    timestamp: float
    message_type: str | None
    text: str | None
    is_group: bool
    raw: dict[str, Any]

    @classmethod
    def from_whapi(cls, whapi_message: WhapiMessage, message_id: str) -> ChatMessage:
        return cls(
            message_id=message_id,
            source="whapi",
            chat_id=whapi_message.chat_id,
            sender_id=whapi_message.from_id,
            sender_name=whapi_message.from_name,
            timestamp=whapi_message.timestamp,
            message_type=whapi_message.message_type,
            text=whapi_message.text,
            is_group=whapi_message.is_group,
            raw=whapi_message.raw,
        )
