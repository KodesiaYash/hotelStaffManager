from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

# How long to cool down a provider after a 429 (seconds)
DEFAULT_COOLDOWN_SECONDS = 60


RETRIABLE_STATUS_CODES = {429, 503, 529}


def _is_rate_limit_error(exc: BaseException) -> bool:
    """Check if an exception is a retriable capacity error (429, 503, 529)."""
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    if status in RETRIABLE_STATUS_CODES:
        return True
    exc_str = str(exc).lower()
    return "429" in exc_str or "rate" in exc_str or "503" in exc_str or "overloaded" in exc_str


class FallbackLLM:
    """Wraps multiple LLM providers and falls through on 429 errors.

    Usage:
        llm = FallbackLLM([GeminiInterface(), OpenAIInterface(), ClaudeInterface()])
        result = llm.generate(prompt)
        # If Gemini returns 429, automatically retries with OpenAI, then Claude.
    """

    def __init__(
        self,
        providers: list[Any],
        cooldown_seconds: int = DEFAULT_COOLDOWN_SECONDS,
    ) -> None:
        if not providers:
            raise ValueError("At least one LLM provider is required")
        self._providers = providers
        self._cooldown_seconds = cooldown_seconds
        # Track when each provider was last rate-limited
        self._cooldowns: dict[int, float] = {}

    @property
    def provider_count(self) -> int:
        return len(self._providers)

    def _provider_name(self, idx: int) -> str:
        return type(self._providers[idx]).__name__

    def _is_cooled_down(self, idx: int) -> bool:
        last_hit = self._cooldowns.get(idx)
        if last_hit is None:
            return True
        return (time.time() - last_hit) >= self._cooldown_seconds

    def generate(self, prompt: str, **kwargs: Any) -> str:
        errors: list[tuple[str, BaseException]] = []

        for idx, provider in enumerate(self._providers):
            name = self._provider_name(idx)

            if not self._is_cooled_down(idx):
                logger.debug("Skipping %s (cooling down)", name)
                continue

            try:
                result = provider.generate(prompt, **kwargs)
                if errors:
                    # We fell through from another provider
                    logger.info("Fallback succeeded with %s after %d failure(s)", name, len(errors))
                return result
            except Exception as exc:
                if _is_rate_limit_error(exc):
                    self._cooldowns[idx] = time.time()
                    logger.warning(
                        "Rate limited (429) on %s, cooling down %ds — trying next provider",
                        name,
                        self._cooldown_seconds,
                    )
                    errors.append((name, exc))
                    continue
                else:
                    # Non-429 error — don't swallow it
                    logger.error("LLM call failed on %s (non-429): %s", name, exc)
                    raise

        # All providers exhausted
        provider_names = [self._provider_name(i) for i in range(len(self._providers))]
        error_summary = "; ".join(f"{name}: {exc}" for name, exc in errors)
        raise RuntimeError(f"All LLM providers rate-limited: [{', '.join(provider_names)}]. Errors: {error_summary}")
