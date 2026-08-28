from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from lessons_to_events import SyncEvent

logger = logging.getLogger(__name__)

_SYNC_ID_TAG = "ITMO_SYNC_ID"
_CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar"


@dataclass(frozen=True)
class ExistingGoogleEvent:
    event_id: str
    summary: str
    description: str | None


def build_service(credentials_path: str):
    creds_path = Path(credentials_path)
    if not creds_path.exists():
        raise FileNotFoundError(f"Google credentials file not found: {credentials_path}")

    credentials = service_account.Credentials.from_service_account_file(
        str(creds_path),
        scopes=[_CALENDAR_SCOPE],
    )
    return build("calendar", "v3", credentials=credentials, cache_discovery=False)


def _description_with_sync_id(description: str, source_uid: str) -> str:
    marker = f"{_SYNC_ID_TAG}: {source_uid}"
    if marker in description:
        return description
    return f"{description}\n\n{marker}" if description else marker


def build_create_payload(event: SyncEvent) -> dict:
    payload: dict[str, object] = {
        "summary": event.summary,
        "description": _description_with_sync_id(event.description, event.source_uid),
        "start": {"dateTime": event.start_iso, "timeZone": "Europe/Moscow"},
        "end": {"dateTime": event.end_iso, "timeZone": "Europe/Moscow"},
    }
    if event.location:
        payload["location"] = event.location
    if event.source_url:
        payload["source"] = {"title": "ITMO lesson", "url": event.source_url}
    return payload


def build_update_payload(event: SyncEvent, existing_event: ExistingGoogleEvent) -> dict:
    description = _description_with_sync_id(existing_event.description or event.description, event.source_uid)
    payload: dict[str, object] = {
        "description": description,
        "start": {"dateTime": event.start_iso, "timeZone": "Europe/Moscow"},
        "end": {"dateTime": event.end_iso, "timeZone": "Europe/Moscow"},
    }
    if event.location:
        payload["location"] = event.location
    else:
        payload["location"] = None

    if event.source_url:
        payload["source"] = {"title": "ITMO lesson", "url": event.source_url}
    else:
        payload["source"] = None

    return payload


async def get_event(service, calendar_id: str, event_id: str) -> ExistingGoogleEvent | None:
    request = service.events().get(calendarId=calendar_id, eventId=event_id)
    try:
        raw_event = await asyncio.to_thread(request.execute)
    except HttpError as error:
        if error.resp.status == 404:
            return None
        raise

    return ExistingGoogleEvent(
        event_id=raw_event["id"],
        summary=raw_event.get("summary", ""),
        description=raw_event.get("description"),
    )


async def create_event(service, calendar_id: str, event: SyncEvent) -> str:
    request = service.events().insert(calendarId=calendar_id, body=build_create_payload(event))
    raw_event = await asyncio.to_thread(request.execute)
    return raw_event["id"]


async def update_event(service, calendar_id: str, google_event_id: str, event: SyncEvent):
    existing_event = await get_event(service, calendar_id, google_event_id)
    if existing_event is None:
        return False

    request = service.events().patch(
        calendarId=calendar_id,
        eventId=google_event_id,
        body=build_update_payload(event, existing_event),
    )
    await asyncio.to_thread(request.execute)
    return True


async def delete_event(service, calendar_id: str, google_event_id: str):
    request = service.events().delete(calendarId=calendar_id, eventId=google_event_id)
    try:
        await asyncio.to_thread(request.execute)
    except HttpError as error:
        if error.resp.status == 404:
            logger.info(f"Google event {google_event_id} already removed")
            return
        raise
