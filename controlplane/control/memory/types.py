from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

BotName = Literal["salesbot", "querybot"]
EventRole = Literal["user", "assistant", "system"]
MemoryLayer = Literal["event", "working", "summary", "semantic", "task", "episodic"]
OwnerType = Literal["bot", "shared", "system"]
ScopeType = Literal["conversation", "chat", "user", "staff", "hotel", "organization", "global"]


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_memory_id(prefix: str) -> str:
    return f"{prefix}:{uuid4()}"


@dataclass(frozen=True)
class MemoryEvent:
    bot_name: BotName
    conversation_id: str
    chat_id: str
    role: EventRole
    text: str
    event_type: str = "message"
    layer: MemoryLayer = "event"
    user_id: str | None = None
    sender_name: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utc_now)
    event_id: str = field(default_factory=lambda: new_memory_id("event"))


@dataclass(frozen=True)
class MemoryItem:
    layer: MemoryLayer
    scope_type: ScopeType
    scope_id: str
    title: str
    content: str
    readers: list[str]
    writers: list[str]
    owner_type: OwnerType = "shared"
    owner_id: str = "common"
    created_by_bot: str = "system"
    status: str = "active"
    confidence: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)
    source_event_id: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    expires_at: datetime | None = None
    memory_id: str = field(default_factory=lambda: new_memory_id("memory"))


@dataclass(frozen=True)
class RecallRequest:
    bot_name: BotName
    conversation_id: str
    chat_id: str
    query_text: str
    user_id: str | None = None
    sender_name: str | None = None
    recent_event_limit: int = 8
    summary_limit: int = 2
    fact_limit: int = 8
    task_limit: int = 5
    episode_limit: int = 6


@dataclass
class RecallBundle:
    recent_events: list[MemoryEvent] = field(default_factory=list)
    summaries: list[MemoryItem] = field(default_factory=list)
    facts: list[MemoryItem] = field(default_factory=list)
    tasks: list[MemoryItem] = field(default_factory=list)
    episodes: list[MemoryItem] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not any([self.recent_events, self.summaries, self.facts, self.tasks, self.episodes])

    def to_markdown(self) -> str:
        sections: list[str] = []
        if self.summaries:
            sections.append("## Conversation Summary")
            for item in self.summaries:
                sections.append(f"- {item.content}")
        if self.tasks:
            sections.append("## Active Tasks")
            for item in self.tasks:
                sections.append(f"- {item.content}")
        if self.facts:
            sections.append("## Learned Facts")
            for item in self.facts:
                sections.append(f"- {item.content}")
        if self.episodes:
            sections.append("## Shared History")
            for item in self.episodes:
                sections.append(f"- {item.content}")
        if self.recent_events:
            sections.append("## Recent Turns")
            for event in self.recent_events:
                speaker = "User" if event.role == "user" else "Assistant" if event.role == "assistant" else "System"
                sections.append(f"- {speaker}: {event.text}")
        return "\n".join(sections).strip()
