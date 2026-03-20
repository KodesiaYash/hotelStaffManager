from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_CONFIG: dict[str, Any] = {
    "temperature": 0,
    "max_tokens": 16384,
}

if TYPE_CHECKING:
    from openai import OpenAI
    from openai.types.chat import ChatCompletionMessageParam


class OpenAIInterface:
    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        config: dict[str, Any] | None = None,
        base_url: str | None = None,
    ) -> None:
        self._api_key = api_key
        self.model = model or os.getenv("OPENAI_MODEL") or DEFAULT_MODEL
        self.config = config or DEFAULT_CONFIG
        self.base_url = base_url or os.getenv("OPENAI_BASE_URL")
        self._client: OpenAI | None = None

    def _get_client(self) -> OpenAI:
        api_key = self._api_key or os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY is not set")
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

    def generate(self, prompt: str, system_prompt: str | None = None) -> str:
        client = self._get_client()
        messages: list[ChatCompletionMessageParam] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        response = client.chat.completions.create(
            model=self.model,
            messages=messages,
            **self.config,
        )
        return response.choices[0].message.content or ""
