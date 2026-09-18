from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import asyncpg


@dataclass(frozen=True)
class SyncState:
    source_uid: str
    google_event_id: str
    last_payload_hash: str
    status: str
    source_payload: dict[str, Any] | None


async def create_connection(database_url: str) -> asyncpg.Connection:
    connection = await asyncpg.connect(database_url)
    await ensure_schema(connection)
    return connection


async def ensure_schema(connection: asyncpg.Connection):
    await connection.execute(
        """
        CREATE TABLE IF NOT EXISTS synced_events (
            source_uid TEXT PRIMARY KEY,
            google_event_id TEXT NOT NULL,
            last_payload_hash TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('active', 'deleted_by_user', 'deleted_from_source')),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    )
    await connection.execute("ALTER TABLE synced_events ADD COLUMN IF NOT EXISTS source_payload JSONB")


async def load_states(connection: asyncpg.Connection) -> dict[str, SyncState]:
    rows = await connection.fetch(
        "SELECT source_uid, google_event_id, last_payload_hash, status, source_payload FROM synced_events",
    )

    def decode_payload(value):
        return json.loads(value) if isinstance(value, str) else value

    return {
        row["source_uid"]: SyncState(
            source_uid=row["source_uid"],
            google_event_id=row["google_event_id"],
            last_payload_hash=row["last_payload_hash"],
            status=row["status"],
            source_payload=decode_payload(row["source_payload"]),
        )
        for row in rows
    }


async def upsert_state(
    connection: asyncpg.Connection,
    source_uid: str,
    google_event_id: str,
    source_event,
    status: str,
    *,
    payload_hash: str | None = None,
):
    if source_event is None:
        source_payload = None
    elif isinstance(source_event, dict):
        source_payload = source_event
    else:
        from google_calendar_sync import source_payload_for_event

        source_payload = source_payload_for_event(source_event)
        payload_hash = source_event.payload_hash

    if payload_hash is None:
        raise ValueError("payload_hash is required when source_event is not a SyncEvent")

    await connection.execute(
        """
        INSERT INTO synced_events (source_uid, google_event_id, last_payload_hash, status, source_payload)
        VALUES ($1, $2, $3, $4, $5::jsonb)
        ON CONFLICT (source_uid)
        DO UPDATE SET
            google_event_id = EXCLUDED.google_event_id,
            last_payload_hash = EXCLUDED.last_payload_hash,
            status = EXCLUDED.status,
            source_payload = EXCLUDED.source_payload,
            updated_at = NOW()
        """,
        source_uid,
        google_event_id,
        payload_hash,
        status,
        json.dumps(source_payload, ensure_ascii=False) if source_payload is not None else None,
    )


async def try_acquire_sync_lock(connection: asyncpg.Connection) -> bool:
    return bool(await connection.fetchval("SELECT pg_try_advisory_lock(hashtext('itmo-google-calendar-sync'))"))


async def release_sync_lock(connection: asyncpg.Connection) -> None:
    await connection.execute("SELECT pg_advisory_unlock(hashtext('itmo-google-calendar-sync'))")
