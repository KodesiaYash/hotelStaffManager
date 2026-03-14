from __future__ import annotations

import os
import sys
import time
import uuid
from typing import Any

from flask import Flask, jsonify, request

# Add the project root to sys.path to import from controlplane and communicationPlane packages.
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)

from communicationPlane.whatsappEngine.engine import WhatsAppEngine  # noqa: E402
from communicationPlane.whatsappEngine.whapiInterface.webhook import (  # noqa: E402
    create_whapi_blueprint,
)
from controlplane.control.control_plane_interface import (  # noqa: E402
    ControlPlaneInterface,
)
from models.chat_message import ChatMessage  # noqa: E402

app = Flask(__name__)
control_plane = ControlPlaneInterface()
whatsapp_engine = WhatsAppEngine(control_plane)
app.register_blueprint(
    create_whapi_blueprint(whatsapp_engine.process_payload),
    url_prefix="/whapi",
)


def _is_debug_enabled() -> bool:
    return os.getenv("FLASK_DEBUG") == "1"


def _build_local_message(text: str) -> ChatMessage:
    return ChatMessage(
        message_id=f"local:{uuid.uuid4()}",
        source="http",
        chat_id="local",
        sender_id=None,
        sender_name=None,
        timestamp=time.time(),
        message_type="text",
        text=text,
        is_group=False,
        raw={"message": text},
    )


@app.route("/process", methods=["POST"])
def process() -> tuple[Any, int]:
    data = request.get_json(silent=True)
    if not data or "message" not in data:
        return jsonify({"error": "Missing message"}), 400

    message = data["message"]
    try:
        chat_message = _build_local_message(str(message))
        control_plane.process(chat_message)
        return jsonify({"status": "success", "message_id": chat_message.message_id}), 200
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


if __name__ == "__main__":
    app.run(debug=_is_debug_enabled())
