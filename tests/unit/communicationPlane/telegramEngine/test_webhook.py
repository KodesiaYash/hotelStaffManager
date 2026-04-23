"""Unit tests for the Telegram webhook Flask blueprint."""

from __future__ import annotations

from typing import Any

from flask import Flask

from communicationPlane.telegramEngine.telegramInterface.webhook import create_telegram_blueprint
from models.chat_message import ChatMessage


def test_blueprint_dispatches_to_handler() -> None:
    """The blueprint should forward payloads to the handler and echo counts."""

    def handler(_payload: dict[str, Any]) -> list[ChatMessage]:
        return [
            ChatMessage(
                message_id="m1",
                source="telegram",
                chat_id="c1",
                sender_id="u1",
                sender_name="User",
                timestamp=0.0,
                message_type="text",
                text="hi",
                is_group=False,
                raw={},
            ),
            ChatMessage(
                message_id="m2",
                source="telegram",
                chat_id="c2",
                sender_id="u2",
                sender_name="User",
                timestamp=0.0,
                message_type="text",
                text="bye",
                is_group=False,
                raw={},
            ),
        ]

    app = Flask(__name__)
    app.register_blueprint(create_telegram_blueprint(handler), url_prefix="/telegram")

    with app.test_client() as client:
        response = client.post("/telegram/webhook", json={"updates": []})
        assert response.status_code == 200
        payload = response.get_json()
        assert payload["status"] == "ok"
        assert payload["messages"] == 2
        assert "request_id" in payload

        response = client.post("/telegram/webhook/messages", json={"updates": []})
        assert response.status_code == 200
        payload = response.get_json()
        assert payload["status"] == "ok"
