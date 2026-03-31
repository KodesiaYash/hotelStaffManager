from __future__ import annotations

import logging
import os
from typing import Any

import requests

from models.whapi import WhapiConfig

logger = logging.getLogger(__name__)


class WhapiError(RuntimeError):
    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        payload: Any | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


class WhapiClient:
    def __init__(
        self,
        config: WhapiConfig | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self.config = config or WhapiConfig.from_env()
        self._session = session or requests.Session()

    def _get_token(self) -> str:
        token = self.config.token or os.getenv("WHAPI_TOKEN") or os.getenv("WHAPI_API_KEY")
        if not token:
            raise ValueError("WHAPI_TOKEN is not set")
        return token

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if not self.config.use_token_query:
            headers["Authorization"] = f"Bearer {self._get_token()}"
        return headers

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.config.base_url.rstrip('/')}{path}"
        query = params or {}
        if self.config.use_token_query:
            query["token"] = self._get_token()
        response = self._session.request(
            method=method,
            url=url,
            headers=self._headers(),
            params=query,
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
                "WHAPI request failed method=%s path=%s status=%s payload=%s",
                method,
                path,
                response.status_code,
                payload,
            )
            raise WhapiError(
                f"WHAPI request failed ({response.status_code})",
                status_code=response.status_code,
                payload=payload,
            )
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError:
            return {"raw": response.text}

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
    ) -> dict[str, Any]:
        logger.info("WHAPI send_text to=%s body_len=%d", to, len(body))
        payload: dict[str, Any] = {"to": to, "body": body}
        if mentions:
            payload["mentions"] = mentions
        if quoted:
            payload["quoted"] = quoted
        if typing_time is not None:
            payload["typing_time"] = typing_time
        if no_link_preview is not None:
            payload["no_link_preview"] = no_link_preview
        if wide_link_preview is not None:
            payload["wide_link_preview"] = wide_link_preview
        return self._request("POST", "/messages/text", json=payload)

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
        payload: dict[str, Any] = {"to": to, "media": media}
        if caption:
            payload["caption"] = caption
        if mentions:
            payload["mentions"] = mentions
        return self._request("POST", "/messages/image", json=payload)

    def send_video(
        self,
        to: str,
        media: str,
        *,
        caption: str | None = None,
        mentions: list[str] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"to": to, "media": media}
        if caption:
            payload["caption"] = caption
        if mentions:
            payload["mentions"] = mentions
        return self._request("POST", "/messages/video", json=payload)

    def send_document(
        self,
        to: str,
        media: str,
        *,
        filename: str | None = None,
        caption: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"to": to, "media": media}
        if filename:
            payload["filename"] = filename
        if caption:
            payload["caption"] = caption
        return self._request("POST", "/messages/document", json=payload)

    def get_messages(
        self,
        chat_id: str,
        *,
        count: int = 100,
        offset: int | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch messages from a chat.

        Args:
            chat_id: The chat ID (e.g., '120363408154982447@g.us' for groups)
            count: Number of messages to fetch (default 100, max 500)
            offset: Offset for pagination

        Returns:
            List of message objects
        """
        params: dict[str, Any] = {"count": min(count, 500)}
        if offset is not None:
            params["offset"] = offset
        result = self._request("GET", f"/messages/list/{chat_id}", params=params)
        return result.get("messages", [])
