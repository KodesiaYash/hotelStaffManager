from __future__ import annotations

import os
from typing import Any

from google import genai

DEFAULT_MODEL = "gemini-2.5-flash"
DEFAULT_CONFIG: dict[str, Any] = {
    "temperature": 0,
    "response_mime_type": "application/json",
}


class GeminiInterface:
    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        self._api_key = api_key
        self.model = model or os.getenv("GEMINI_MODEL") or DEFAULT_MODEL
        self.config = config or DEFAULT_CONFIG
        self._client: genai.Client | None = None

    def _get_client(self) -> genai.Client:
        api_key = self._api_key or os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY is not set")
        if self._client is None:
            self._client = genai.Client(api_key=api_key)
        return self._client

    def generate(self, prompt: str) -> str:
        client = self._get_client()
        response = client.models.generate_content(
            model=self.model,
            contents={"text": prompt},
            config=self.config,  # type: ignore[arg-type]
        )
        return response.text or ""
