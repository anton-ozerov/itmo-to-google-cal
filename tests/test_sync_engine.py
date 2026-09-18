from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from google_calendar_sync import ExistingGoogleEvent, source_payload_for_event
from lessons_to_events import SyncEvent
from sync_engine import synchronize
from sync_state_repository import SyncState


def make_event() -> SyncEvent:
    return SyncEvent(
        source_uid="pair:42",
        legacy_source_uid="legacy-42",
        summary="Lesson",
        start_iso="2026-09-18T10:00:00+03:00",
        end_iso="2026-09-18T11:30:00+03:00",
        location="101",
        description="Teacher",
        source_url=None,
        payload_hash="hash",
    )


def make_google_event(event: SyncEvent) -> ExistingGoogleEvent:
    payload = source_payload_for_event(event)
    return ExistingGoogleEvent(
        event_id="google-42",
        summary=event.summary,
        description=event.description,
        start=payload["start"],
        end=payload["end"],
        location=event.location,
        source=None,
        private_properties={"itmoManaged": "true", "itmoSyncId": event.source_uid},
    )


class SyncEngineTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_google_event_becomes_manual_delete_tombstone(self):
        event = make_event()
        state = SyncState(event.source_uid, "google-42", event.payload_hash, "active", source_payload_for_event(event))

        with (
            patch("sync_engine.load_states", AsyncMock(return_value={event.source_uid: state})),
            patch("sync_engine.list_managed_events", AsyncMock(return_value={})),
            patch("sync_engine.get_event", AsyncMock(return_value=None)),
            patch("sync_engine.upsert_state", AsyncMock()) as upsert,
        ):
            stats = await synchronize(object(), "calendar", {event.source_uid: event}, object())

        assert stats["skipped_manual_delete"] == 1
        assert upsert.await_args.args[-1] == "deleted_by_user"

    async def test_removed_source_event_is_deleted_from_google(self):
        event = make_event()
        google_event = make_google_event(event)
        state = SyncState(event.source_uid, google_event.event_id, event.payload_hash, "active", {})

        with (
            patch("sync_engine.load_states", AsyncMock(return_value={event.source_uid: state})),
            patch("sync_engine.list_managed_events", AsyncMock(return_value={event.source_uid: google_event})),
            patch("sync_engine.delete_event", AsyncMock()) as delete,
            patch("sync_engine.upsert_state", AsyncMock()) as upsert,
        ):
            stats = await synchronize(object(), "calendar", {}, object())

        assert stats["deleted"] == 1
        delete.assert_awaited_once_with(unittest.mock.ANY, "calendar", google_event.event_id)
        assert upsert.await_args.args[4] == "deleted_from_source"

    async def test_legacy_tombstone_is_migrated_to_pair_id(self):
        event = make_event()
        state = SyncState(event.legacy_source_uid, "google-42", "old-hash", "deleted_by_user", None)

        with (
            patch("sync_engine.load_states", AsyncMock(return_value={event.legacy_source_uid: state})),
            patch("sync_engine.rename_state", AsyncMock()) as rename,
            patch("sync_engine.list_managed_events", AsyncMock(return_value={})),
        ):
            stats = await synchronize(object(), "calendar", {event.source_uid: event}, object())

        rename.assert_awaited_once_with(unittest.mock.ANY, event.legacy_source_uid, event.source_uid)
        assert stats["skipped_manual_delete"] == 1


if __name__ == "__main__":
    unittest.main()
