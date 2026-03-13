from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

DEFAULT_BASE_URL = "https://gate.whapi.cloud"


def _extract_text(message: dict[str, Any]) -> str | None:
    text = (message.get("text") or {}).get("body")
    if text:
        return text
    for key in ("image", "video", "document"):
        media = message.get(key) or {}
        caption = media.get("caption")
        if caption:
            return caption
    return None


@dataclass(frozen=True)
class WhapiConfig:
    token: str | None = None
    base_url: str = DEFAULT_BASE_URL
    timeout: float = 20.0
    use_token_query: bool = False

    @classmethod
    def from_env(cls) -> WhapiConfig:
        token = os.getenv("WHAPI_TOKEN") or os.getenv("WHAPI_API_KEY")
        base_url = os.getenv("WHAPI_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
        timeout_raw = os.getenv("WHAPI_TIMEOUT", "20")
        try:
            timeout = float(timeout_raw)
        except ValueError:
            timeout = 20.0
        use_token_query = os.getenv("WHAPI_TOKEN_IN_QUERY") == "1"
        return cls(
            token=token,
            base_url=base_url,
            timeout=timeout,
            use_token_query=use_token_query,
        )


@dataclass(frozen=True)
class WhapiMessage:
    message_id: str
    message_type: str
    chat_id: str
    from_id: str | None
    from_name: str | None
    timestamp: float
    text: str | None
    raw: dict[str, Any]

    @property
    def is_group(self) -> bool:
        return self.chat_id.endswith("@g.us")

    @property
    def is_private(self) -> bool:
        return not self.is_group

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> WhapiMessage:
        raw_id = raw.get("id")
        return cls(
            message_id=str(raw_id) if raw_id is not None else "",
            message_type=str(raw.get("type", "")),
            chat_id=str(raw.get("chat_id", "")),
            from_id=raw.get("from"),
            from_name=raw.get("from_name"),
            timestamp=float(raw.get("timestamp", 0)),
            text=_extract_text(raw),
            raw=raw,
        )
