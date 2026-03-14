"""Unit tests for shared utility helpers."""

from __future__ import annotations

from shared.utils import safe_json_parse


def test_safe_json_parse_handles_empty() -> None:
    """Return an explicit error payload for empty strings."""
    assert safe_json_parse("") == {"error": "empty_response"}


def test_safe_json_parse_strips_json_fences() -> None:
    """Strip fenced code blocks and parse JSON payloads."""
    raw = """```json\n{\"Service\": \"Spa\"}\n```"""
    assert safe_json_parse(raw) == {"Service": "Spa"}
