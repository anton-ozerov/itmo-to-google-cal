from __future__ import annotations

import logging
from collections.abc import Iterable

from google_calendar_sync import (
    ExistingGoogleEvent,
    create_event,
    delete_event,
    get_event,
    list_managed_events,
    update_event,
)
from lessons_to_events import SyncEvent
from sync_state_repository import SyncState, load_states, upsert_state

logger = logging.getLogger(__name__)


def index_source_events(events: Iterable[SyncEvent]) -> dict[str, SyncEvent]:
    result: dict[str, SyncEvent] = {}
    for event in events:
        if event.source_uid in result:
            raise ValueError(f"ITMO returned duplicate lesson id: {event.source_uid}")
        result[event.source_uid] = event
    return result


class EventLookup:
    def __init__(self, service, calendar_id: str, managed_events: dict[str, ExistingGoogleEvent]):
        self.service = service
        self.calendar_id = calendar_id
        self.managed_events = managed_events
        self.fetched_events: dict[str, ExistingGoogleEvent | None] = {}

    async def get(self, source_uid: str, state: SyncState | None) -> ExistingGoogleEvent | None:
        if source_uid in self.managed_events:
            return self.managed_events[source_uid]
        if state is None:
            return None
        if state.google_event_id not in self.fetched_events:
            self.fetched_events[state.google_event_id] = await get_event(
                self.service,
                self.calendar_id,
                state.google_event_id,
            )
        return self.fetched_events[state.google_event_id]


async def _sync_source_event(
    service,
    calendar_id: str,
    connection,
    lookup: EventLookup,
    source_event: SyncEvent,
    state: SyncState | None,
) -> str:
    source_uid = source_event.source_uid
    if state is not None and state.status == "deleted_by_user":
        return "skipped_manual_delete"

    existing_event = await lookup.get(source_uid, state)
    if state is not None and state.status == "active" and existing_event is None:
        await upsert_state(connection, source_uid, state.google_event_id, source_event, "deleted_by_user")
        return "skipped_manual_delete"

    if existing_event is None:
        google_event_id = await create_event(service, calendar_id, source_event)
        await upsert_state(connection, source_uid, google_event_id, source_event, "active")
        return "created"

    changed = await update_event(
        service,
        calendar_id,
        existing_event,
        source_event,
        state.source_payload if state is not None else None,
    )
    await upsert_state(connection, source_uid, existing_event.event_id, source_event, "active")
    return "updated" if changed else "unchanged"


async def _remove_source_event(service, calendar_id: str, connection, lookup: EventLookup, state: SyncState) -> str:
    existing_event = await lookup.get(state.source_uid, state)
    if existing_event is None:
        # Both sides disappeared since the last run. Prefer remembering a
        # possible manual deletion so a temporarily restored source lesson is
        # never recreated against the user's intent.
        await upsert_state_from_state(connection, state, "deleted_by_user")
        return "skipped_manual_delete"

    await delete_event(service, calendar_id, state.google_event_id)
    await upsert_state_from_state(connection, state, "deleted_from_source")
    return "deleted"


async def synchronize(service, calendar_id: str, source_events: dict[str, SyncEvent], connection) -> dict[str, int]:
    states = await load_states(connection)
    lookup = EventLookup(service, calendar_id, await list_managed_events(service, calendar_id))

    stats = {
        "source_events": len(source_events),
        "created": 0,
        "updated": 0,
        "deleted": 0,
        "skipped_manual_delete": 0,
        "unchanged": 0,
    }

    for source_uid, source_event in source_events.items():
        state = states.get(source_uid)
        outcome = await _sync_source_event(service, calendar_id, connection, lookup, source_event, state)
        stats[outcome] += 1

    for source_uid, state in states.items():
        if state.status != "active" or source_uid in source_events:
            continue

        outcome = await _remove_source_event(service, calendar_id, connection, lookup, state)
        stats[outcome] += 1

    return stats


async def upsert_state_from_state(connection, state: SyncState, status: str) -> None:
    await upsert_state(
        connection,
        state.source_uid,
        state.google_event_id,
        state.source_payload,
        status,
        payload_hash=state.last_payload_hash,
    )
