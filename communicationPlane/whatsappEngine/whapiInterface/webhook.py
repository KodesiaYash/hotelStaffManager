from __future__ import annotations

from collections.abc import Callable
from typing import Any

from flask import Blueprint, jsonify, request

from models.chat_message import ChatMessage

PayloadHandler = Callable[[dict[str, Any]], list[ChatMessage]]


def create_whapi_blueprint(handler: PayloadHandler) -> Blueprint:
    blueprint = Blueprint("whapi_webhook", __name__)

    def _handle_webhook() -> tuple[Any, int]:
        payload = request.get_json(silent=True) or {}
        messages = handler(payload)
        return jsonify({"status": "ok", "messages": len(messages)}), 200

    @blueprint.route("/webhook", methods=["POST"])
    def webhook() -> tuple[Any, int]:
        return _handle_webhook()

    @blueprint.route("/webhook/messages", methods=["POST"])
    def webhook_messages() -> tuple[Any, int]:
        return _handle_webhook()

    return blueprint
