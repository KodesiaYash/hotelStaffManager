"""Integration test for WHAPI webhook -> engine -> control plane wiring."""

from __future__ import annotations

from typing import Any

import pytest
from flask import Flask

from communicationPlane.whatsappEngine.engine import WhatsAppEngine
from communicationPlane.whatsappEngine.whapiInterface.webhook import create_whapi_blueprint
from models.chat_message import ChatMessage


class RecordingControlPlane:
    """Test control plane that records processed messages."""

    def __init__(self) -> None:
        self.messages: list[ChatMessage] = []

    def process(self, message: ChatMessage) -> None:
        self.messages.append(message)


def _payload(message_id: str = "msg-1") -> dict[str, Any]:
    return {
        "messages": [
            {
                "id": message_id,
                "type": "text",
                "chat_id": "1203634@g.us",
                "from": "999@c.us",
                "from_name": "Tester",
                "timestamp": 1700000000,
                "from_me": False,
                "text": {"body": "hello"},
            }
        ]
    }


@pytest.mark.integration
def test_webhook_messages_endpoint_ingests_payload() -> None:
    """Posting to /whapi/webhook/messages is processed by the engine."""
    control = RecordingControlPlane()
    engine = WhatsAppEngine(control)

    app = Flask(__name__)
    app.register_blueprint(create_whapi_blueprint(engine.process_payload), url_prefix="/whapi")

    with app.test_client() as client:
        response = client.post("/whapi/webhook/messages", json=_payload())
        assert response.status_code == 200
        payload = response.get_json()
        assert payload["status"] == "ok"
        assert payload["messages"] == 1
        assert "request_id" in payload

    assert len(control.messages) == 1
    assert control.messages[0].text == "hello"
    assert control.messages[0].chat_id == "1203634@g.us"
