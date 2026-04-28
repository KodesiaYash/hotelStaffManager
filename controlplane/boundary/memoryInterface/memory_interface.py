from __future__ import annotations

from typing import Protocol

from controlplane.control.memory.types import MemoryEvent, MemoryItem, RecallBundle, RecallRequest


class MemoryInterface(Protocol):
    def record_event(self, event: MemoryEvent, *, cache_working_memory: bool = True) -> None: ...

    def save_item(self, item: MemoryItem) -> None: ...

    def close_task(
        self,
        *,
        bot_name: str,
        conversation_id: str,
        task_type: str,
        status: str,
        resolution_note: str | None = None,
    ) -> None: ...

    def refresh_summary(self, *, bot_name: str, conversation_id: str, chat_id: str) -> None: ...

    def recall(self, request: RecallRequest) -> RecallBundle: ...
