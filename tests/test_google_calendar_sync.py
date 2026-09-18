from __future__ import annotations

import unittest

from google_calendar_sync import (
    ExistingGoogleEvent,
    build_create_payload,
    build_update_payload,
    source_payload_for_event,
)
from lessons_to_events import SyncEvent


def make_event(**overrides) -> SyncEvent:
    values = {
        "source_uid": "pair:42",
        "summary": "[Лек] Предмет",
        "start_iso": "2026-09-18T10:00:00+03:00",
        "end_iso": "2026-09-18T11:30:00+03:00",
        "location": "101",
        "description": "Преподаватель: Иванов",
        "source_url": None,
        "payload_hash": "hash",
    }
    values.update(overrides)
    return SyncEvent(**values)


def make_existing(event: SyncEvent, **overrides) -> ExistingGoogleEvent:
    payload = source_payload_for_event(event)
    values = {
        "event_id": "google-1",
        "summary": payload["summary"],
        "description": f"{payload['description']}\n\nITMO_SYNC_ID: {event.source_uid}",
        "start": payload["start"],
        "end": payload["end"],
        "location": payload["location"],
        "source": payload["source"],
        "private_properties": {"itmoManaged": "true", "itmoSyncId": event.source_uid},
    }
    values.update(overrides)
    return ExistingGoogleEvent(**values)


class GooglePayloadTests(unittest.TestCase):
    def test_create_payload_has_private_identity(self):
        event = make_event()
        payload = build_create_payload(event)

        assert payload["extendedProperties"]["private"]["itmoSyncId"] == "pair:42"
        assert "ITMO_SYNC_ID: pair:42" in payload["description"]

    def test_source_change_updates_untouched_field(self):
        old = make_event()
        new = make_event(location="202")
        payload = build_update_payload(new, make_existing(old), source_payload_for_event(old))

        assert payload == {"location": "202"}

    def test_description_update_keeps_visible_sync_marker(self):
        old = make_event()
        new = make_event(description="Новый преподаватель")
        payload = build_update_payload(new, make_existing(old), source_payload_for_event(old))

        assert payload["description"] == "Новый преподаватель\n\nITMO_SYNC_ID: pair:42"

    def test_manual_change_wins_over_source_change(self):
        old = make_event()
        new = make_event(location="202")
        existing = make_existing(old, location="Моё место")
        payload = build_update_payload(new, existing, source_payload_for_event(old))

        assert "location" not in payload


if __name__ == "__main__":
    unittest.main()
