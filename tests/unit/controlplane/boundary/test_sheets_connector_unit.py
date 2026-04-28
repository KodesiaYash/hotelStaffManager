"""Unit tests for Sheets connector helpers and cost calculation."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from controlplane.boundary.storageInterface import salesAudit as sales_audit_module
from controlplane.boundary.storageInterface.sheetsConnector import _resolve_path, normalize_env_value


def test_normalize_env_value_strips_quotes_and_whitespace() -> None:
    """Normalize env values by trimming whitespace and stripping quotes."""
    assert normalize_env_value("  'abc'  ") == "abc"
    assert normalize_env_value('  "abc"  ') == "abc"
    assert normalize_env_value("  abc  ") == "abc"
    assert normalize_env_value(123) == "123"
    assert normalize_env_value("") is None
    assert normalize_env_value(None) is None


def test_resolve_path_prefers_base_dir(tmp_path: Path) -> None:
    """Resolve relative paths against a provided base directory first."""
    target = tmp_path / "service.json"
    target.write_text("{}", encoding="utf-8")

    resolved, tried = _resolve_path("service.json", base_dir=str(tmp_path))
    assert resolved == str(target)
    assert str(target) in tried


def test_calculate_cost_uses_pricelist(monkeypatch: pytest.MonkeyPatch) -> None:
    """Calculate costs using a provided PriceList implementation."""

    class DummyConnector:
        def __init__(self, _config: dict[str, Any]) -> None:
            self.config = _config

    class DummyPriceList:
        def read_pricelist(self) -> list[dict[str, str]]:
            return [
                {"Service": "Hammam", "Cost": "150"},
                {"Service": "Airport Transfer", "Cost": "300"},
            ]

        def write_pricelist(self, data: Sequence[Any]) -> None:
            _ = data

    monkeypatch.setattr(sales_audit_module, "SheetsConnector", DummyConnector)

    audit = sales_audit_module.SalesAudit(
        config={
            "service_account_file": "ignored",
            "sheets": {sales_audit_module.DETAILS_KEY: {"spreadsheet_id": "dummy"}},
        },
        pricelist=DummyPriceList(),
    )

    assert audit.calculate_cost("2 Hammam", 2) == 300.0
    assert audit.calculate_cost("Airport Transfer", 1) == 300.0
    assert audit.calculate_cost("Unknown", 3) == 0.0


def test_validate_service_uses_llm_to_resolve_ambiguous_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prompts: list[str] = []

    class DummyConnector:
        def __init__(self, _config: dict[str, Any]) -> None:
            self.config = _config

    class DummyPriceList:
        def read_pricelist(self) -> list[dict[str, str]]:
            return [
                {"Service": "One Hour Massage"},
                {"Service": "Hammam + Massage"},
                {"Service": "Transfer To Airport"},
                {"Service": "Quad + Dinner"},
                {"Service": "Dinner Deluxe"},
            ]

        def write_pricelist(self, data: Sequence[Any]) -> None:
            _ = data

    class DummyLLM:
        def generate(self, prompt: str) -> str:
            prompts.append(prompt)
            return '{"match": "One Hour Massage", "confidence": "high"}'

    monkeypatch.setattr(sales_audit_module, "SheetsConnector", DummyConnector)
    monkeypatch.setenv("SALES_BOT_LLM_MATCHING", "1")

    audit = sales_audit_module.SalesAudit(
        config={
            "service_account_file": "ignored",
            "sheets": {sales_audit_module.DETAILS_KEY: {"spreadsheet_id": "dummy"}},
        },
        pricelist=DummyPriceList(),
    )

    is_valid, matched_name, suggestions = audit.validate_service("massage", llm=DummyLLM())

    assert is_valid is True
    assert matched_name == "One Hour Massage"
    assert suggestions == []
    assert prompts
    assert "Do not require an exact full-string match" in prompts[0]
    assert "If the staff writes `trans` or `trans to airport`" in prompts[0]


def test_service_exists_in_pricelist_falls_back_to_suggestions_without_llm() -> None:
    records = [
        {"Service": "One Hour Massage"},
        {"Service": "Hammam + Massage"},
    ]

    is_valid, matched_name, suggestions = sales_audit_module.service_exists_in_pricelist("massage", records)

    assert is_valid is False
    assert matched_name is None
    assert suggestions == [("One Hour Massage", 0.95), ("Hammam + Massage", 0.95)]
