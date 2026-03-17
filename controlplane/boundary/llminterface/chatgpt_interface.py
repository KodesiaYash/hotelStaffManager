from __future__ import annotations

import os
from typing import Any

from openai import OpenAI

DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_CONFIG: dict[str, Any] = {
    "temperature": 0,
    "max_tokens": 16384,
}


class ChatGPTInterface:
    def __init__(
        self,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        config: dict[str, Any] | None = None,
    ) -> None:
        self._api_key = api_key
        self.model = model
        self.config = config or DEFAULT_CONFIG
        self._client: OpenAI | None = None

    def _get_client(self) -> OpenAI:
        api_key = self._api_key or os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY is not set")
        if self._client is None:
            self._client = OpenAI(api_key=api_key)
        return self._client

    def generate(self, prompt: str, system_prompt: str | None = None) -> str:
        client = self._get_client()
        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        response = client.chat.completions.create(
            model=self.model,
            messages=messages,  # type: ignore[arg-type]
            **self.config,
        )
        return response.choices[0].message.content or ""
