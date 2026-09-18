from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone

from aiohttp import ClientSession

logger = logging.getLogger(__name__)

_API_BASE_URL = "https://my.itmo.ru/api"
_MOSCOW_TIMEZONE = timezone(timedelta(hours=3))


def _get_date_range_params() -> dict:
    """Produces start and end dates to request events of current academic term"""
    today = datetime.now(_MOSCOW_TIMEZONE).date()
    pivot = today.replace(month=8, day=1)
    term_start_year = today.year - 1 if today < pivot else today.year
    return dict(
        date_start=f"{term_start_year}-08-01",
        date_end=f"{term_start_year + 1}-07-31",
    )


async def _get_calendar_data(session: ClientSession, auth_token: str, path: str) -> dict:
    url = _API_BASE_URL + path
    params = _get_date_range_params()
    logger.info(f"Getting data from {url}, using params {params}")

    auth_header = "Bearer " + auth_token
    resp = await session.get(url, params=params, headers={"Authorization": auth_header})
    resp.raise_for_status()
    logger.info(f"Got response from {url}")
    return await resp.json()


async def get_raw_lessons(session: ClientSession, auth_token: str) -> Iterable[dict]:
    resp_json = await _get_calendar_data(session, auth_token, "/schedule/schedule/personal")
    if resp_json.get("code", 0) != 0:
        raise RuntimeError(f"ITMO schedule API returned error code {resp_json['code']}")
    days = resp_json.get("data")
    if not isinstance(days, list):
        raise TypeError("ITMO schedule API response does not contain a data list")
    return (dict(date=day["date"], **lesson) for day in days for lesson in day["lessons"])
