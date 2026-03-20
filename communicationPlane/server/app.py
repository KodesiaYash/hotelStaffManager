from __future__ import annotations

import logging
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
from shared.logging_context import LogContext, init_logging  # noqa: E402

init_logging()
logger = logging.getLogger(__name__)

app = Flask(__name__)
control_plane = ControlPlaneInterface()
whatsapp_engine = WhatsAppEngine(control_plane)
app.register_blueprint(
    create_whapi_blueprint(whatsapp_engine.process_payload),
    url_prefix="/whapi",
)


def _is_debug_enabled() -> bool:
    return os.getenv("FLASK_DEBUG") == "1"


def _server_host() -> str:
    return os.getenv("SERVER_HOST", "127.0.0.1")


def _server_port() -> int:
    port_raw = os.getenv("SERVER_PORT") or os.getenv("PORT") or "5000"
    return int(port_raw)


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
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    logger.info("HTTP /process called from %s", request.remote_addr)
    logger.info("HTTP /process payload: %s", data)
    if not data or "message" not in data:
        logger.warning("HTTP /process missing message field")
        return jsonify({"error": "Missing message"}), 400

    message = data["message"]
    try:
        chat_message = _build_local_message(str(message))
        with LogContext(
            request_id=request_id,
            message_id=chat_message.message_id,
            chat_id=chat_message.chat_id,
            chat_name="local",
            sender_id=chat_message.sender_id,
            source=chat_message.source,
        ):
            logger.info("Dispatching local message")
            control_plane.process(chat_message)
        return jsonify({"status": "success", "message_id": chat_message.message_id}), 200
    except Exception as exc:
        logger.error("HTTP /process failed: %s", exc, exc_info=True)
        return jsonify({"error": str(exc)}), 500


@app.route("/health", methods=["GET"])
def health() -> tuple[Any, int]:
    logger.info("HTTP /health called from %s", request.remote_addr)
    return jsonify({"status": "ok"}), 200


if __name__ == "__main__":
    app.run(host=_server_host(), port=_server_port(), debug=_is_debug_enabled())
