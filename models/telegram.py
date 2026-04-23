from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

DEFAULT_BASE_URL = "https://api.telegram.org"

# Telegram chat types that represent a multi-participant conversation.
_GROUP_CHAT_TYPES = {"group", "supergroup", "channel"}


def _extract_text(message: dict[str, Any]) -> str | None:
    """Extract text or caption from a Telegram message object."""
    text = message.get("text")
    if isinstance(text, str) and text:
        return text
    caption = message.get("caption")
    if isinstance(caption, str) and caption:
        return caption
    return None


def _message_type(message: dict[str, Any]) -> str:
    """Infer a simple message type label from a Telegram message object."""
    if message.get("text"):
        return "text"
    for key in ("photo", "video", "document", "audio", "voice", "sticker", "animation"):
        if key in message:
            return key
    return "unknown"


@dataclass(frozen=True)
class TelegramConfig:
    token: str | None = None
    base_url: str = DEFAULT_BASE_URL
    timeout: float = 20.0
    parse_mode: str | None = None

    @classmethod
    def from_env(cls) -> TelegramConfig:
        token = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")
        base_url = os.getenv("TELEGRAM_API_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
        timeout_raw = os.getenv("TELEGRAM_TIMEOUT", "20")
        try:
            timeout = float(timeout_raw)
        except ValueError:
            timeout = 20.0
        parse_mode = os.getenv("TELEGRAM_PARSE_MODE") or None
        return cls(
            token=token,
            base_url=base_url,
            timeout=timeout,
            parse_mode=parse_mode,
        )


@dataclass(frozen=True)
class TelegramMessage:
    """Normalized Telegram message extracted from a webhook Update."""

    message_id: str
    message_type: str
    chat_id: str
    chat_type: str
    chat_title: str | None
    from_id: str | None
    from_name: str | None
    from_is_bot: bool
    timestamp: float
    text: str | None
    raw: dict[str, Any]

    @property
    def is_group(self) -> bool:
        return self.chat_type in _GROUP_CHAT_TYPES

    @property
    def is_private(self) -> bool:
        return not self.is_group

    @classmethod
    def from_update(cls, update: dict[str, Any]) -> TelegramMessage | None:
        """Create a TelegramMessage from a Telegram Update object.

        Returns None if the update has no message payload we can process
        (e.g., it's an edited_message or callback_query we don't handle).
        """
        msg = (
            update.get("message")
            or update.get("edited_message")
            or update.get("channel_post")
            or update.get("edited_channel_post")
        )
        if not isinstance(msg, dict):
            return None
        return cls.from_raw(msg)

    @classmethod
    def from_raw(cls, msg: dict[str, Any]) -> TelegramMessage:
        """Create a TelegramMessage from a raw Telegram message object."""
        chat = msg.get("chat") or {}
        sender = msg.get("from") or {}

        chat_id_raw = chat.get("id")
        chat_id = "" if chat_id_raw is None else str(chat_id_raw)
        chat_type = str(chat.get("type") or "")
        chat_title = chat.get("title") or chat.get("username")

        from_id_raw = sender.get("id")
        from_id = str(from_id_raw) if from_id_raw is not None else None
        from_name = sender.get("username") or sender.get("first_name")

        message_id_raw = msg.get("message_id")
        message_id = "" if message_id_raw is None else str(message_id_raw)

        return cls(
            message_id=message_id,
            message_type=_message_type(msg),
            chat_id=chat_id,
            chat_type=chat_type,
            chat_title=str(chat_title) if chat_title else None,
            from_id=from_id,
            from_name=str(from_name) if from_name else None,
            from_is_bot=bool(sender.get("is_bot")),
            timestamp=float(msg.get("date", 0)),
            text=_extract_text(msg),
            raw=msg,
        )
