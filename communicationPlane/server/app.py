from __future__ import annotations

import logging
import os
import sys
import threading
import time
import uuid
from typing import Any

from flask import Flask, jsonify, request

# Add the project root to sys.path to import from controlplane and communicationPlane packages.
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)

from communicationPlane.telegramEngine.engine import TelegramEngine  # noqa: E402
from communicationPlane.telegramEngine.telegramInterface.webhook import (  # noqa: E402
    create_telegram_blueprint,
)
from controlplane.control.bot.salesbot.brain import (  # noqa: E402
    process_expired_corrections,
)
from controlplane.control.control_plane_interface import (  # noqa: E402
    ControlPlaneInterface,
)
from models.chat_message import ChatMessage  # noqa: E402
from shared.logging_context import LogContext, init_logging  # noqa: E402

init_logging()
logger = logging.getLogger(__name__)

# Background task interval for checking expired corrections (in seconds)
EXPIRED_CORRECTION_CHECK_INTERVAL = int(os.getenv("EXPIRED_CORRECTION_CHECK_INTERVAL", "3600"))  # 1 hour


def _run_expired_corrections_checker() -> None:
    """Background thread to periodically check for expired corrections."""
    logger.info(
        "Starting expired corrections checker (interval=%ds)",
        EXPIRED_CORRECTION_CHECK_INTERVAL,
    )
    while True:
        try:
            time.sleep(EXPIRED_CORRECTION_CHECK_INTERVAL)
            escalated = process_expired_corrections()
            if escalated > 0:
                logger.info("Escalated %d expired corrections", escalated)
        except Exception as exc:
            logger.error("Error in expired corrections checker: %s", exc, exc_info=True)


# Start background thread for expired corrections
_expired_checker_thread = threading.Thread(
    target=_run_expired_corrections_checker,
    daemon=True,
    name="ExpiredCorrectionsChecker",
)
_expired_checker_thread.start()

app = Flask(__name__)
control_plane = ControlPlaneInterface()
telegram_engine = TelegramEngine(
    control_plane,
    bot_user_id=os.getenv("TELEGRAM_BOT_USER_ID") or None,
)
app.register_blueprint(
    create_telegram_blueprint(telegram_engine.process_payload),
    url_prefix="/telegram",
)


def _is_debug_enabled() -> bool:
    return os.getenv("FLASK_DEBUG") == "1"


def _server_host() -> str:
    return os.getenv("SERVER_HOST", "127.0.0.1")


def _server_port() -> int:
    port_raw = os.getenv("SERVER_PORT") or os.getenv("PORT") or "5000"
    return int(port_raw)


def _read_last_log_lines(path: str, line_count: int) -> list[str]:
    if line_count <= 0:
        return []
    with open(path, encoding="utf-8") as log_file:
        lines = log_file.readlines()
    return lines[-line_count:]


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
            source="app",
            transport=chat_message.source,
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


@app.route("/logs", methods=["GET"])
def logs() -> tuple[Any, int]:
    """Get recent application logs from the local JSON log file."""
    logger.info("HTTP /logs called from %s", request.remote_addr)

    tail = request.args.get("tail", "100")

    try:
        tail_count = int(tail)
        app_log_path = os.getenv("APP_LOG_PATH") or os.path.join(PROJECT_ROOT, "logs", "app.jsonl")
        if not os.path.isabs(app_log_path):
            app_log_path = os.path.join(PROJECT_ROOT, app_log_path)

        if not os.path.exists(app_log_path):
            logger.warning("HTTP /logs missing log file path=%s", app_log_path)
            return jsonify({"error": f"log file not found: {app_log_path}"}), 404

        lines = _read_last_log_lines(app_log_path, tail_count)

        return jsonify(
            {
                "status": "success",
                "log_path": app_log_path,
                "tail": tail_count,
                "logs": "".join(lines),
            }
        ), 200
    except ValueError:
        logger.warning("HTTP /logs invalid tail=%s", tail)
        return jsonify({"error": "tail must be a valid integer"}), 400
    except Exception as exc:
        logger.error("HTTP /logs failed: %s", exc, exc_info=True)
        return jsonify({"error": str(exc)}), 500


if __name__ == "__main__":
    app.run(host=_server_host(), port=_server_port(), debug=_is_debug_enabled())
