"""Unit tests for the SalesBot brain orchestration logic."""

from __future__ import annotations

import pytest

from controlplane.control.bot.salesbot import brain as brain_module


def test_llm_extract_retries_on_parse_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Retry once when the first LLM response cannot be parsed as JSON."""
    responses = ["not json", '{"Service": "Spa"}']

    class DummyLLM:
        def generate(self, _prompt: str) -> str:
            return responses.pop(0)

    monkeypatch.setattr(brain_module, "_get_llm_interface", lambda: DummyLLM())
    result = brain_module.llm_extract("hello")
    assert result == {"Service": "Spa"}


def test_process_message_skips_on_extraction_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip write flow entirely if extraction returns an error payload."""
    monkeypatch.setattr(brain_module, "llm_extract", lambda _msg: {"error": "bad"})
    monkeypatch.setattr(
        brain_module,
        "_get_sales_audit",
        lambda: (_ for _ in ()).throw(RuntimeError("should not be called")),
    )
    brain_module.process_message("hello")
