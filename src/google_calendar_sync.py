from __future__ import annotations

import asyncio
import json
import logging
import ssl
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials as UserCredentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from lessons_to_events import SyncEvent

logger = logging.getLogger(__name__)

_SYNC_ID_TAG = "ITMO_SYNC_ID"
_PRIVATE_SYNC_ID = "itmoSyncId"
_PRIVATE_MANAGED = "itmoManaged"
_CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar"
_DEFAULT_TOKEN_URI = "https://oauth2.googleapis.com/token"
_RETRYABLE_HTTP_STATUSES = {408, 429, 500, 502, 503, 504}
_MAX_REQUEST_ATTEMPTS = 3


@dataclass(frozen=True)
class ExistingGoogleEvent:
    event_id: str
    summary: str
    description: str | None
    start: dict[str, Any]
    end: dict[str, Any]
    location: str | None
    source: dict[str, Any] | None
    private_properties: dict[str, str]


def _build_oauth_credentials_from_client_config(
    credentials_info: dict,
    refresh_token: str | None,
    token_uri: str,
):
    oauth_config = credentials_info.get("installed") or credentials_info.get("web")
    if not isinstance(oauth_config, dict):
        raise TypeError("credentials.json must contain an installed or web OAuth client configuration")

    if not refresh_token:
        raise RuntimeError(
            "OAuth client credentials detected in credentials.json, but refresh token is missing. "
            "Set ITMO_ICAL_GOOGLE_REFRESH_TOKEN.",
        )

    client_id = oauth_config.get("client_id")
    client_secret = oauth_config.get("client_secret")
    if not client_id or not client_secret:
        raise RuntimeError("OAuth client credentials must include client_id and client_secret")

    credentials = UserCredentials(
        token=None,
        refresh_token=refresh_token,
        token_uri=token_uri,
        client_id=client_id,
        client_secret=client_secret,
        scopes=[_CALENDAR_SCOPE],
    )
    credentials.refresh(Request())
    return credentials


def build_service(credentials_path: str, refresh_token: str | None = None, token_uri: str = _DEFAULT_TOKEN_URI):
    creds_path = Path(credentials_path)
    if not creds_path.exists():
        raise FileNotFoundError(f"Google credentials file not found: {credentials_path}")

    try:
        credentials_info = json.loads(creds_path.read_text(encoding="utf-8"))
    except Exception as error:
        raise RuntimeError(f"Failed to parse Google credentials file: {credentials_path}") from error

    credentials = _build_oauth_credentials_from_client_config(credentials_info, refresh_token, token_uri)
    return build("calendar", "v3", credentials=credentials, cache_discovery=False)


async def _execute_request(request):
    last_error: Exception | None = None
    for attempt in range(1, _MAX_REQUEST_ATTEMPTS + 1):
        try:
            return await asyncio.to_thread(request.execute)
        except HttpError as error:
            if error.resp.status not in _RETRYABLE_HTTP_STATUSES or attempt == _MAX_REQUEST_ATTEMPTS:
                raise
            last_error = error
        except ssl.SSLError as error:
            if attempt == _MAX_REQUEST_ATTEMPTS:
                raise
            last_error = error

        wait_seconds = attempt
        logger.warning(
            f"Google API request failed (attempt {attempt}/{_MAX_REQUEST_ATTEMPTS}), retrying in {wait_seconds}s: {last_error}",
        )
        await asyncio.sleep(wait_seconds)

    if last_error is not None:
        raise last_error
    raise RuntimeError("Unexpected Google API retry state without error")


def _description_with_sync_id(description: str, source_uid: str) -> str:
    marker = f"{_SYNC_ID_TAG}: {source_uid}"
    if marker in description:
        return description
    return f"{description}\n\n{marker}" if description else marker


def source_payload_for_event(event: SyncEvent) -> dict[str, Any]:
    return {
        "summary": event.summary,
        "description": event.description,
        "start": {"dateTime": event.start_iso, "timeZone": "Europe/Moscow"},
        "end": {"dateTime": event.end_iso, "timeZone": "Europe/Moscow"},
        "location": event.location,
        "source": {"title": "ITMO lesson", "url": event.source_url} if event.source_url else None,
    }


def build_create_payload(event: SyncEvent) -> dict[str, Any]:
    payload = source_payload_for_event(event)
    payload["description"] = _description_with_sync_id(event.description, event.source_uid)
    payload["extendedProperties"] = {
        "private": {_PRIVATE_MANAGED: "true", _PRIVATE_SYNC_ID: event.source_uid},
    }
    if payload["location"] is None:
        payload.pop("location")
    if payload["source"] is None:
        payload.pop("source")
    return payload


def _description_without_sync_id(description: str | None, source_uid: str) -> str:
    if not description:
        return ""
    marker = f"{_SYNC_ID_TAG}: {source_uid}"
    lines = description.splitlines()
    if lines and lines[-1].strip() == marker:
        return "\n".join(lines[:-1]).rstrip()
    return description


def _existing_payload(event: ExistingGoogleEvent, source_uid: str) -> dict[str, Any]:
    return {
        "summary": event.summary,
        "description": _description_without_sync_id(event.description, source_uid),
        "start": event.start,
        "end": event.end,
        "location": event.location,
        "source": event.source,
    }


def build_update_payload(
    event: SyncEvent,
    existing_event: ExistingGoogleEvent,
    previous_source_payload: dict[str, Any] | None,
) -> dict[str, Any]:
    new_source_payload = source_payload_for_event(event)
    current_payload = _existing_payload(existing_event, event.source_uid)
    payload: dict[str, Any] = {}

    if previous_source_payload is not None:
        for field, new_value in new_source_payload.items():
            old_value = previous_source_payload.get(field)
            if new_value != old_value and current_payload.get(field) == old_value:
                payload[field] = (
                    _description_with_sync_id(new_value, event.source_uid) if field == "description" else new_value
                )
    desired_private = dict(existing_event.private_properties)
    desired_private.update({_PRIVATE_MANAGED: "true", _PRIVATE_SYNC_ID: event.source_uid})
    if desired_private != existing_event.private_properties:
        payload["extendedProperties"] = {"private": desired_private}

    return payload


def _parse_event(raw_event: dict[str, Any]) -> ExistingGoogleEvent | None:
    if raw_event.get("status") == "cancelled":
        return None
    return ExistingGoogleEvent(
        event_id=raw_event["id"],
        summary=raw_event.get("summary", ""),
        description=raw_event.get("description"),
        start=raw_event.get("start", {}),
        end=raw_event.get("end", {}),
        location=raw_event.get("location"),
        source=raw_event.get("source"),
        private_properties=raw_event.get("extendedProperties", {}).get("private", {}),
    )


async def get_event(service, calendar_id: str, event_id: str) -> ExistingGoogleEvent | None:
    request = service.events().get(calendarId=calendar_id, eventId=event_id)
    try:
        raw_event = await _execute_request(request)
    except HttpError as error:
        if error.resp.status in {404, 410}:
            return None
        raise

    return _parse_event(raw_event)


async def list_managed_events(service, calendar_id: str) -> dict[str, ExistingGoogleEvent]:
    result: dict[str, ExistingGoogleEvent] = {}
    page_token = None
    while True:
        request = service.events().list(
            calendarId=calendar_id,
            privateExtendedProperty=f"{_PRIVATE_MANAGED}=true",
            showDeleted=False,
            maxResults=2500,
            pageToken=page_token,
        )
        response = await _execute_request(request)
        for raw_event in response.get("items", []):
            event = _parse_event(raw_event)
            if event is None:
                continue
            source_uid = event.private_properties.get(_PRIVATE_SYNC_ID)
            if source_uid:
                result.setdefault(source_uid, event)
        page_token = response.get("nextPageToken")
        if not page_token:
            return result


async def create_event(service, calendar_id: str, event: SyncEvent) -> str:
    request = service.events().insert(calendarId=calendar_id, body=build_create_payload(event))
    raw_event = await _execute_request(request)
    return raw_event["id"]


async def update_event(
    service,
    calendar_id: str,
    existing_event: ExistingGoogleEvent,
    event: SyncEvent,
    previous_source_payload: dict[str, Any] | None,
) -> bool:
    payload = build_update_payload(event, existing_event, previous_source_payload)
    if not payload:
        return False
    request = service.events().patch(
        calendarId=calendar_id,
        eventId=existing_event.event_id,
        body=payload,
    )
    await _execute_request(request)
    return True


async def delete_event(service, calendar_id: str, google_event_id: str):
    request = service.events().delete(calendarId=calendar_id, eventId=google_event_id)
    try:
        await _execute_request(request)
    except HttpError as error:
        if error.resp.status in {404, 410}:
            logger.info(f"Google event {google_event_id} already removed")
            return
        raise
