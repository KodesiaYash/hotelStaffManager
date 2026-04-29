from __future__ import annotations

import logging
import os
from typing import Any

import requests

from models.telegram import TelegramConfig

logger = logging.getLogger(__name__)


class TelegramError(RuntimeError):
    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        payload: Any | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


def _coerce_message_id_int(value: Any) -> int | None:
    """Parse an integer message ID from a plain int, plain str, or
    the internal composite format 'telegram:CHAT_ID:MESSAGE_ID'."""
    if value is None:
        return None
    if isinstance(value, int):
        return value
    s = str(value).strip()
    if ":" in s:
        s = s.rsplit(":", 1)[-1]
    try:
        return int(s)
    except (TypeError, ValueError):
        return None


def _coerce_reply_to(value: Any) -> int | None:
    """Telegram's reply_to_message_id is an integer. Accept str or int."""
    return _coerce_message_id_int(value)


class TelegramClient:
    """Minimal Telegram Bot API client.

    Exposes a stable method surface (``send_text``, ``send_image``,
    ``send_video``, ``send_document``) so control plane code can send
    messages without knowing the underlying transport.
    """

    def __init__(
        self,
        config: TelegramConfig | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self.config = config or TelegramConfig.from_env()
        self._session = session or requests.Session()

    def _get_token(self) -> str:
        token = self.config.token or os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")
        if not token:
            raise ValueError("TELEGRAM_BOT_TOKEN is not set")
        return token

    def _url(self, method: str) -> str:
        base = self.config.base_url.rstrip("/")
        return f"{base}/bot{self._get_token()}/{method}"

    def _request(
        self,
        method: str,
        *,
        json: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = self._url(method)
        response = self._session.post(
            url=url,
            json=json,
            data=data,
            files=files,
            timeout=self.config.timeout,
        )
        if response.status_code >= 400:
            try:
                payload: Any = response.json()
            except ValueError:
                payload = response.text
            logger.error(
                "Telegram request failed method=%s status=%s payload=%s",
                method,
                response.status_code,
                payload,
            )
            raise TelegramError(
                f"Telegram request failed ({response.status_code})",
                status_code=response.status_code,
                payload=payload,
            )
        if not response.content:
            return {}
        try:
            body = response.json()
        except ValueError:
            return {"raw": response.text}
        if isinstance(body, dict) and body.get("ok") is False:
            raise TelegramError(
                f"Telegram error: {body.get('description', 'unknown')}",
                status_code=body.get("error_code"),
                payload=body,
            )
        return body

    def _apply_parse_mode(self, payload: dict[str, Any]) -> None:
        if self.config.parse_mode and "parse_mode" not in payload:
            payload["parse_mode"] = self.config.parse_mode

    def send_text(
        self,
        to: str,
        body: str,
        *,
        mentions: list[str] | None = None,
        quoted: str | None = None,
        typing_time: float | None = None,
        no_link_preview: bool | None = None,
        wide_link_preview: bool | None = None,
        parse_mode: str | None = None,
    ) -> dict[str, Any]:
        logger.info("Telegram send_text to=%s body_len=%d", to, len(body))
        payload: dict[str, Any] = {"chat_id": to, "text": body}
        reply_to = _coerce_reply_to(quoted)
        if reply_to is not None:
            payload["reply_to_message_id"] = reply_to
        if no_link_preview is not None:
            payload["disable_web_page_preview"] = bool(no_link_preview)
        if parse_mode:
            payload["parse_mode"] = parse_mode
        self._apply_parse_mode(payload)
        return self._request("sendMessage", json=payload)

    def send_notification(self, to: str, body: str) -> dict[str, Any]:
        return self.send_text(to=to, body=body)

    def send_image(
        self,
        to: str,
        media: str,
        *,
        caption: str | None = None,
        mentions: list[str] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"chat_id": to, "photo": media}
        if caption:
            payload["caption"] = caption
        self._apply_parse_mode(payload)
        return self._request("sendPhoto", json=payload)

    def send_video(
        self,
        to: str,
        media: str,
        *,
        caption: str | None = None,
        mentions: list[str] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"chat_id": to, "video": media}
        if caption:
            payload["caption"] = caption
        self._apply_parse_mode(payload)
        return self._request("sendVideo", json=payload)

    def send_document(
        self,
        to: str,
        media: str,
        *,
        filename: str | None = None,
        caption: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"chat_id": to, "document": media}
        if caption:
            payload["caption"] = caption
        self._apply_parse_mode(payload)
        return self._request("sendDocument", json=payload)

    def set_reaction(
        self,
        chat_id: str,
        message_id: Any,
        emoji: str,
        *,
        is_big: bool = False,
    ) -> dict[str, Any]:
        """Set an emoji reaction on a message via setMessageReaction."""
        msg_id = _coerce_message_id_int(message_id)
        if msg_id is None:
            logger.warning("set_reaction: could not parse message_id=%r, skipping", message_id)
            return {}
        logger.info("Telegram set_reaction chat_id=%s message_id=%s emoji=%s", chat_id, msg_id, emoji)
        return self._request(
            "setMessageReaction",
            json={
                "chat_id": chat_id,
                "message_id": msg_id,
                "reaction": [{"type": "emoji", "emoji": emoji}],
                "is_big": is_big,
            },
        )

    def get_messages(
        self,
        chat_id: str,
        *,
        count: int = 100,
        offset: int | None = None,
    ) -> list[dict[str, Any]]:
        """Telegram bots cannot fetch arbitrary chat history like WhatsApp did.

        The Bot API exposes ``getUpdates`` which only returns unconsumed
        updates for this bot (and only if a webhook is NOT configured). This
        method is kept for API compatibility but returns an empty list.
        Use webhook updates instead.
        """
        logger.warning(
            "TelegramClient.get_messages is not supported by the Telegram Bot API; returning []",
        )
        return []
