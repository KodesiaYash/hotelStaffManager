from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

DEFAULT_MODEL = "claude-3-5-sonnet-20240620"
DEFAULT_CONFIG: dict[str, Any] = {
    "temperature": 0,
    "max_tokens": 2048,
}

if TYPE_CHECKING:
    from anthropic import Anthropic


class ClaudeInterface:
    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        self._api_key = api_key
        self.model = model or os.getenv("CLAUDE_MODEL") or DEFAULT_MODEL
        self.config = config or DEFAULT_CONFIG
        self._client: Anthropic | None = None

    def _get_client(self) -> Anthropic:
        api_key = self._api_key or os.getenv("ANTHROPIC_API_KEY") or os.getenv("CLAUDE_API_KEY")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY is not set")
        if self._client is None:
            try:
                from anthropic import Anthropic
            except ModuleNotFoundError as exc:
                raise ModuleNotFoundError(
                    "anthropic is not installed. Run `python -m pip install anthropic` "
                    "or `python -m pip install -r requirements.txt`."
                ) from exc
            self._client = Anthropic(api_key=api_key)
        return self._client

    def generate(self, prompt: str) -> str:
        client = self._get_client()
        response = client.messages.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=self.config.get("temperature", 0),
            max_tokens=self.config.get("max_tokens", 1024),
        )
        content = response.content or []
        parts: list[str] = []
        for part in content:
            text = getattr(part, "text", None)
            if isinstance(text, str) and text:
                parts.append(text)
        return "".join(parts)
