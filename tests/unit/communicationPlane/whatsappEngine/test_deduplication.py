"""Unit tests for the in-memory deduplication store."""

from __future__ import annotations

import pytest

from communicationPlane.whatsappEngine.deduplication.in_memory_store import (
    InMemoryDeduplicator as DevInMemoryDeduplicator,
)
from models import deduplication as dedup_module


def test_in_memory_deduplicator_marks_duplicates() -> None:
    """Mark a repeated key as a duplicate."""
    dedup = dedup_module.InMemoryDeduplicator()
    assert dedup.is_duplicate("alpha") is False
    assert dedup.is_duplicate("alpha") is True


def test_in_memory_deduplicator_reset_clears_store() -> None:
    """Reset clears all stored deduplication keys."""
    dedup = dedup_module.InMemoryDeduplicator()
    assert dedup.is_duplicate("alpha") is False
    dedup.reset()
    assert dedup.is_duplicate("alpha") is False


def test_in_memory_deduplicator_ttl_expiry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Expire keys after TTL elapses."""
    times = iter([0.0, 2.0])
    monkeypatch.setattr(dedup_module.time, "time", lambda: next(times))
    dedup = dedup_module.InMemoryDeduplicator(ttl_seconds=1.0)
    assert dedup.is_duplicate("alpha") is False
    assert dedup.is_duplicate("alpha") is False


def test_in_memory_deduplicator_zero_ttl_never_expires(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Treat TTL=0 as 'never expire' while retaining duplicates."""
    times = iter([0.0, 100.0])
    monkeypatch.setattr(dedup_module.time, "time", lambda: next(times))
    dedup = dedup_module.InMemoryDeduplicator(ttl_seconds=0.0)
    assert dedup.is_duplicate("alpha") is False
    assert dedup.is_duplicate("alpha") is True


def test_in_memory_deduplicator_eviction_when_full(monkeypatch: pytest.MonkeyPatch) -> None:
    """Evict the oldest key when max_entries is reached."""
    times = iter([0.0, 1.0, 2.0, 3.0])
    monkeypatch.setattr(dedup_module.time, "time", lambda: next(times))
    dedup = dedup_module.InMemoryDeduplicator(ttl_seconds=100.0, max_entries=2)
    assert dedup.is_duplicate("alpha") is False
    assert dedup.is_duplicate("bravo") is False
    assert dedup.is_duplicate("charlie") is False
    assert dedup.is_duplicate("alpha") is False


def test_dev_in_memory_store_alias() -> None:
    """Ensure the communicationPlane alias points to the shared implementation."""
    dedup = DevInMemoryDeduplicator()
    assert isinstance(dedup, dedup_module.InMemoryDeduplicator)
