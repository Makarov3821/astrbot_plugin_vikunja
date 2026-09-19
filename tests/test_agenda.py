import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from plugin_loader import agenda_module, domain

busy_intervals = agenda_module.busy_intervals
format_plan = agenda_module.format_plan
free_slots = agenda_module.free_slots
parse_windows = agenda_module.parse_windows
plan_blocks = agenda_module.plan_blocks
planning_candidates = agenda_module.planning_candidates
split_agenda = agenda_module.split_agenda
estimate_minutes = domain.estimate_minutes
to_vikunja_time = domain.to_vikunja_time

TZ = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 9, 20, 9, 0, tzinfo=TZ)


def task(task_id, **kwargs):
    base = {"id": task_id, "title": f"任务{task_id}", "done": False, "priority": 0}
    for key in ("due_date", "start_date", "end_date"):
        if key in kwargs and isinstance(kwargs[key], datetime):
            kwargs[key] = to_vikunja_time(kwargs[key])
    if "labels" in kwargs:
        kwargs["labels"] = [
            {"id": index, "title": title}
            for index, title in enumerate(kwargs["labels"], 1)
        ]
    base.update(kwargs)
    return base


class AgendaTests(unittest.TestCase):
    def test_split_puts_each_task_in_exactly_one_group(self):
        tasks = [
            task(1, due_date=NOW - timedelta(days=1)),
            task(2, due_date=NOW + timedelta(hours=5)),
            task(3, start_date=NOW + timedelta(hours=2)),
            task(4, due_date=NOW + timedelta(days=2)),
            task(5),
            task(6, due_date=NOW - timedelta(days=3), labels=["@等待中"]),
            task(7, done=True),
        ]
        agenda = split_agenda(tasks, TZ, NOW)
        self.assertEqual([t["id"] for t in agenda.overdue], [1])
        self.assertEqual([t["id"] for t in agenda.due_today], [2])
        self.assertEqual([t["id"] for t in agenda.blocks], [3])
        self.assertEqual([t["id"] for t in agenda.due_soon], [4])
        self.assertEqual([t["id"] for t in agenda.unscheduled], [5])
        self.assertEqual([t["id"] for t in agenda.waiting], [6])

    def test_waiting_tasks_never_enter_planning(self):
        tasks = [
            task(1, due_date=NOW + timedelta(hours=1)),
            task(2, due_date=NOW - timedelta(days=1), labels=["@等待中"]),
        ]
        candidates = planning_candidates(split_agenda(tasks, TZ, NOW))
        self.assertEqual([t["id"] for t in candidates], [1])

    def test_estimate_label_drives_block_length(self):
        self.assertEqual(estimate_minutes(task(1, labels=["@深度", "est2h"])), 120)
        self.assertEqual(estimate_minutes(task(2)), 30)
        self.assertEqual(estimate_minutes(task(3), default=45), 45)

    def test_parse_windows_and_free_slots_skip_existing_blocks(self):
        windows = parse_windows("09:00-12:00,14:00-18:00", TZ, NOW)
        self.assertEqual(windows[0][0], datetime(2026, 9, 20, 9, 0, tzinfo=TZ))
        self.assertEqual(windows[1][1], datetime(2026, 9, 20, 18, 0, tzinfo=TZ))
        existing = [
            task(
                9,
                start_date=datetime(2026, 9, 20, 10, 0, tzinfo=TZ),
                end_date=datetime(2026, 9, 20, 11, 0, tzinfo=TZ),
            )
        ]
        busy = busy_intervals(existing, TZ, NOW)
        slots = free_slots(windows, busy, NOW)
        self.assertIn(
            (
                datetime(2026, 9, 20, 9, 0, tzinfo=TZ),
                datetime(2026, 9, 20, 10, 0, tzinfo=TZ),
            ),
            slots,
        )
        self.assertIn(
            (
                datetime(2026, 9, 20, 11, 0, tzinfo=TZ),
                datetime(2026, 9, 20, 12, 0, tzinfo=TZ),
            ),
            slots,
        )

    def test_plan_packs_by_urgency_without_double_booking(self):
        tasks = [
            task(1, due_date=NOW + timedelta(hours=3), labels=["est1h"]),
            task(2, due_date=NOW - timedelta(days=1), labels=["est30"]),
            task(3, labels=["est4h"]),
            task(
                4,
                start_date=datetime(2026, 9, 20, 10, 0, tzinfo=TZ),
                end_date=datetime(2026, 9, 20, 11, 0, tzinfo=TZ),
            ),
        ]
        agenda = split_agenda(tasks, TZ, NOW)
        windows = parse_windows("09:00-12:00", TZ, NOW)
        busy = busy_intervals(tasks, TZ, NOW)
        planned, unplanned = plan_blocks(
            planning_candidates(agenda), windows, busy, NOW
        )
        self.assertEqual([t["id"] for t, _, _ in planned], [2, 1])
        self.assertEqual(planned[0][1], datetime(2026, 9, 20, 9, 0, tzinfo=TZ))
        self.assertEqual(planned[0][2], datetime(2026, 9, 20, 9, 30, tzinfo=TZ))
        # The 10:00-11:00 block is occupied, so the one hour task lands after it.
        self.assertEqual(planned[1][1], datetime(2026, 9, 20, 11, 0, tzinfo=TZ))
        self.assertEqual([t["id"] for t in unplanned], [3])
        text = format_plan(planned, unplanned, TZ, {}, applied=True)
        self.assertIn("09:00-09:30", text)
        self.assertIn("已写入", text)

    def test_rejects_bad_window(self):
        with self.assertRaisesRegex(ValueError, "无法识别时间窗"):
            parse_windows("上午", TZ, NOW)
        with self.assertRaisesRegex(ValueError, "结束时间"):
            parse_windows("14:00-13:00", TZ, NOW)


if __name__ == "__main__":
    unittest.main()
