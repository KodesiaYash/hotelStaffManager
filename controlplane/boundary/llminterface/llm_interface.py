from __future__ import annotations

import logging
import os
from typing import Protocol

from controlplane.boundary.llminterface.claude_interface import ClaudeInterface
from controlplane.boundary.llminterface.fallback_llm import FallbackLLM
from controlplane.boundary.llminterface.gemini_interface import GeminiInterface
from controlplane.boundary.llminterface.grok_interface import GrokInterface
from controlplane.boundary.llminterface.openai_interface import OpenAIInterface

logger = logging.getLogger(__name__)

_PROVIDER_MAP: dict[str, type] = {
    "gemini": GeminiInterface,
    "google": GeminiInterface,
    "openai": OpenAIInterface,
    "gpt": OpenAIInterface,
    "chatgpt": OpenAIInterface,
    "claude": ClaudeInterface,
    "anthropic": ClaudeInterface,
    "grok": GrokInterface,
    "xai": GrokInterface,
}


class LLMInterface(Protocol):
    def generate(self, prompt: str) -> str: ...


def _build_provider(name: str) -> LLMInterface:
    cls = _PROVIDER_MAP.get(name)
    if cls is None:
        raise ValueError(f"Unknown LLM provider: {name!r}")
    return cls()


def get_sales_bot_llm(provider: str | None = None) -> LLMInterface:
    """Return an LLM for the sales bot.

    Supports automatic 429 fallback when multiple providers are configured.
    Set ``SALES_BOT_LLM_PROVIDER`` to a comma-separated list for fallback,
    e.g. ``gemini,openai,claude``.  The first provider is primary; the rest
    are used only when the previous one returns HTTP 429.

    A single provider name (no comma) returns that provider directly.
    """
    raw = (provider or os.getenv("SALES_BOT_LLM_PROVIDER") or "gemini").strip().lower()
    names = [n.strip() for n in raw.split(",") if n.strip()]

    if len(names) == 1:
        logger.info("SalesBot LLM provider: %s", names[0])
        return _build_provider(names[0])

    # Multiple providers → fallback chain
    providers: list[LLMInterface] = []
    for name in names:
        try:
            providers.append(_build_provider(name))
        except Exception as exc:
            logger.warning("Skipping unavailable fallback provider %s: %s", name, exc)
    if not providers:
        raise ValueError(f"No valid LLM providers in fallback chain: {raw}")

    cooldown = int(os.getenv("LLM_FALLBACK_COOLDOWN_SECONDS", "60"))
    logger.info(
        "SalesBot LLM fallback chain: %s (cooldown=%ds)",
        " → ".join(names),
        cooldown,
    )
    return FallbackLLM(providers, cooldown_seconds=cooldown)
