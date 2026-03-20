"""Unit tests for the WHAPI webhook Flask blueprint."""

from __future__ import annotations

import time

from flask import Flask

from communicationPlane.whatsappEngine.whapiInterface.webhook import create_whapi_blueprint
from models.chat_message import ChatMessage


def test_webhook_blueprint_returns_count() -> None:
    """Return a 200 response with the count of processed messages."""

    def handler(_payload: dict) -> list[ChatMessage]:
        now = time.time()
        return [
            ChatMessage(
                message_id="m1",
                source="whapi",
                chat_id="c1",
                sender_id="u1",
                sender_name="User",
                timestamp=now,
                message_type="text",
                text="one",
                is_group=False,
                raw={},
            ),
            ChatMessage(
                message_id="m2",
                source="whapi",
                chat_id="c2",
                sender_id="u2",
                sender_name="User",
                timestamp=now,
                message_type="text",
                text="two",
                is_group=True,
                raw={},
            ),
        ]

    app = Flask(__name__)
    app.register_blueprint(create_whapi_blueprint(handler), url_prefix="/whapi")

    with app.test_client() as client:
        response = client.post("/whapi/webhook", json={"messages": []})
        assert response.status_code == 200
        payload = response.get_json()
        assert payload["status"] == "ok"
        assert payload["messages"] == 2
        assert "request_id" in payload

        response = client.post("/whapi/webhook/messages", json={"messages": []})
        assert response.status_code == 200
        payload = response.get_json()
        assert payload["status"] == "ok"
        assert payload["messages"] == 2
        assert "request_id" in payload
