from __future__ import annotations

import json
import logging
import os
from datetime import datetime

from redis import Redis

from controlplane.control.memory.types import MemoryEvent, utc_now

logger = logging.getLogger(__name__)


class RedisWorkingMemoryStore:
    def __init__(self) -> None:
        self._client = Redis.from_url(self._redis_url(), decode_responses=True)

    def _redis_url(self) -> str:
        explicit_url = os.getenv("MEMORY_REDIS_URL")
        if explicit_url:
            return explicit_url
        host = os.getenv("MEMORY_REDIS_HOST", "memory-redis")
        port = os.getenv("MEMORY_REDIS_PORT", "6379")
        db = os.getenv("MEMORY_REDIS_DB", "0")
        password = os.getenv("MEMORY_REDIS_PASSWORD") or os.getenv("REDIS_PASSWORD")
        auth = f":{password}@" if password else ""
        return f"redis://{auth}{host}:{port}/{db}"

    def append_event(self, event: MemoryEvent, *, max_events: int) -> None:
        key = self._key(event.bot_name, event.conversation_id)
        payload = {
            "event_id": event.event_id,
            "bot_name": event.bot_name,
            "conversation_id": event.conversation_id,
            "chat_id": event.chat_id,
            "user_id": event.user_id,
            "sender_name": event.sender_name,
            "role": event.role,
            "text": event.text,
            "event_type": event.event_type,
            "layer": event.layer,
            "metadata": event.metadata,
            "created_at": event.created_at.isoformat(),
        }
        pipe = self._client.pipeline()
        pipe.rpush(key, json.dumps(payload))
        pipe.ltrim(key, -max_events, -1)
        pipe.execute()

    def list_recent_events(self, *, bot_name: str, conversation_id: str, limit: int) -> list[MemoryEvent]:
        key = self._key(bot_name, conversation_id)
        raw_events = self._client.lrange(key, max(0, -limit), -1)
        events: list[MemoryEvent] = []
        for raw in raw_events:
            try:
                data = json.loads(raw)
                events.append(
                    MemoryEvent(
                        event_id=data["event_id"],
                        bot_name=data["bot_name"],
                        conversation_id=data["conversation_id"],
                        chat_id=data["chat_id"],
                        user_id=data.get("user_id"),
                        sender_name=data.get("sender_name"),
                        role=data["role"],
                        text=data["text"],
                        event_type=data.get("event_type", "message"),
                        layer=data.get("layer", "event"),
                        metadata=data.get("metadata") or {},
                        created_at=(
                            utc_now()
                            if not data.get("created_at")
                            else datetime.fromisoformat(data["created_at"])
                        ),
                    )
                )
            except Exception as exc:
                logger.warning("Failed to decode working memory event: %s", exc)
        return events

    def _key(self, bot_name: str, conversation_id: str) -> str:
        return f"memory:working:{bot_name}:{conversation_id}"
