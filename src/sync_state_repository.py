from __future__ import annotations

from dataclasses import dataclass

import asyncpg


@dataclass(frozen=True)
class SyncState:
    source_uid: str
    google_event_id: str
    last_payload_hash: str
    status: str


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


async def load_states(connection: asyncpg.Connection) -> dict[str, SyncState]:
    rows = await connection.fetch(
        "SELECT source_uid, google_event_id, last_payload_hash, status FROM synced_events",
    )

    return {
        row["source_uid"]: SyncState(
            source_uid=row["source_uid"],
            google_event_id=row["google_event_id"],
            last_payload_hash=row["last_payload_hash"],
            status=row["status"],
        )
        for row in rows
    }


async def upsert_state(
    connection: asyncpg.Connection,
    source_uid: str,
    google_event_id: str,
    payload_hash: str,
    status: str,
):
    await connection.execute(
        """
        INSERT INTO synced_events (source_uid, google_event_id, last_payload_hash, status)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (source_uid)
        DO UPDATE SET
            google_event_id = EXCLUDED.google_event_id,
            last_payload_hash = EXCLUDED.last_payload_hash,
            status = EXCLUDED.status,
            updated_at = NOW()
        """,
        source_uid,
        google_event_id,
        payload_hash,
        status,
    )
