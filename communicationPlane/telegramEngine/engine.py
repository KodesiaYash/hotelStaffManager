from __future__ import annotations

import hashlib
import logging
from typing import Any, Protocol

from models.chat_message import ChatMessage
from models.deduplication import Deduplicator, InMemoryDeduplicator
from models.telegram import TelegramMessage
from shared.logging_context import LogContext

logger = logging.getLogger(__name__)


def build_dedup_id(message: TelegramMessage) -> str:
    """Deterministic dedup key for a Telegram message.

    Prefers ``telegram:{chat_id}:{message_id}`` when the message has an id,
    otherwise falls back to a content hash.
    """
    if message.message_id:
        return f"telegram:{message.chat_id}:{message.message_id}"
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
    return f"telegram:{message.chat_id}:{message.from_id or 'unknown'}:{int(message.timestamp)}:{digest}"


def _iter_messages(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Yield raw message dicts from a Telegram webhook payload.

    Telegram delivers a single Update per webhook POST, but we also
    support batched payloads (``updates`` or legacy ``messages`` list)
    for integration tests and backfills.
    """
    if isinstance(payload.get("updates"), list):
        return [u for u in payload["updates"] if isinstance(u, dict)]
    if isinstance(payload.get("messages"), list):
        # Treat each entry as a raw Telegram message object already.
        return [{"message": m} for m in payload["messages"] if isinstance(m, dict)]
    # Single Update object (most common case)
    return [payload] if payload else []


class ChatMessageHandler(Protocol):
    def process(self, message: ChatMessage) -> None: ...


class TelegramEngine:
    """Parses Telegram webhook payloads and dispatches ChatMessages."""

    def __init__(
        self,
        control_plane: ChatMessageHandler,
        *,
        deduplicator: Deduplicator | None = None,
        ignore_from_me: bool = True,
        bot_user_id: str | None = None,
    ) -> None:
        self.control_plane = control_plane
        self.deduplicator = deduplicator or InMemoryDeduplicator()
        self.ignore_from_me = ignore_from_me
        self.bot_user_id = bot_user_id

    def _is_from_self(self, msg: TelegramMessage) -> bool:
        if not self.ignore_from_me:
            return False
        if msg.from_is_bot and self.bot_user_id and msg.from_id == self.bot_user_id:
            return True
        # If we don't know our own bot id, default to ignoring all bot-originated
        # messages (safe fallback; bots typically don't receive their own messages).
        return bool(msg.from_is_bot and not self.bot_user_id)

    def process_payload(self, payload: dict[str, Any]) -> list[ChatMessage]:
        raw_updates = _iter_messages(payload)
        logger.debug("Telegram payload received with %d update(s)", len(raw_updates))
        processed: list[ChatMessage] = []
        for raw in raw_updates:
            telegram_message = TelegramMessage.from_update(raw)
            if telegram_message is None:
                logger.debug("Skipping update with no message payload")
                continue

            dedup_id = build_dedup_id(telegram_message)
            with LogContext(
                message_id=dedup_id,
                chat_id=telegram_message.chat_id,
                chat_name=telegram_message.chat_title,
                sender_id=telegram_message.from_id,
                sender_name=telegram_message.from_name,
                source="app",
                transport="telegram",
            ):
                if self._is_from_self(telegram_message):
                    logger.info("Skipping message from self")
                    continue
                if self.deduplicator and self.deduplicator.is_duplicate(dedup_id):
                    logger.info("Duplicate message ignored")
                    continue
                chat_message = ChatMessage.from_telegram(telegram_message, dedup_id)
                logger.info(
                    "Dispatching message type=%s text_len=%d",
                    chat_message.message_type,
                    len(chat_message.text or ""),
                )
                logger.debug("Message payload: %s", chat_message.raw)
                self.control_plane.process(chat_message)
                processed.append(chat_message)
        logger.debug("Processed %d message(s) from payload", len(processed))
        return processed
