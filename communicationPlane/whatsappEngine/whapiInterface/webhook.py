from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable
from typing import Any

from flask import Blueprint, jsonify, request

from models.chat_message import ChatMessage
from shared.logging_context import LogContext

PayloadHandler = Callable[[dict[str, Any]], list[ChatMessage]]

logger = logging.getLogger(__name__)


def create_whapi_blueprint(handler: PayloadHandler) -> Blueprint:
    blueprint = Blueprint("whapi_webhook", __name__)

    def _handle_webhook() -> tuple[Any, int]:
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        started = time.time()
        payload = request.get_json(silent=True) or {}
        message_count = len(payload.get("messages") or [])
        with LogContext(request_id=request_id, source="whapi_webhook"):
            logger.info(
                "Webhook path=%s remote=%s messages=%d",
                request.path,
                request.remote_addr,
                message_count,
            )
            logger.info("Webhook payload: %s", payload)
            try:
                messages = handler(payload)
            except Exception as exc:  # pragma: no cover - defensive logging
                logger.error("Webhook failed: %s", exc, exc_info=True)
                return jsonify({"status": "error", "request_id": request_id}), 500
            elapsed_ms = int((time.time() - started) * 1000)
            logger.info(
                "Webhook processed=%d elapsed_ms=%d",
                len(messages),
                elapsed_ms,
            )
        return jsonify({"status": "ok", "messages": len(messages), "request_id": request_id}), 200

    @blueprint.route("/webhook", methods=["POST"])
    def webhook() -> tuple[Any, int]:
        return _handle_webhook()

    @blueprint.route("/webhook/messages", methods=["POST"])
    def webhook_messages() -> tuple[Any, int]:
        return _handle_webhook()

    return blueprint
