from __future__ import annotations

from dataclasses import replace

from controlplane.control.memory.access_policy import MemoryAccessPolicy
from controlplane.control.memory.service import MemoryService
from controlplane.control.memory.types import MemoryEvent, MemoryItem, RecallRequest


class FakeDurableStore:
    def __init__(self) -> None:
        self.events: list[MemoryEvent] = []
        self.items: dict[str, MemoryItem] = {}

    def initialize(self) -> None:
        return

    def append_event(self, event: MemoryEvent) -> None:
        self.events.append(event)

    def list_recent_events(self, *, bot_name: str, conversation_id: str, limit: int) -> list[MemoryEvent]:
        matching = [e for e in self.events if e.bot_name == bot_name and e.conversation_id == conversation_id]
        return matching[-limit:]

    def save_item(self, item: MemoryItem) -> None:
        self.items[item.memory_id] = item

    def list_items(
        self,
        *,
        reader: str,
        layers: list[str],
        scope_ids: list[str],
        limit: int,
        only_active: bool = True,
    ) -> list[MemoryItem]:
        matching = [
            item
            for item in self.items.values()
            if reader in item.readers
            and item.layer in layers
            and item.scope_id in scope_ids
            and (not only_active or item.status == "active")
        ]
        matching.sort(key=lambda item: item.updated_at, reverse=True)
        return matching[:limit]

    def close_task(
        self,
        *,
        bot_name: str,
        conversation_id: str,
        task_type: str,
        status: str,
        resolution_note: str | None = None,
    ) -> None:
        for key, item in list(self.items.items()):
            if (
                item.created_by_bot == bot_name
                and item.layer == "task"
                and item.scope_id == conversation_id
                and item.title == task_type
                and item.status == "active"
            ):
                content = item.content if not resolution_note else item.content + f"\nResolution: {resolution_note}"
                self.items[key] = replace(item, status=status, content=content)


class FakeWorkingStore:
    def __init__(self) -> None:
        self.events: list[MemoryEvent] = []

    def append_event(self, event: MemoryEvent, *, max_events: int) -> None:
        self.events.append(event)
        self.events = self.events[-max_events:]

    def list_recent_events(self, *, bot_name: str, conversation_id: str, limit: int) -> list[MemoryEvent]:
        matching = [e for e in self.events if e.bot_name == bot_name and e.conversation_id == conversation_id]
        return matching[-limit:]


def test_querybot_recall_includes_shared_sales_learning() -> None:
    durable = FakeDurableStore()
    working = FakeWorkingStore()
    service = MemoryService(durable_store=durable, working_store=working, access_policy=MemoryAccessPolicy())

    service.record_event(
        MemoryEvent(
            bot_name="querybot",
            conversation_id="telegram:dm-1",
            chat_id="dm-1",
            role="user",
            text="What happened with recent corrections?",
        )
    )
    service.save_item(
        MemoryItem(
            memory_id="query-summary",
            owner_type="bot",
            owner_id="querybot",
            created_by_bot="querybot",
            layer="summary",
            scope_type="conversation",
            scope_id="telegram:dm-1",
            title="query summary",
            content="User is asking for follow-up context.",
            readers=["querybot"],
            writers=["querybot"],
        )
    )
    service.remember_sales_learning(
        title="Resolved service alias",
        content="`hamam vip` was corrected to `Hammam VIP` during sales correction.",
    )

    recall = service.recall(
        RecallRequest(
            bot_name="querybot",
            conversation_id="telegram:dm-1",
            chat_id="dm-1",
            query_text="What happened?",
        )
    )

    assert recall.summaries
    assert any("Hammam VIP" in item.content for item in recall.facts)


def test_salesbot_cannot_read_querybot_private_memory() -> None:
    durable = FakeDurableStore()
    service = MemoryService(durable_store=durable, working_store=FakeWorkingStore(), access_policy=MemoryAccessPolicy())

    service.save_item(
        MemoryItem(
            memory_id="query-private-fact",
            owner_type="bot",
            owner_id="querybot",
            created_by_bot="querybot",
            layer="semantic",
            scope_type="conversation",
            scope_id="telegram:sales-chat",
            title="query private fact",
            content="This should stay private to QueryBot.",
            readers=["querybot"],
            writers=["querybot"],
        )
    )

    recall = service.recall(
        RecallRequest(
            bot_name="salesbot",
            conversation_id="telegram:sales-chat",
            chat_id="sales-chat",
            query_text="extract sale",
        )
    )

    assert not recall.facts


def test_refresh_summary_persists_compact_conversation_summary() -> None:
    durable = FakeDurableStore()
    working = FakeWorkingStore()
    service = MemoryService(durable_store=durable, working_store=working, access_policy=MemoryAccessPolicy())

    service.record_event(
        MemoryEvent(
            bot_name="querybot",
            conversation_id="telegram:dm-2",
            chat_id="dm-2",
            role="user",
            text="Show me sales for Sahara today.",
        )
    )
    service.record_event(
        MemoryEvent(
            bot_name="querybot",
            conversation_id="telegram:dm-2",
            chat_id="dm-2",
            role="assistant",
            text="I found 3 Sahara sales for today.",
        )
    )

    service.refresh_summary(bot_name="querybot", conversation_id="telegram:dm-2", chat_id="dm-2")

    summary = durable.items["summary:querybot:telegram:dm-2"]
    assert "Recent user focus" in summary.content
    assert "Sahara" in summary.content


def test_recall_prefers_working_memory_for_recent_turns() -> None:
    durable = FakeDurableStore()
    working = FakeWorkingStore()
    service = MemoryService(durable_store=durable, working_store=working, access_policy=MemoryAccessPolicy())

    durable.append_event(
        MemoryEvent(
            bot_name="querybot",
            conversation_id="telegram:dm-3",
            chat_id="dm-3",
            role="user",
            text="older durable event",
        )
    )
    service.record_event(
        MemoryEvent(
            bot_name="querybot",
            conversation_id="telegram:dm-3",
            chat_id="dm-3",
            role="assistant",
            text="fresh working event",
        )
    )

    recall = service.recall(
        RecallRequest(
            bot_name="querybot",
            conversation_id="telegram:dm-3",
            chat_id="dm-3",
            query_text="continue",
        )
    )

    assert recall.recent_events[-1].text == "fresh working event"


def test_open_and_close_task_round_trip() -> None:
    durable = FakeDurableStore()
    service = MemoryService(durable_store=durable, working_store=FakeWorkingStore(), access_policy=MemoryAccessPolicy())

    service.open_task(
        bot_name="salesbot",
        conversation_id="telegram:sales-1",
        chat_id="sales-1",
        task_type="salesbot_correction_pending",
        content="Awaiting correction",
        metadata={"field": "service"},
    )
    service.close_task(
        bot_name="salesbot",
        conversation_id="telegram:sales-1",
        task_type="salesbot_correction_pending",
        status="resolved",
        resolution_note="User supplied the missing service.",
    )

    task = durable.items["task:salesbot:telegram:sales-1:salesbot_correction_pending"]
    assert task.status == "resolved"
    assert "Resolution: User supplied the missing service." in task.content


def test_sales_learning_and_episode_are_shared_with_querybot() -> None:
    durable = FakeDurableStore()
    service = MemoryService(durable_store=durable, working_store=FakeWorkingStore(), access_policy=MemoryAccessPolicy())

    service.remember_sales_learning(
        title="Hotel alias",
        content="`Sahara room` often maps to `The Sahara Room`.",
    )
    service.remember_sales_correction_episode(
        conversation_id="telegram:sales-2",
        chat_id="sales-2",
        title="Resolved correction",
        content="User corrected an unknown room name during service confirmation.",
    )

    facts = [item for item in durable.items.values() if item.layer == "semantic"]
    episodes = [item for item in durable.items.values() if item.layer == "episodic"]

    assert facts and facts[0].readers == ["salesbot", "querybot"]
    assert episodes and episodes[0].owner_id == "common"
