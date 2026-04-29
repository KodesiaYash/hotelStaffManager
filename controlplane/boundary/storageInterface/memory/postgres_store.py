from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from typing import Any

from psycopg import Connection, connect
from psycopg.rows import dict_row

from controlplane.control.memory.types import MemoryEvent, MemoryItem
from shared.logging_context import LogContext

logger = logging.getLogger(__name__)


class PostgresMemoryStore:
    def __init__(self) -> None:
        self._dsn = self._build_dsn()
        self._initialized = False

    def _build_dsn(self) -> str:
        host = os.getenv("MEMORY_DB_HOST", "memory-postgres")
        port = os.getenv("MEMORY_DB_PORT", "5432")
        dbname = os.getenv("MEMORY_DB_NAME", "hotel_staff_manager")
        user = os.getenv("MEMORY_DB_USER", "hotelstaffmanager")
        password = os.getenv("MEMORY_DB_PASSWORD", "hotelstaffmanager")
        sslmode = os.getenv("MEMORY_DB_SSLMODE", "disable")
        return (
            f"host={host} port={port} dbname={dbname} user={user} "
            f"password={password} sslmode={sslmode}"
        )

    def _connect(self) -> Connection[Any]:
        return connect(self._dsn, row_factory=dict_row)

    def initialize(self) -> None:
        if self._initialized:
            return
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_events (
                    id TEXT PRIMARY KEY,
                    bot_name TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    user_id TEXT NULL,
                    sender_name TEXT NULL,
                    role TEXT NOT NULL,
                    text_content TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    layer TEXT NOT NULL,
                    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                    created_at TIMESTAMPTZ NOT NULL
                );
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_memory_events_bot_conversation_created
                ON memory_events (bot_name, conversation_id, created_at DESC);
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_items (
                    id TEXT PRIMARY KEY,
                    owner_type TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    created_by_bot TEXT NOT NULL,
                    layer TEXT NOT NULL,
                    scope_type TEXT NOT NULL,
                    scope_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    readers JSONB NOT NULL DEFAULT '[]'::jsonb,
                    writers JSONB NOT NULL DEFAULT '[]'::jsonb,
                    status TEXT NOT NULL DEFAULT 'active',
                    confidence DOUBLE PRECISION NOT NULL DEFAULT 1.0,
                    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                    source_event_id TEXT NULL,
                    created_at TIMESTAMPTZ NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL,
                    expires_at TIMESTAMPTZ NULL
                );
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_memory_items_reader_layer_scope_updated
                ON memory_items (layer, scope_id, updated_at DESC);
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_memory_items_readers_gin
                ON memory_items USING GIN (readers);
                """
            )
        self._initialized = True

    def append_event(self, event: MemoryEvent) -> None:
        self.initialize()
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO memory_events (
                    id, bot_name, conversation_id, chat_id, user_id, sender_name,
                    role, text_content, event_type, layer, metadata, created_at
                ) VALUES (
                    %(id)s, %(bot_name)s, %(conversation_id)s, %(chat_id)s, %(user_id)s, %(sender_name)s,
                    %(role)s, %(text_content)s, %(event_type)s, %(layer)s, %(metadata)s::jsonb, %(created_at)s
                )
                ON CONFLICT (id) DO NOTHING;
                """,
                {
                    "id": event.event_id,
                    "bot_name": event.bot_name,
                    "conversation_id": event.conversation_id,
                    "chat_id": event.chat_id,
                    "user_id": event.user_id,
                    "sender_name": event.sender_name,
                    "role": event.role,
                    "text_content": event.text,
                    "event_type": event.event_type,
                    "layer": event.layer,
                    "metadata": json.dumps(event.metadata),
                    "created_at": event.created_at,
                },
            )
            self._log_sql_operation(
                operation="create",
                entity="memory_events",
                action="append_event",
                conversation_id=event.conversation_id,
                chat_id=event.chat_id,
                bot_name=event.bot_name,
                row_count=cur.rowcount,
            )

    def list_recent_events(self, *, bot_name: str, conversation_id: str, limit: int) -> list[MemoryEvent]:
        self.initialize()
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT *
                FROM memory_events
                WHERE bot_name = %(bot_name)s AND conversation_id = %(conversation_id)s
                ORDER BY created_at DESC
                LIMIT %(limit)s;
                """,
                {
                    "bot_name": bot_name,
                    "conversation_id": conversation_id,
                    "limit": limit,
                },
            )
            rows = list(reversed(cur.fetchall()))
        self._log_sql_operation(
            operation="read",
            entity="memory_events",
            action="list_recent_events",
            conversation_id=conversation_id,
            bot_name=bot_name,
            result_count=len(rows),
            limit=limit,
        )
        return [self._event_from_row(row) for row in rows]

    def save_item(self, item: MemoryItem) -> None:
        self.initialize()
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO memory_items (
                    id, owner_type, owner_id, created_by_bot, layer, scope_type, scope_id,
                    title, content, readers, writers, status, confidence, metadata,
                    source_event_id, created_at, updated_at, expires_at
                ) VALUES (
                    %(id)s, %(owner_type)s, %(owner_id)s, %(created_by_bot)s, %(layer)s, %(scope_type)s, %(scope_id)s,
                    %(title)s, %(content)s, %(readers)s::jsonb, %(writers)s::jsonb, %(status)s, %(confidence)s,
                    %(metadata)s::jsonb, %(source_event_id)s, %(created_at)s, %(updated_at)s, %(expires_at)s
                )
                ON CONFLICT (id) DO UPDATE SET
                    owner_type = EXCLUDED.owner_type,
                    owner_id = EXCLUDED.owner_id,
                    created_by_bot = EXCLUDED.created_by_bot,
                    layer = EXCLUDED.layer,
                    scope_type = EXCLUDED.scope_type,
                    scope_id = EXCLUDED.scope_id,
                    title = EXCLUDED.title,
                    content = EXCLUDED.content,
                    readers = EXCLUDED.readers,
                    writers = EXCLUDED.writers,
                    status = EXCLUDED.status,
                    confidence = EXCLUDED.confidence,
                    metadata = EXCLUDED.metadata,
                    source_event_id = EXCLUDED.source_event_id,
                    updated_at = EXCLUDED.updated_at,
                    expires_at = EXCLUDED.expires_at;
                """,
                {
                    "id": item.memory_id,
                    "owner_type": item.owner_type,
                    "owner_id": item.owner_id,
                    "created_by_bot": item.created_by_bot,
                    "layer": item.layer,
                    "scope_type": item.scope_type,
                    "scope_id": item.scope_id,
                    "title": item.title,
                    "content": item.content,
                    "readers": json.dumps(item.readers),
                    "writers": json.dumps(item.writers),
                    "status": item.status,
                    "confidence": item.confidence,
                    "metadata": json.dumps(item.metadata),
                    "source_event_id": item.source_event_id,
                    "created_at": item.created_at,
                    "updated_at": item.updated_at,
                    "expires_at": item.expires_at,
                },
            )
            self._log_sql_operation(
                operation="update",
                entity="memory_items",
                action="save_item",
                conversation_id=item.scope_id if item.scope_type == "conversation" else None,
                bot_name=item.created_by_bot,
                memory_id=item.memory_id,
                layer=item.layer,
                scope_id=item.scope_id,
                row_count=cur.rowcount,
            )

    def list_items(
        self,
        *,
        reader: str,
        layers: list[str],
        scope_ids: list[str],
        limit: int,
        only_active: bool = True,
    ) -> list[MemoryItem]:
        self.initialize()
        if not layers or not scope_ids or limit <= 0:
            return []
        where_status = "AND status = 'active'" if only_active else ""
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT *
                FROM memory_items
                WHERE readers ? %(reader)s
                  AND layer = ANY(%(layers)s)
                  AND scope_id = ANY(%(scope_ids)s)
                  {where_status}
                ORDER BY updated_at DESC
                LIMIT %(limit)s;
                """,
                {
                    "reader": reader,
                    "layers": layers,
                    "scope_ids": scope_ids,
                    "limit": limit,
                },
            )
            rows = cur.fetchall()
        self._log_sql_operation(
            operation="read",
            entity="memory_items",
            action="list_items",
            reader=reader,
            layers=layers,
            scope_ids=scope_ids,
            result_count=len(rows),
            limit=limit,
            only_active=only_active,
        )
        return [self._item_from_row(row) for row in rows]

    def close_task(
        self,
        *,
        bot_name: str,
        conversation_id: str,
        task_type: str,
        status: str,
        resolution_note: str | None = None,
    ) -> None:
        self.initialize()
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE memory_items
                SET status = %(status)s,
                    updated_at = %(updated_at)s,
                    content = CASE
                        WHEN %(resolution_note)s IS NULL OR %(resolution_note)s = '' THEN content
                        ELSE content || E'\nResolution: ' || %(resolution_note)s
                    END
                WHERE created_by_bot = %(bot_name)s
                  AND layer = 'task'
                  AND scope_id = %(conversation_id)s
                  AND title = %(task_title)s
                  AND status = 'active';
                """,
                {
                    "status": status,
                    "updated_at": datetime.now(UTC),
                    "resolution_note": resolution_note,
                    "bot_name": bot_name,
                    "conversation_id": conversation_id,
                    "task_title": task_type,
                },
            )
            self._log_sql_operation(
                operation="update",
                entity="memory_items",
                action="close_task",
                conversation_id=conversation_id,
                bot_name=bot_name,
                task_type=task_type,
                status=status,
                row_count=cur.rowcount,
            )

    def _log_sql_operation(self, *, operation: str, entity: str, action: str, **details: Any) -> None:
        with LogContext(source="sql"):
            logger.info(
                "Postgres memory operation action=%s entity=%s operation=%s",
                action,
                entity,
                operation,
                extra={
                    "db": "postgres",
                    "operation": operation,
                    "entity": entity,
                    "action": action,
                    **{key: value for key, value in details.items() if value is not None},
                },
            )

    def _event_from_row(self, row: dict[str, Any]) -> MemoryEvent:
        return MemoryEvent(
            event_id=row["id"],
            bot_name=row["bot_name"],
            conversation_id=row["conversation_id"],
            chat_id=row["chat_id"],
            user_id=row.get("user_id"),
            sender_name=row.get("sender_name"),
            role=row["role"],
            text=row["text_content"],
            event_type=row["event_type"],
            layer=row["layer"],
            metadata=row.get("metadata") or {},
            created_at=self._ensure_datetime(row["created_at"]),
        )

    def _item_from_row(self, row: dict[str, Any]) -> MemoryItem:
        return MemoryItem(
            memory_id=row["id"],
            owner_type=row["owner_type"],
            owner_id=row["owner_id"],
            created_by_bot=row["created_by_bot"],
            layer=row["layer"],
            scope_type=row["scope_type"],
            scope_id=row["scope_id"],
            title=row["title"],
            content=row["content"],
            readers=list(row.get("readers") or []),
            writers=list(row.get("writers") or []),
            status=row["status"],
            confidence=float(row["confidence"]),
            metadata=row.get("metadata") or {},
            source_event_id=row.get("source_event_id"),
            created_at=self._ensure_datetime(row["created_at"]),
            updated_at=self._ensure_datetime(row["updated_at"]),
            expires_at=self._ensure_datetime(row["expires_at"]) if row.get("expires_at") else None,
        )

    def _ensure_datetime(self, value: Any) -> datetime:
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            return datetime.fromisoformat(value)
        return datetime.now(UTC)
