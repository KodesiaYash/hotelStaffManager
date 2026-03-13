from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Protocol


class Deduplicator(Protocol):
    def is_duplicate(self, key: str) -> bool: ...


@dataclass
class InMemoryDeduplicator:
    ttl_seconds: float = 3600.0
    max_entries: int = 10000
    _store: dict[str, float] = field(default_factory=dict, init=False)

    def is_duplicate(self, key: str) -> bool:
        now = time.time()
        self._cleanup(now)
        if key in self._store:
            return True
        if len(self._store) >= self.max_entries:
            self._evict_oldest()
        self._store[key] = now
        return False

    def reset(self) -> None:
        self._store.clear()

    def _cleanup(self, now: float) -> None:
        if self.ttl_seconds <= 0:
            return
        expired = [k for k, ts in self._store.items() if now - ts > self.ttl_seconds]
        for key in expired:
            self._store.pop(key, None)

    def _evict_oldest(self) -> None:
        if not self._store:
            return
        oldest_key = min(self._store, key=self._store.get)
        self._store.pop(oldest_key, None)
