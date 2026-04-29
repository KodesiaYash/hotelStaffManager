from __future__ import annotations

import contextvars
import json
import logging
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

_CONTEXT_VARS: dict[str, contextvars.ContextVar[str | None]] = {
    "message_id": contextvars.ContextVar("message_id", default=None),
    "chat_id": contextvars.ContextVar("chat_id", default=None),
    "chat_name": contextvars.ContextVar("chat_name", default=None),
    "sender_id": contextvars.ContextVar("sender_id", default=None),
    "sender_name": contextvars.ContextVar("sender_name", default=None),
    "request_id": contextvars.ContextVar("request_id", default=None),
    "source": contextvars.ContextVar("source", default=None),
    "transport": contextvars.ContextVar("transport", default=None),
}

_LOGGING_CONFIGURED = False
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_ABS_PROJECT_ROOT = os.path.realpath(_PROJECT_ROOT)
_DEFAULT_LOW_CONF_PATH = os.path.join(_PROJECT_ROOT, "logs", "low_confidence.jsonl")
_DEFAULT_MEDIUM_CONF_PATH = os.path.join(_PROJECT_ROOT, "logs", "medium_confidence.jsonl")
_DEFAULT_ERROR_PATH = os.path.join(_PROJECT_ROOT, "logs", "error.jsonl")
_DEFAULT_SOURCE = "app"


def _current_context() -> dict[str, str]:
    context: dict[str, str] = {}
    for key, var in _CONTEXT_VARS.items():
        value = var.get()
        if value:
            context[key] = value
    return context


def _ensure_parent_dir(path: str) -> None:
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)


def _set_context(**kwargs: str | None) -> dict[str, contextvars.Token]:
    tokens: dict[str, contextvars.Token] = {}
    for key, value in kwargs.items():
        if key in _CONTEXT_VARS and value is not None:
            tokens[key] = _CONTEXT_VARS[key].set(str(value))
    return tokens


def _reset_context(tokens: dict[str, contextvars.Token]) -> None:
    for key, token in tokens.items():
        if key in _CONTEXT_VARS:
            _CONTEXT_VARS[key].reset(token)


@dataclass
class LogContext:
    message_id: str | None = None
    chat_id: str | None = None
    chat_name: str | None = None
    sender_id: str | None = None
    sender_name: str | None = None
    request_id: str | None = None
    source: str | None = None
    transport: str | None = None
    _tokens: dict[str, contextvars.Token] | None = None

    def __enter__(self) -> LogContext:
        self._tokens = _set_context(
            message_id=self.message_id,
            chat_id=self.chat_id,
            chat_name=self.chat_name,
            sender_id=self.sender_id,
            sender_name=self.sender_name,
            request_id=self.request_id,
            source=self.source,
            transport=self.transport,
        )
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self._tokens:
            _reset_context(self._tokens)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": message,
            "source": _DEFAULT_SOURCE,
        }
        payload.update(_current_context())
        payload.update(_record_extras(record))
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


_RESERVED_LOG_RECORD_ATTRS = frozenset(vars(logging.LogRecord("", 0, "", 0, "", (), None)).keys())


def _record_extras(record: logging.LogRecord) -> dict[str, Any]:
    extras: dict[str, Any] = {}
    for key, value in record.__dict__.items():
        if key in _RESERVED_LOG_RECORD_ATTRS or key.startswith("_"):
            continue
        extras[key] = _normalize_json_value(value)
    return extras


def _normalize_json_value(value: Any) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, dict):
        return {str(key): _normalize_json_value(item) for key, item in value.items()}
    if isinstance(value, list | tuple | set):
        return [_normalize_json_value(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


_DEFAULT_APP_LOG_PATH = os.path.join(_PROJECT_ROOT, "logs", "app.jsonl")


def init_logging(level: str | None = None) -> None:
    global _LOGGING_CONFIGURED
    if _LOGGING_CONFIGURED:
        return
    log_level = (level or os.getenv("LOG_LEVEL") or "INFO").upper()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(log_level)
    root.addHandler(handler)

    # Add file handler for all logs (for Promtail/Loki)
    app_log_path = os.getenv("APP_LOG_PATH") or _DEFAULT_APP_LOG_PATH
    if not os.path.isabs(app_log_path):
        app_log_path = os.path.join(_ABS_PROJECT_ROOT, app_log_path)
    _ensure_parent_dir(app_log_path)
    app_handler = logging.FileHandler(app_log_path)
    app_handler.setLevel(log_level)
    app_handler.setFormatter(JsonFormatter())
    root.addHandler(app_handler)

    # Error-only file handler
    error_path = os.getenv("ERROR_LOG_PATH") or _DEFAULT_ERROR_PATH
    if not os.path.isabs(error_path):
        error_path = os.path.join(_ABS_PROJECT_ROOT, error_path)
    _ensure_parent_dir(error_path)
    error_handler = logging.FileHandler(error_path)
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(JsonFormatter())
    root.addHandler(error_handler)
    _LOGGING_CONFIGURED = True


def log_low_confidence(payload: dict[str, Any]) -> None:
    path = os.getenv("LOW_CONFIDENCE_LOG_PATH") or _DEFAULT_LOW_CONF_PATH
    if not os.path.isabs(path):
        path = os.path.join(_ABS_PROJECT_ROOT, path)
    _ensure_parent_dir(path)
    entry = {
        "timestamp": datetime.now(UTC).isoformat(),
        "source": _DEFAULT_SOURCE,
        **_current_context(),
        **_normalize_json_value(payload),
    }
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def log_medium_confidence(payload: dict[str, Any]) -> None:
    path = os.getenv("MEDIUM_CONFIDENCE_LOG_PATH") or _DEFAULT_MEDIUM_CONF_PATH
    if not os.path.isabs(path):
        path = os.path.join(_ABS_PROJECT_ROOT, path)
    _ensure_parent_dir(path)
    entry = {
        "timestamp": datetime.now(UTC).isoformat(),
        "source": _DEFAULT_SOURCE,
        **_current_context(),
        **_normalize_json_value(payload),
    }
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
