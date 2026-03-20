from __future__ import annotations

import logging
import os
from collections.abc import Callable

from dotenv import load_dotenv

from models.chat_message import ChatMessage

logger = logging.getLogger(__name__)

SalesBotHandler = Callable[[str, str | None], None]

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _load_env_files() -> None:
    load_dotenv()
    env_path = os.path.join(PROJECT_ROOT, "env")
    if os.path.exists(env_path):
        load_dotenv(dotenv_path=env_path, override=False)


_load_env_files()


class ControlPlaneInterface:
    def __init__(self, sales_bot_handler: SalesBotHandler | None = None) -> None:
        if sales_bot_handler is None:
            from controlplane.control.bot.salesbot.brain import process_message as default_handler

            sales_bot_handler = default_handler
        if sales_bot_handler is None:
            raise RuntimeError("Sales bot handler is not configured")
        self._sales_bot_handler: SalesBotHandler = sales_bot_handler
        self._sales_group_id = (os.getenv("SALES_GROUP_ID") or "").strip()

    def process(self, message: ChatMessage) -> None:
        logger.info(
            "ControlPlane received message id=%s source=%s chat_id=%s",
            message.message_id,
            message.source,
            message.chat_id,
        )
        if message.source == "whapi" and self._sales_group_id and message.chat_id != self._sales_group_id:
            logger.info("Ignoring message outside sales group (chat_id=%s)", message.chat_id)
            return
        if not message.text:
            logger.info("Ignoring message %s with no text", message.message_id)
            return
        logger.info("Routing to SalesBot")
        self._sales_bot_handler(message.text, message.sender_id)
