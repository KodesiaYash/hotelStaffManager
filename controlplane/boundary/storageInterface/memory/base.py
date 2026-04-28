from __future__ import annotations

from typing import Protocol

from controlplane.control.memory.types import MemoryEvent, MemoryItem


class DurableMemoryStore(Protocol):
    def initialize(self) -> None: ...

    def append_event(self, event: MemoryEvent) -> None: ...

    def list_recent_events(self, *, bot_name: str, conversation_id: str, limit: int) -> list[MemoryEvent]: ...

    def save_item(self, item: MemoryItem) -> None: ...

    def list_items(
        self,
        *,
        reader: str,
        layers: list[str],
        scope_ids: list[str],
        limit: int,
        only_active: bool = True,
    ) -> list[MemoryItem]: ...

    def close_task(
        self,
        *,
        bot_name: str,
        conversation_id: str,
        task_type: str,
        status: str,
        resolution_note: str | None = None,
    ) -> None: ...


class WorkingMemoryStore(Protocol):
    def append_event(self, event: MemoryEvent, *, max_events: int) -> None: ...

    def list_recent_events(self, *, bot_name: str, conversation_id: str, limit: int) -> list[MemoryEvent]: ...
