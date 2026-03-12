from __future__ import annotations

from typing import Any

import pytest

from control.bot.salesBot import brain as brain_module


def test_safe_json_parse_handles_empty() -> None:
    assert brain_module.safe_json_parse("") == {"error": "empty_response"}


def test_safe_json_parse_strips_json_fences() -> None:
    raw = """```json\n{\"Service\": \"Spa\"}\n```"""
    assert brain_module.safe_json_parse(raw) == {"Service": "Spa"}


def test_update_costs_handles_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(_service: str, _quantity: Any) -> float:
        raise RuntimeError("boom")

    monkeypatch.setattr(
        brain_module,
        "_get_sales_audit",
        lambda: type("X", (), {"calculate_cost": _raise})(),
    )
    assert brain_module.update_costs("Spa", 2) == 0.0
