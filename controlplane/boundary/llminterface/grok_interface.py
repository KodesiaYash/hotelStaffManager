from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

DEFAULT_MODEL = "grok-beta"
DEFAULT_CONFIG: dict[str, Any] = {
    "temperature": 0,
    "max_tokens": 2048,
}

if TYPE_CHECKING:
    from openai import OpenAI


class GrokInterface:
    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        config: dict[str, Any] | None = None,
        base_url: str | None = None,
    ) -> None:
        self._api_key = api_key
        self.model = model or os.getenv("GROK_MODEL") or DEFAULT_MODEL
        self.config = config or DEFAULT_CONFIG
        self.base_url = base_url or os.getenv("GROK_BASE_URL") or "https://api.x.ai/v1"
        self._client: OpenAI | None = None

    def _get_client(self) -> OpenAI:
        api_key = self._api_key or os.getenv("GROK_API_KEY") or os.getenv("XAI_API_KEY") or os.getenv("XAI_TOKEN")
        if not api_key:
            raise ValueError("GROK_API_KEY is not set")
        if self._client is None:
            try:
                from openai import OpenAI
            except ModuleNotFoundError as exc:
                raise ModuleNotFoundError(
                    "openai is not installed. Run `python -m pip install openai` "
                    "or `python -m pip install -r requirements.txt`."
                ) from exc
            self._client = OpenAI(api_key=api_key, base_url=self.base_url)
        return self._client

    def generate(self, prompt: str) -> str:
        client = self._get_client()
        response = client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            **self.config,
        )
        return response.choices[0].message.content or ""
