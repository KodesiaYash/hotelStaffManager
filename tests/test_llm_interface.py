from __future__ import annotations

import pytest

from boundary.llminterface.gemini_interface import GeminiInterface


def test_generate_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    interface = GeminiInterface(api_key=None)
    with pytest.raises(ValueError):
        interface.generate("hello")
