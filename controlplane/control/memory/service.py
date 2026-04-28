from __future__ import annotations

import logging
import os
from typing import Any

from controlplane.boundary.memoryInterface.memory_interface import MemoryInterface
from controlplane.boundary.storageInterface.memory.base import DurableMemoryStore, WorkingMemoryStore
from controlplane.control.memory.access_policy import MemoryAccessPolicy
from controlplane.control.memory.profiles import get_profile
from controlplane.control.memory.summary_strategy import DeterministicSummaryStrategy
from controlplane.control.memory.types import (
    MemoryEvent,
    MemoryItem,
    RecallBundle,
    RecallRequest,
    new_memory_id,
    utc_now,
)

logger = logging.getLogger(__name__)

SUMMARY_SCOPE_ID_PREFIX = "summary"


class MemoryService(MemoryInterface):
    def __init__(
        self,
        *,
        durable_store: DurableMemoryStore | None = None,
        working_store: WorkingMemoryStore | None = None,
        access_policy: MemoryAccessPolicy | None = None,
        summary_strategy: DeterministicSummaryStrategy | None = None,
    ) -> None:
        if durable_store is None:
            from controlplane.boundary.storageInterface.memory.postgres_store import PostgresMemoryStore

            durable_store = PostgresMemoryStore()
        if working_store is None:
            from controlplane.boundary.storageInterface.memory.redis_store import RedisWorkingMemoryStore

            working_store = RedisWorkingMemoryStore()
        self._durable_store = durable_store
        self._working_store = working_store
        self._access_policy = access_policy or MemoryAccessPolicy()
        self._summary_strategy = summary_strategy or DeterministicSummaryStrategy()
        self._working_max_events = int(os.getenv("MEMORY_WORKING_MAX_EVENTS", "24"))

    def record_event(self, event: MemoryEvent, *, cache_working_memory: bool = True) -> None:
        self._durable_store.append_event(event)
        if cache_working_memory:
            try:
                self._working_store.append_event(event, max_events=self._working_max_events)
            except Exception as exc:
                logger.warning("Working memory append failed: %s", exc)

    def save_item(self, item: MemoryItem) -> None:
        self._durable_store.save_item(item)

    def close_task(
        self,
        *,
        bot_name: str,
        conversation_id: str,
        task_type: str,
        status: str,
        resolution_note: str | None = None,
    ) -> None:
        self._durable_store.close_task(
            bot_name=bot_name,
            conversation_id=conversation_id,
            task_type=task_type,
            status=status,
            resolution_note=resolution_note,
        )

    def refresh_summary(self, *, bot_name: str, conversation_id: str, chat_id: str) -> None:
        recent_events = self._durable_store.list_recent_events(
            bot_name=bot_name,
            conversation_id=conversation_id,
            limit=12,
        )
        tasks = self._durable_store.list_items(
            reader=bot_name,
            layers=["task"],
            scope_ids=[conversation_id],
            limit=6,
            only_active=True,
        )
        facts = self._durable_store.list_items(
            reader=bot_name,
            layers=["semantic"],
            scope_ids=[conversation_id, chat_id],
            limit=6,
            only_active=True,
        )
        content = self._summary_strategy.build_summary(
            bot_name=bot_name,
            conversation_id=conversation_id,
            chat_id=chat_id,
            recent_events=recent_events,
            tasks=tasks,
            facts=facts,
        )
        summary_id = f"{SUMMARY_SCOPE_ID_PREFIX}:{bot_name}:{conversation_id}"
        self.save_item(
            MemoryItem(
                memory_id=summary_id,
                owner_type="bot",
                owner_id=bot_name,
                created_by_bot=bot_name,
                layer="summary",
                scope_type="conversation",
                scope_id=conversation_id,
                title=f"{bot_name} summary",
                content=content,
                readers=self._access_policy.private_readers(bot_name),  # type: ignore[arg-type]
                writers=[bot_name],
                metadata={"chat_id": chat_id},
                updated_at=utc_now(),
            )
        )

    def recall(self, request: RecallRequest) -> RecallBundle:
        profile = get_profile(request.bot_name)
        recent_events = self._recent_events_for_request(request)
        scope_ids = self._scope_ids_for_request(request)
        summaries = self._durable_store.list_items(
            reader=request.bot_name,
            layers=list(profile.summary_layers),
            scope_ids=scope_ids,
            limit=request.summary_limit,
            only_active=True,
        )
        facts = self._durable_store.list_items(
            reader=request.bot_name,
            layers=list(profile.fact_layers),
            scope_ids=[*scope_ids, "common:salesbot"],
            limit=request.fact_limit,
            only_active=True,
        )
        tasks = self._durable_store.list_items(
            reader=request.bot_name,
            layers=list(profile.task_layers),
            scope_ids=scope_ids,
            limit=request.task_limit,
            only_active=True,
        )
        episodes = self._durable_store.list_items(
            reader=request.bot_name,
            layers=list(profile.episode_layers),
            scope_ids=[*scope_ids, "common:salesbot", "organization:default"],
            limit=request.episode_limit,
            only_active=True,
        )
        return RecallBundle(
            recent_events=recent_events,
            summaries=summaries,
            facts=facts,
            tasks=tasks,
            episodes=episodes,
        )

    def open_task(
        self,
        *,
        bot_name: str,
        conversation_id: str,
        chat_id: str,
        task_type: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.save_item(
            MemoryItem(
                memory_id=f"task:{bot_name}:{conversation_id}:{task_type}",
                owner_type="bot",
                owner_id=bot_name,
                created_by_bot=bot_name,
                layer="task",
                scope_type="conversation",
                scope_id=conversation_id,
                title=task_type,
                content=content,
                readers=self._access_policy.private_readers(bot_name),  # type: ignore[arg-type]
                writers=[bot_name],
                metadata={"chat_id": chat_id, **(metadata or {})},
                updated_at=utc_now(),
            )
        )

    def remember_sales_correction_episode(
        self,
        *,
        conversation_id: str,
        chat_id: str,
        title: str,
        content: str,
        metadata: dict[str, Any] | None = None,
        source_event_id: str | None = None,
    ) -> None:
        readers = self._access_policy.common_readers_for_sales_learning()
        self.save_item(
            MemoryItem(
                memory_id=new_memory_id("episode"),
                owner_type="shared",
                owner_id="common",
                created_by_bot="salesbot",
                layer="episodic",
                scope_type="organization",
                scope_id="common:salesbot",
                title=title,
                content=content,
                readers=readers,
                writers=["salesbot"],
                metadata={"conversation_id": conversation_id, "chat_id": chat_id, **(metadata or {})},
                source_event_id=source_event_id,
            )
        )

    def remember_sales_learning(
        self,
        *,
        title: str,
        content: str,
        metadata: dict[str, Any] | None = None,
        source_event_id: str | None = None,
    ) -> None:
        readers = self._access_policy.common_readers_for_sales_learning()
        self.save_item(
            MemoryItem(
                memory_id=new_memory_id("fact"),
                owner_type="shared",
                owner_id="common",
                created_by_bot="salesbot",
                layer="semantic",
                scope_type="organization",
                scope_id="common:salesbot",
                title=title,
                content=content,
                readers=readers,
                writers=["salesbot"],
                metadata=metadata or {},
                source_event_id=source_event_id,
            )
        )

    def _recent_events_for_request(self, request: RecallRequest) -> list[MemoryEvent]:
        try:
            events = self._working_store.list_recent_events(
                bot_name=request.bot_name,
                conversation_id=request.conversation_id,
                limit=request.recent_event_limit,
            )
            if events:
                return events
        except Exception as exc:
            logger.warning("Working memory recall failed: %s", exc)
        return self._durable_store.list_recent_events(
            bot_name=request.bot_name,
            conversation_id=request.conversation_id,
            limit=request.recent_event_limit,
        )

    def _scope_ids_for_request(self, request: RecallRequest) -> list[str]:
        scope_ids = [request.conversation_id, request.chat_id, "organization:default"]
        if request.user_id:
            scope_ids.append(request.user_id)
        return scope_ids


_memory_service: MemoryService | None = None


def get_memory_service() -> MemoryService:
    global _memory_service
    if _memory_service is None:
        _memory_service = MemoryService()
    return _memory_service
