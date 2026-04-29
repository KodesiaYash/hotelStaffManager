from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from shared.logging_context import JsonFormatter, LogContext


def _make_record(message: str) -> logging.LogRecord:
    return logging.LogRecord(
        name="tests.logging",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=(),
        exc_info=None,
    )


def test_json_formatter_defaults_source_to_app() -> None:
    formatter = JsonFormatter()
    record = _make_record("hello world")

    payload = json.loads(formatter.format(record))

    assert payload["message"] == "hello world"
    assert payload["source"] == "app"
    assert "transport" not in payload


def test_json_formatter_includes_context_and_extra_fields() -> None:
    formatter = JsonFormatter()
    record = _make_record("db operation")
    record.db = "postgres"
    record.operation = "read"
    record.entity = "memory_items"
    record.metadata = {"layers": ["summary", "semantic"]}
    record.recorded_at = datetime(2026, 4, 29, tzinfo=UTC)

    with LogContext(source="sql", transport="telegram", request_id="req-1", chat_id="chat-7"):
        payload = json.loads(formatter.format(record))

    assert payload["source"] == "sql"
    assert payload["transport"] == "telegram"
    assert payload["request_id"] == "req-1"
    assert payload["chat_id"] == "chat-7"
    assert payload["db"] == "postgres"
    assert payload["operation"] == "read"
    assert payload["entity"] == "memory_items"
    assert payload["metadata"] == {"layers": ["summary", "semantic"]}
    assert payload["recorded_at"] == "2026-04-29T00:00:00+00:00"
