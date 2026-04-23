"""Integration test for Telegram webhook -> engine -> control plane wiring."""

from __future__ import annotations

from typing import Any

import pytest
from flask import Flask

from communicationPlane.telegramEngine.engine import TelegramEngine
from communicationPlane.telegramEngine.telegramInterface.webhook import create_telegram_blueprint
from models.chat_message import ChatMessage


class RecordingControlPlane:
    """Test control plane that records processed messages."""

    def __init__(self) -> None:
        self.messages: list[ChatMessage] = []

    def process(self, message: ChatMessage) -> None:
        self.messages.append(message)


def _payload(message_id: int = 1) -> dict[str, Any]:
    return {
        "update_id": message_id,
        "message": {
            "message_id": message_id,
            "chat": {"id": -100456, "type": "group", "title": "Staff"},
            "from": {"id": 999, "first_name": "Tester", "is_bot": False},
            "date": 1700000000,
            "text": "hello",
        },
    }


@pytest.mark.integration
def test_webhook_messages_endpoint_ingests_payload() -> None:
    """Posting to /telegram/webhook/messages is processed by the engine."""
    control = RecordingControlPlane()
    engine = TelegramEngine(control)

    app = Flask(__name__)
    app.register_blueprint(create_telegram_blueprint(engine.process_payload), url_prefix="/telegram")

    with app.test_client() as client:
        response = client.post("/telegram/webhook/messages", json=_payload())
        assert response.status_code == 200
        payload = response.get_json()
        assert payload["status"] == "ok"
        assert payload["messages"] == 1
        assert "request_id" in payload

    assert len(control.messages) == 1
    assert control.messages[0].text == "hello"
    assert control.messages[0].chat_id == "-100456"
    assert control.messages[0].is_group is True
