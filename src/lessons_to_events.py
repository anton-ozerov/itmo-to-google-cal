from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256

from dateutil.parser import isoparse

_lesson_type_to_tag_map = {
    "Лекции": "Лек",
    "Практические занятия": "Прак",
    "Лабораторные занятия": "Лаб",
    "Занятия спортом": "Спорт",
}

_raw_lesson_key_names = {
    "group": "Группа",
    "teacher_name": "Преподаватель",
    "teacher_fio": "Преподаватель",
    "zoom_url": "Ссылка на Zoom",
    "zoom_password": "Пароль Zoom",
    "zoom_info": "Доп. информация для Zoom",
    "note": "Примечание",
}


@dataclass(frozen=True)
class SyncEvent:
    source_uid: str
    summary: str
    start_iso: str
    end_iso: str
    location: str | None
    description: str
    source_url: str | None
    payload_hash: str


def _lesson_type_to_tag(t: str):
    return _lesson_type_to_tag_map.get(t, t)


def _raw_lesson_to_description(raw_lesson: dict):
    lines = []
    for key, name in _raw_lesson_key_names.items():
        if raw_lesson.get(key):
            lines.append(f"{name}: {raw_lesson[key]}")

    _msk_formatted_datetime = (datetime.utcnow() + timedelta(hours=3)).strftime("%Y-%m-%d %H:%M")
    lines.append(f"Обновлено: {_msk_formatted_datetime} MSK")
    return "\n".join(lines)


def _raw_lesson_to_location(raw_lesson: dict):
    elements = []
    for key in "room", "building":
        if raw_lesson.get(key):
            elements.append(raw_lesson[key])

    result = ", ".join(elements)

    if raw_lesson.get("zoom_url"):
        result = f"Zoom / {result}" if result else "Zoom"

    return result if result else None


def _raw_lesson_to_source_uid(raw_lesson: dict):
    elements = [
        raw_lesson["date"],
        raw_lesson["time_start"],
        raw_lesson["subject"],
    ]
    source_material = ", ".join(elements)
    return sha256(source_material.encode("utf-8")).hexdigest()


def _payload_hash(
    summary: str,
    start_iso: str,
    end_iso: str,
    location: str | None,
    description: str,
    source_url: str | None,
) -> str:
    payload = {
        "summary": summary,
        "start": start_iso,
        "end": end_iso,
        "location": location,
        "description": description,
        "source_url": source_url,
    }
    json_payload = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return sha256(json_payload.encode("utf-8")).hexdigest()


def raw_lesson_to_sync_event(raw_lesson: dict) -> SyncEvent:
    begin = isoparse(f"{raw_lesson['date']}T{raw_lesson['time_start']}:00+03:00")
    end = isoparse(f"{raw_lesson['date']}T{raw_lesson['time_end']}:00+03:00")
    if begin > end:
        begin, end = end, begin

    summary = f"[{_lesson_type_to_tag(raw_lesson['type'])}] {raw_lesson['subject']}"
    description = _raw_lesson_to_description(raw_lesson)
    location = _raw_lesson_to_location(raw_lesson)
    source_url = raw_lesson.get("zoom_url")
    if source_url:
        summary = f"🌐 {summary}"
    else:
        summary = f"🏫 {summary}"
    start_iso = begin.isoformat()
    end_iso = end.isoformat()

    return SyncEvent(
        source_uid=_raw_lesson_to_source_uid(raw_lesson),
        summary=summary,
        start_iso=start_iso,
        end_iso=end_iso,
        location=location,
        description=description,
        source_url=source_url,
        payload_hash=_payload_hash(summary, start_iso, end_iso, location, description, source_url),
    )
