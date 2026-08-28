from __future__ import annotations

import logging

import sentry_sdk
from aiohttp import ClientSession
from flask import Flask, jsonify
from sentry_sdk.integrations.flask import FlaskIntegration

from auth import get_access_token
from credentials_hashing import get_credentials_hash
from google_calendar_sync import build_service, create_event, delete_event, update_event
from lessons_to_events import raw_lesson_to_sync_event
from main_api import get_raw_lessons
from sync_state_repository import SyncState, create_pool, load_states, upsert_state

logging.basicConfig(level=logging.INFO)
logging.getLogger("werkzeug").handlers = []  # prevent duplicated logging output

app = Flask(__name__)
application = app  # for wsgi compliance

prefix = "ITMO_ICAL"
app.config.from_prefixed_env(prefix, loads=str)
assert "ISU_USERNAME" in app.config, f"{prefix}_ISU_USERNAME env var is required"
assert "ISU_PASSWORD" in app.config, f"{prefix}_ISU_PASSWORD env var is required"
assert "GOOGLE_CALENDAR_ID" in app.config, f"{prefix}_GOOGLE_CALENDAR_ID env var is required"
assert "DATABASE_URL" in app.config, f"{prefix}_DATABASE_URL env var is required"

_google_credentials_path = app.config.get("GOOGLE_CREDENTIALS_PATH", "/app/credentials.json")
_google_refresh_token = app.config.get("GOOGLE_REFRESH_TOKEN")
_google_token_uri = app.config.get("GOOGLE_TOKEN_URI", "https://oauth2.googleapis.com/token")
_google_calendar_id = app.config["GOOGLE_CALENDAR_ID"]
assert _google_calendar_id.strip(), f"{prefix}_GOOGLE_CALENDAR_ID must not be empty"
app.logger.info(f"Using Google Calendar ID: {_google_calendar_id}")
_google_service = None
_db_pool = None

_creds_hash = get_credentials_hash(app.config["ISU_USERNAME"], app.config["ISU_PASSWORD"])
_sync_route = f"/sync/{_creds_hash}"
app.logger.info(f"URL path for schedule sync: {_sync_route}")

if app.config.get("SENTRY_DSN"):
    sentry_sdk.init(
        dsn=app.config["SENTRY_DSN"],
        integrations=[FlaskIntegration()],
        traces_sample_rate=1.0,
    )


async def _get_db_pool():
    global _db_pool
    if _db_pool is None:
        _db_pool = await create_pool(app.config["DATABASE_URL"])
    return _db_pool


def _get_google_service():
    global _google_service
    if _google_service is None:
        _google_service = build_service(
            _google_credentials_path,
            refresh_token=_google_refresh_token,
            token_uri=_google_token_uri,
        )
    return _google_service


@app.route(_sync_route, methods=["POST", "GET"])
async def sync_schedule_to_google_calendar():
    google_service = _get_google_service()

    async with ClientSession() as session:
        token = await get_access_token(session, app.config["ISU_USERNAME"], app.config["ISU_PASSWORD"])
        lessons = await get_raw_lessons(session, token)

    source_events = {event.source_uid: event for event in map(raw_lesson_to_sync_event, lessons)}

    pool = await _get_db_pool()
    states = await load_states(pool)

    stats = {
        "source_events": len(source_events),
        "created": 0,
        "updated": 0,
        "deleted": 0,
        "skipped_manual_delete": 0,
        "unchanged": 0,
    }

    for source_uid, source_event in source_events.items():
        state: SyncState | None = states.get(source_uid)

        if state is not None and state.status == "deleted_by_user":
            stats["skipped_manual_delete"] += 1
            continue

        if state is None or state.status == "deleted_from_source":
            google_event_id = await create_event(google_service, _google_calendar_id, source_event)
            await upsert_state(pool, source_uid, google_event_id, source_event.payload_hash, "active")
            stats["created"] += 1
            continue

        if source_event.payload_hash == state.last_payload_hash:
            stats["unchanged"] += 1
            continue

        is_updated = await update_event(
            google_service,
            _google_calendar_id,
            state.google_event_id,
            source_event,
        )
        if not is_updated:
            await upsert_state(pool, source_uid, state.google_event_id, source_event.payload_hash, "deleted_by_user")
            stats["skipped_manual_delete"] += 1
            continue

        await upsert_state(pool, source_uid, state.google_event_id, source_event.payload_hash, "active")
        stats["updated"] += 1

    for source_uid, state in states.items():
        if state.status != "active" or source_uid in source_events:
            continue

        await delete_event(google_service, _google_calendar_id, state.google_event_id)
        await upsert_state(pool, source_uid, state.google_event_id, state.last_payload_hash, "deleted_from_source")
        stats["deleted"] += 1

    return jsonify(stats)


sentry_sdk.capture_message(f"my-itmo-ru-to-google-cal started for {app.config['ISU_USERNAME']}, hash {_creds_hash}")
