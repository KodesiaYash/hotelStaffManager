from __future__ import annotations

import logging
import os
from collections.abc import Callable
from typing import Protocol

from dotenv import load_dotenv

from models.chat_message import ChatMessage

logger = logging.getLogger(__name__)

SalesBotHandler = Callable[[str, str | None], None]


class QueryBotHandler(Protocol):
    def __call__(self, message: str, chat_id: str) -> None: ...


PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _load_env_files() -> None:
    load_dotenv()
    env_path = os.path.join(PROJECT_ROOT, "env")
    if os.path.exists(env_path):
        load_dotenv(dotenv_path=env_path, override=False)


_load_env_files()


class ControlPlaneInterface:
    def __init__(
        self,
        sales_bot_handler: SalesBotHandler | None = None,
        query_bot_handler: QueryBotHandler | None = None,
    ) -> None:
        if sales_bot_handler is None:
            from controlplane.control.bot.salesbot.brain import process_message as default_handler

            sales_bot_handler = default_handler
        if query_bot_handler is None:
            from controlplane.control.bot.querybot.brain import process_message as default_dm_handler

            query_bot_handler = default_dm_handler
        if sales_bot_handler is None:
            raise RuntimeError("Sales bot handler is not configured")
        if query_bot_handler is None:
            raise RuntimeError("Query bot handler is not configured")
        self._sales_bot_handler: SalesBotHandler = sales_bot_handler
        self._query_bot_handler: QueryBotHandler = query_bot_handler
        self._sales_group_id = (os.getenv("SALES_GROUP_ID") or "").strip()
        self._allowed_chat_ids = {
            item.strip() for item in os.getenv("QUERYBOT_ALLOWED_CHAT_IDS", "").split(",") if item.strip()
        }

    def process(self, message: ChatMessage) -> None:
        logger.info(
            "ControlPlane received message id=%s source=%s chat_id=%s sender_id=%s text_len=%d",
            message.message_id,
            message.source,
            message.chat_id,
            message.sender_id,
            len(message.text or ""),
        )
        if message.source == "whapi" and message.is_group:
            # For group messages, check against sales group ID
            if self._sales_group_id and message.chat_id != self._sales_group_id:
                logger.debug("Ignoring message outside sales group (chat_id=%s)", message.chat_id)
                return
        elif (
            message.source == "whapi"
            and not message.is_group
            and self._allowed_chat_ids
            and message.chat_id not in self._allowed_chat_ids
        ):
            # For DMs, check against allowed chat IDs (if configured)
            logger.debug("Ignoring DM outside allowed chats (chat_id=%s)", message.chat_id)
            return
        if not message.text:
            logger.debug("Ignoring message %s with no text", message.message_id)
            return

        if message.source == "whapi":
            if message.is_group:
                logger.info("Routing to SalesBot chat_id=%s sender_id=%s", message.chat_id, message.sender_id)
                try:
                    self._sales_bot_handler(message.text, message.sender_id)
                except Exception as exc:
                    logger.error(
                        "SalesBot handler failed error=%s chat_id=%s sender_id=%s message_preview=%s",
                        str(exc)[:100],
                        message.chat_id,
                        message.sender_id,
                        (message.text or "")[:200],
                        exc_info=True,
                    )
            else:
                logger.info("Routing to QueryBot chat_id=%s", message.chat_id)
                try:
                    self._query_bot_handler(message.text, message.chat_id)
                except Exception as exc:
                    logger.error(
                        "QueryBot handler failed error=%s chat_id=%s message_preview=%s",
                        str(exc)[:100],
                        message.chat_id,
                        (message.text or "")[:200],
                        exc_info=True,
                    )
