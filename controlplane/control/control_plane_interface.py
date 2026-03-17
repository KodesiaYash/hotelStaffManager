from __future__ import annotations

import os
from collections.abc import Callable

from dotenv import load_dotenv

from models.chat_message import ChatMessage

SalesBotHandler = Callable[[str], None]
DutchTutorHandler = Callable[[str], None]

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
        dutch_tutor_handler: DutchTutorHandler | None = None,
    ) -> None:
        if sales_bot_handler is None:
            from controlplane.control.bot.salesbot.brain import process_message as default_handler

            sales_bot_handler = default_handler
        if sales_bot_handler is None:
            raise RuntimeError("Sales bot handler is not configured")
        self._sales_bot_handler: SalesBotHandler = sales_bot_handler
        self._dutch_tutor_handler: DutchTutorHandler | None = dutch_tutor_handler
        self._sales_group_id = (os.getenv("SALES_GROUP_ID") or "").strip()
        self._dutch_tutor_chat_id = (os.getenv("DUTCH_TUTOR_CHAT_ID") or "").strip()

    def process(self, message: ChatMessage) -> None:
        if not message.text:
            return
        if (
            message.source == "whapi"
            and self._dutch_tutor_chat_id
            and message.chat_id == self._dutch_tutor_chat_id
            and self._dutch_tutor_handler
        ):
            self._dutch_tutor_handler(message.text)
            return
        if message.source == "whapi" and self._sales_group_id and message.chat_id != self._sales_group_id:
            return
        self._sales_bot_handler(message.text)
