from __future__ import annotations

import logging
import os
from typing import Protocol

from controlplane.boundary.llminterface.claude_interface import ClaudeInterface
from controlplane.boundary.llminterface.gemini_interface import GeminiInterface
from controlplane.boundary.llminterface.grok_interface import GrokInterface
from controlplane.boundary.llminterface.openai_interface import OpenAIInterface

logger = logging.getLogger(__name__)


class LLMInterface(Protocol):
    def generate(self, prompt: str) -> str: ...


def get_sales_bot_llm(provider: str | None = None) -> LLMInterface:
    name = (provider or os.getenv("SALES_BOT_LLM_PROVIDER") or "gemini").strip().lower()
    if name in {"gemini", "google"}:
        logger.info("SalesBot LLM provider: gemini")
        return GeminiInterface()
    if name in {"openai", "gpt", "chatgpt"}:
        logger.info("SalesBot LLM provider: openai")
        return OpenAIInterface()
    if name in {"claude", "anthropic"}:
        logger.info("SalesBot LLM provider: claude")
        return ClaudeInterface()
    if name in {"grok", "xai"}:
        logger.info("SalesBot LLM provider: grok")
        return GrokInterface()
    raise ValueError(f"Unsupported SALES_BOT_LLM_PROVIDER: {name}")
