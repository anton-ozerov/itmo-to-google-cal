from __future__ import annotations

import unittest

from lessons_to_events import raw_lesson_to_sync_event


class LessonConversionTests(unittest.TestCase):
    def setUp(self):
        self.lesson = {
            "pair_id": 12345,
            "date": "2026-09-18",
            "time_start": "10:00",
            "time_end": "11:30",
            "subject": "Тестирование ПО",
            "type": "Лекции",
            "teacher_name": "Иванов И.И.",
            "room": "101",
            "building": "Кронверкский, 49",
            "zoom_url": None,
            "zoom_password": None,
            "zoom_info": None,
            "note": None,
            "group": "P0000",
        }

    def test_pair_id_is_stable_identity(self):
        original = raw_lesson_to_sync_event(self.lesson)
        moved = raw_lesson_to_sync_event({**self.lesson, "time_start": "12:00", "time_end": "13:30"})

        assert original.source_uid == "pair:12345"
        assert len(original.legacy_source_uid) == 64
        assert moved.source_uid == original.source_uid
        assert moved.payload_hash != original.payload_hash

    def test_payload_is_stable_between_runs(self):
        first = raw_lesson_to_sync_event(self.lesson)
        second = raw_lesson_to_sync_event(self.lesson)

        assert first.payload_hash == second.payload_hash
        assert "Обновлено:" not in first.description

    def test_fallback_distinguishes_parallel_lessons(self):
        without_id = {key: value for key, value in self.lesson.items() if key != "pair_id"}
        another_group = {**without_id, "group": "P0001"}

        assert raw_lesson_to_sync_event(without_id).source_uid != raw_lesson_to_sync_event(another_group).source_uid


if __name__ == "__main__":
    unittest.main()
