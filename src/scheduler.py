from __future__ import annotations

import asyncio
import logging
import os

from aiohttp import ClientSession, ClientTimeout

from credentials_hashing import get_credentials_hash

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} env var is required for scheduler")
    return value


def _build_sync_url() -> str:
    username = _required_env("ITMO_ICAL_ISU_USERNAME")
    password = _required_env("ITMO_ICAL_ISU_PASSWORD")
    base_url = os.getenv("ITMO_ICAL_SYNC_BASE_URL", "http://app:35601").rstrip("/")

    creds_hash = get_credentials_hash(username, password)
    return f"{base_url}/sync/{creds_hash}"


async def _sync_once(session: ClientSession, sync_url: str):
    try:
        response = await session.post(sync_url)
        response_text = await response.text()
        if response.status >= 400:
            logger.error(f"Scheduled sync failed: HTTP {response.status}, body: {response_text[:500]}")
            return
        logger.info(f"Scheduled sync completed: {response_text}")
    except Exception:
        logger.exception("Scheduled sync request failed")


async def _run_scheduler():
    sync_url = _build_sync_url()
    interval_seconds = int(float(os.getenv("ITMO_ICAL_SYNC_INTERVAL_SECONDS", "5400")))

    if interval_seconds <= 0:
        raise RuntimeError("ITMO_ICAL_SYNC_INTERVAL_SECONDS must be > 0")

    timeout = ClientTimeout(total=1800)
    logger.info(f"Starting scheduler with interval {interval_seconds}s, sync URL: {sync_url}")

    async with ClientSession(timeout=timeout) as session:
        while True:
            await _sync_once(session, sync_url)
            await asyncio.sleep(interval_seconds)


def main():
    asyncio.run(_run_scheduler())


if __name__ == "__main__":
    main()
