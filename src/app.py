from __future__ import annotations

import logging

import sentry_sdk
from aiohttp import ClientSession
from flask import Flask, jsonify
from sentry_sdk.integrations.flask import FlaskIntegration

from auth import get_access_token
from credentials_hashing import get_credentials_hash
from google_calendar_sync import build_service
from lessons_to_events import raw_lesson_to_sync_event
from main_api import get_raw_lessons
from sync_engine import index_source_events, synchronize
from sync_state_repository import create_connection, release_sync_lock, try_acquire_sync_lock

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

_creds_hash = get_credentials_hash(app.config["ISU_USERNAME"], app.config["ISU_PASSWORD"])
_sync_route = f"/sync/{_creds_hash}"
app.logger.info(f"URL path for schedule sync: {_sync_route}")

if app.config.get("SENTRY_DSN"):
    sentry_sdk.init(
        dsn=app.config["SENTRY_DSN"],
        integrations=[FlaskIntegration()],
        traces_sample_rate=1.0,
    )


def _build_google_service():
    return build_service(
        _google_credentials_path,
        refresh_token=_google_refresh_token,
        token_uri=_google_token_uri,
    )


@app.route(_sync_route, methods=["POST", "GET"])
async def sync_schedule_to_google_calendar():
    google_service = _build_google_service()

    async with ClientSession() as session:
        token = await get_access_token(session, app.config["ISU_USERNAME"], app.config["ISU_PASSWORD"])
        lessons = await get_raw_lessons(session, token)

    source_events = index_source_events(map(raw_lesson_to_sync_event, lessons))

    connection = await create_connection(app.config["DATABASE_URL"])
    lock_acquired = False
    try:
        lock_acquired = await try_acquire_sync_lock(connection)
        if not lock_acquired:
            return jsonify({"error": "sync_already_running"}), 409
        stats = await synchronize(google_service, _google_calendar_id, source_events, connection)
    finally:
        if lock_acquired:
            await release_sync_lock(connection)
        await connection.close()

    app.logger.info(f"Sync completed for {app.config['ISU_USERNAME']}, hash {_creds_hash}: {stats}")

    return jsonify(stats)


sentry_sdk.capture_message(f"my-itmo-ru-to-google-cal started for {app.config['ISU_USERNAME']}, hash {_creds_hash}")
