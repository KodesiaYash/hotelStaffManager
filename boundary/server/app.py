from __future__ import annotations

import os
import sys
from typing import Any

from flask import Flask, jsonify, request

# Add the project root to sys.path to import from control/bot/salesBot
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)

from control.bot.salesBot.test import process_message  # noqa: E402

app = Flask(__name__)


def _is_debug_enabled() -> bool:
    return os.getenv("FLASK_DEBUG") == "1"


@app.route("/process", methods=["POST"])
def process() -> tuple[Any, int]:
    data = request.get_json(silent=True)
    if not data or "message" not in data:
        return jsonify({"error": "Missing message"}), 400

    message = data["message"]
    try:
        process_message(message)
        return jsonify({"status": "success"}), 200
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


if __name__ == "__main__":
    app.run(debug=_is_debug_enabled())
