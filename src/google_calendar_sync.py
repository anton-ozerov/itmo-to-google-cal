from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials as UserCredentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from lessons_to_events import SyncEvent

logger = logging.getLogger(__name__)

_SYNC_ID_TAG = "ITMO_SYNC_ID"
_CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar"
_DEFAULT_TOKEN_URI = "https://oauth2.googleapis.com/token"


@dataclass(frozen=True)
class ExistingGoogleEvent:
    event_id: str
    summary: str
    description: str | None


def _build_service_account_credentials(credentials_path: str):
    return service_account.Credentials.from_service_account_file(
        credentials_path,
        scopes=[_CALENDAR_SCOPE],
    )


def _build_authorized_user_credentials(credentials_path: str):
    credentials = UserCredentials.from_authorized_user_file(credentials_path, scopes=[_CALENDAR_SCOPE])
    if not credentials.valid:
        credentials.refresh(Request())
    return credentials


def _build_oauth_credentials_from_client_config(
    credentials_info: dict,
    refresh_token: str | None,
    token_uri: str,
):
    oauth_config = credentials_info.get("installed") or credentials_info.get("web")
    if not isinstance(oauth_config, dict):
        raise RuntimeError("Unsupported Google credentials format")

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

    try:
        if credentials_info.get("type") == "service_account":
            credentials = _build_service_account_credentials(str(creds_path))
        elif credentials_info.get("type") == "authorized_user":
            credentials = _build_authorized_user_credentials(str(creds_path))
        elif "installed" in credentials_info or "web" in credentials_info:
            credentials = _build_oauth_credentials_from_client_config(credentials_info, refresh_token, token_uri)
        else:
            raise RuntimeError("Unsupported Google credentials format")
    except Exception as error:
        raise RuntimeError(
            "Failed to initialize Google credentials. Supported formats: service account key JSON, "
            "authorized_user JSON, or OAuth client JSON (installed/web) with ITMO_ICAL_GOOGLE_REFRESH_TOKEN.",
        ) from error

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
