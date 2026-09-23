"""The default "start today" anchor, and how it stays out of the today lists."""

import types
import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from state_store import StateStore

from plugin_loader import agenda_module, bootstrap_module, domain, load_plugin_class
from test_recurrence import FakeVikunja

TZ = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 9, 23, 10, 0, tzinfo=TZ)


def task(task_id, **kwargs):
    base = {"id": task_id, "title": f"任务{task_id}", "done": False, "priority": 0}
    for key in ("due_date", "start_date", "end_date"):
        if isinstance(kwargs.get(key), datetime):
            kwargs[key] = domain.to_vikunja_time(kwargs[key])
    base.update(kwargs)
    return base


class TimeBlockDistinctionTests(unittest.TestCase):
    def test_midnight_start_without_end_is_only_a_gantt_anchor(self):
        anchor = task(1, start_date=datetime(2026, 9, 23, 0, 0, tzinfo=TZ))
        self.assertFalse(domain.is_time_block(anchor, TZ))
        self.assertEqual(domain.format_time_block(anchor, TZ), "")

    def test_start_with_a_clock_time_is_a_real_block(self):
        block = task(2, start_date=datetime(2026, 9, 23, 14, 0, tzinfo=TZ))
        self.assertTrue(domain.is_time_block(block, TZ))

    def test_start_and_end_is_always_a_real_block(self):
        block = task(
            3,
            start_date=datetime(2026, 9, 23, 0, 0, tzinfo=TZ),
            end_date=datetime(2026, 9, 23, 2, 0, tzinfo=TZ),
        )
        self.assertTrue(domain.is_time_block(block, TZ))
        self.assertEqual(domain.format_time_block(block, TZ), "00:00-02:00")

    def test_anchor_does_not_pollute_today_and_stays_unscheduled(self):
        anchor = task(1, start_date=datetime(2026, 9, 23, 0, 0, tzinfo=TZ))
        real_block = task(
            2,
            start_date=datetime(2026, 9, 23, 14, 0, tzinfo=TZ),
            end_date=datetime(2026, 9, 23, 16, 0, tzinfo=TZ),
        )
        tasks = [anchor, real_block]
        self.assertEqual(
            [t["id"] for t in domain.select_tasks(tasks, "today", TZ, NOW)], [2]
        )
        self.assertEqual(
            [t["id"] for t in domain.select_tasks(tasks, "scheduled", TZ, NOW)], [2]
        )
        self.assertEqual(
            [t["id"] for t in domain.select_tasks(tasks, "unscheduled", TZ, NOW)], [1]
        )

    def test_old_anchor_from_a_previous_day_never_shows_up_in_today(self):
        # Without this, every task ever created would sit in the today list forever.
        stale = task(1, start_date=datetime(2026, 8, 1, 0, 0, tzinfo=TZ))
        self.assertEqual(domain.select_tasks([stale], "today", TZ, NOW), [])

    def test_multi_day_block_covers_every_day_it_spans(self):
        span = task(
            4,
            start_date=datetime(2026, 9, 22, 9, 0, tzinfo=TZ),
            end_date=datetime(2026, 9, 24, 18, 0, tzinfo=TZ),
        )
        self.assertTrue(domain.block_covers_day(span, TZ, NOW.date()))
        self.assertEqual(
            [t["id"] for t in domain.select_tasks([span], "today", TZ, NOW)], [4]
        )

    def test_agenda_and_planner_ignore_the_anchor(self):
        anchor = task(1, start_date=datetime(2026, 9, 23, 0, 0, tzinfo=TZ), priority=3)
        agenda = agenda_module.split_agenda([anchor], TZ, NOW)
        self.assertEqual(agenda.blocks, [])
        self.assertEqual([t["id"] for t in agenda.unscheduled], [1])
        # The anchor must not occupy the morning in the planner either.
        self.assertEqual(agenda_module.busy_intervals([anchor], TZ, NOW), [])
        candidates = agenda_module.planning_candidates(agenda)
        self.assertEqual([t["id"] for t in candidates], [1])

    def test_server_side_queries_require_an_end_date_for_blocks(self):
        query, include_nulls = domain.scope_filter_query("today")
        self.assertIn("end_date > now/d", query)
        self.assertFalse(include_nulls)
        # The saved filter and the local selection must mean the same thing.
        self.assertEqual(bootstrap_module.TODAY_QUERY, domain.TODAY_FILTER_QUERY)


class DefaultStartTodayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.saved = {}

        async def load():
            return self.saved

        async def save(value):
            self.saved = value

        self.state = StateStore(load, save)
        await self.state.initialize()
        await self.state.register_channel("qq", {"umo": "qq"})
        plugin_class = load_plugin_class()
        self.plugin = plugin_class.__new__(plugin_class)
        self.plugin.state = self.state
        self.plugin.tz = TZ
        self.plugin.tz_name = "Asia/Shanghai"
        self.plugin.config = {"default_project": "Inbox", "default_due_time": "21:00"}
        self.plugin._ready = True
        self.plugin._projects_cache = None
        self.plugin._labels_cache = None
        self.plugin._label_title_by_key = {}
        self.plugin.client = FakeVikunja()
        self.plugin._now = lambda: NOW
        self.event = types.SimpleNamespace(
            unified_msg_origin="qq",
            get_group_id=lambda: "",
            get_platform_name=lambda: "qq_official",
            get_sender_id=lambda: "me",
        )

    def _only_task(self):
        return list(self.plugin.client.tasks.values())[0]

    async def test_enabled_by_default_and_anchors_at_local_midnight(self):
        await self.plugin.vikunja_create_task(self.event, "写引言")
        start = domain.from_vikunja_time(self._only_task()["start_date"]).astimezone(TZ)
        self.assertEqual(start, NOW.replace(hour=0, minute=0))
        self.assertNotIn("end_date", self._only_task())

    async def test_switch_off_keeps_tasks_without_a_start_date(self):
        self.plugin.config["default_start_today"] = False
        await self.plugin.vikunja_create_task(self.event, "写引言")
        self.assertNotIn("start_date", self._only_task())

    async def test_explicit_start_is_never_overwritten(self):
        await self.plugin.vikunja_create_task(
            self.event, "写引言", start="2026-09-24T14:00", end="2026-09-24T16:00"
        )
        task_data = self._only_task()
        start = domain.from_vikunja_time(task_data["start_date"]).astimezone(TZ)
        self.assertEqual(start, datetime(2026, 9, 24, 14, 0, tzinfo=TZ))

    async def test_anchor_never_precedes_an_already_overdue_due_date(self):
        await self.plugin.vikunja_create_task(
            self.event, "早就该交的", due="2026-09-01T18:00"
        )
        task_data = self._only_task()
        start = domain.from_vikunja_time(task_data["start_date"]).astimezone(TZ)
        due = domain.from_vikunja_time(task_data["due_date"]).astimezone(TZ)
        self.assertLessEqual(start, due)
        self.assertEqual(start.date(), due.date())

    async def test_anchored_task_still_gets_planned_into_the_day(self):
        await self.plugin.vikunja_create_task(
            self.event, "写引言", due="2026-09-23T20:00", labels="est1h"
        )
        result = await self.plugin.vikunja_plan_day(
            self.event, windows="14:00-18:00", apply_changes=True
        )
        self.assertIn("已写入", result)
        task_data = self._only_task()
        start = domain.from_vikunja_time(task_data["start_date"]).astimezone(TZ)
        end = domain.from_vikunja_time(task_data["end_date"]).astimezone(TZ)
        self.assertEqual(start, datetime(2026, 9, 23, 14, 0, tzinfo=TZ))
        self.assertEqual(end - start, timedelta(hours=1))

    async def test_repeating_task_also_gets_the_anchor(self):
        await self.plugin.vikunja_create_task(self.event, "每周日浇花")
        task_data = self._only_task()
        self.assertIn("start_date", task_data)
        self.assertEqual(task_data["repeat_after"], 604800)
        # The anchor is not a block, so the chore does not claim today's time.
        self.assertFalse(domain.is_time_block(task_data, TZ))


if __name__ == "__main__":
    unittest.main()
