from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from models.telegram import TelegramMessage


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
    def from_telegram(cls, telegram_message: TelegramMessage, message_id: str) -> ChatMessage:
        return cls(
            message_id=message_id,
            source="telegram",
            chat_id=telegram_message.chat_id,
            sender_id=telegram_message.from_id,
            sender_name=telegram_message.from_name,
            timestamp=telegram_message.timestamp,
            message_type=telegram_message.message_type,
            text=telegram_message.text,
            is_group=telegram_message.is_group,
            raw=telegram_message.raw,
        )
