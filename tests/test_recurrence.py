"""Automatic recurrence routing and the self-enforced 'repeat until' limit."""

import types
import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from state_store import StateStore

from plugin_loader import domain, load_plugin_class

TZ = ZoneInfo("Asia/Shanghai")
# A Wednesday, so weekday anchoring is observable.
NOW = datetime(2026, 9, 23, 10, 0, tzinfo=TZ)


class RecurrenceDetectionTests(unittest.TestCase):
    def test_weekday_rhythm_anchors_on_that_weekday(self):
        recurrence = domain.detect_recurrence("每周日浇花")
        self.assertEqual(recurrence.repeat_after, 604800)
        # Calendar rhythms stay anchored to the original date, not the completion day.
        self.assertEqual(recurrence.repeat_mode, domain.REPEAT_MODE_DEFAULT)
        self.assertEqual(recurrence.weekday, 6)
        self.assertEqual(recurrence.describe, "每周日")
        first = domain.recurrence_first_due(recurrence, TZ, NOW)
        self.assertEqual(first.date(), datetime(2026, 9, 27).date())
        self.assertEqual(first.weekday(), 6)

    def test_daily_check_in(self):
        recurrence = domain.detect_recurrence("每天看一下计算跑得怎么样")
        self.assertEqual(recurrence.repeat_after, 86400)
        self.assertEqual(recurrence.repeat_mode, domain.REPEAT_MODE_DEFAULT)
        first = domain.recurrence_first_due(recurrence, TZ, NOW)
        self.assertEqual(first.date(), NOW.date())

    def test_day_of_month_rhythm_uses_monthly_mode(self):
        recurrence = domain.detect_recurrence("每月5号交月报")
        self.assertEqual(recurrence.repeat_mode, domain.REPEAT_MODE_MONTH)
        self.assertEqual(recurrence.day_of_month, 5)
        self.assertEqual(recurrence.describe, "每月5号")
        first = domain.recurrence_first_due(recurrence, TZ, NOW)
        self.assertEqual((first.month, first.day), (10, 5))

    def test_interval_without_anchor_counts_from_completion(self):
        for title in ("每隔三天浇花", "隔天跑步", "每3天备份一次"):
            with self.subTest(title=title):
                recurrence = domain.detect_recurrence(title)
                self.assertEqual(
                    recurrence.repeat_mode, domain.REPEAT_MODE_FROM_COMPLETION
                )

    def test_week_and_month_intervals_stay_anchored(self):
        self.assertEqual(
            domain.detect_recurrence("每两周复盘").repeat_mode,
            domain.REPEAT_MODE_DEFAULT,
        )
        self.assertEqual(domain.detect_recurrence("每2周开会").repeat_after, 1209600)

    def test_one_off_tasks_are_not_turned_into_rhythms(self):
        for title in ("写引言", "周三要交周报", "明天买牛奶", "这周把数据整理完"):
            with self.subTest(title=title):
                self.assertIsNone(domain.detect_recurrence(title))

    def test_first_due_rolls_to_next_week_when_today_already_passed(self):
        late = datetime(2026, 9, 27, 23, 30, tzinfo=TZ)  # Sunday night
        recurrence = domain.detect_recurrence("每周日浇花")
        first = domain.recurrence_first_due(recurrence, TZ, late)
        self.assertEqual(first.date(), datetime(2026, 10, 4).date())


class RepeatLimitMarkerTests(unittest.TestCase):
    def test_date_limit_round_trips_through_the_description(self):
        line = domain.repeat_limit_line(datetime(2026, 10, 31, tzinfo=TZ))
        html = domain.description_to_html("每天看进度\n" + line)
        until, condition = domain.parse_repeat_limit(html, TZ)
        self.assertEqual(until.date(), datetime(2026, 10, 31).date())
        self.assertEqual(condition, "")
        # The marker is plain readable text, so editing it in the web UI still works.
        self.assertIn("重复至 2026-10-31", domain.html_to_text(html))

    def test_condition_limit_is_kept_as_text(self):
        html = domain.description_to_html(domain.repeat_limit_line(None, "计算跑完"))
        until, condition = domain.parse_repeat_limit(html, TZ)
        self.assertIsNone(until)
        self.assertEqual(condition, "计算跑完")

    def test_strip_removes_only_the_marker(self):
        html = domain.description_to_html(
            "目标：盯着任务\n"
            + domain.repeat_limit_line(datetime(2026, 10, 31, tzinfo=TZ))
        )
        stripped = domain.strip_repeat_limit(html)
        self.assertIn("目标：盯着任务", stripped)
        self.assertEqual(domain.parse_repeat_limit(stripped, TZ), (None, ""))

    def test_repeat_label_mentions_the_limit(self):
        html = domain.description_to_html(
            domain.repeat_limit_line(datetime(2026, 10, 31, tzinfo=TZ))
        )
        self.assertEqual(
            domain.format_repeat({"repeat_after": 604800, "description": html}),
            "每周 至2026-10-31",
        )


class SlashCommandRecurrenceTests(unittest.TestCase):
    def test_repeat_accepts_spoken_rhythm_and_anchors_first_due(self):
        spec = domain.parse_add_arguments("浇花 --repeat 每周日", TZ, NOW)
        self.assertEqual(spec.repeat_after, 604800)
        self.assertEqual(spec.due.weekday(), 6)

    def test_repeat_until_is_written_into_the_description(self):
        spec = domain.parse_add_arguments(
            "浇花 --repeat 每周日 --repeat-until 2026-12-31", TZ, NOW
        )
        until, _ = domain.parse_repeat_limit(spec.description, TZ)
        self.assertEqual(until.date(), datetime(2026, 12, 31).date())
        self.assertIn("重复至 2026-12-31", spec.payload()["description"])

    def test_repeat_until_condition_text(self):
        spec = domain.parse_add_arguments(
            '看计算 --repeat daily --due "2026-09-23T21:00" --until "计算跑完"',
            TZ,
            NOW,
        )
        self.assertEqual(domain.parse_repeat_limit(spec.description, TZ)[1], "计算跑完")

    def test_repeat_until_without_repeat_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "需要同时设置 --repeat"):
            domain.parse_add_arguments("浇花 --repeat-until 2026-12-31", TZ, NOW)


class FakeVikunja:
    configured = True
    web_url = "https://vkj.example.com"

    def __init__(self):
        self.projects = [{"id": 1, "title": "Inbox", "parent_project_id": 0}]
        self.labels = []
        self.tasks = {}
        self.comments = []
        self._next = 200

    async def list_projects(self):
        return list(self.projects)

    async def list_labels(self):
        return list(self.labels)

    async def create_label(self, title, hex_color=""):
        self._next += 1
        label = {"id": self._next, "title": title}
        self.labels.append(label)
        return label

    async def add_label_to_task(self, task_id, label_id):
        title = next(
            (label["title"] for label in self.labels if label["id"] == label_id), ""
        )
        self.tasks[int(task_id)].setdefault("labels", []).append(
            {"id": label_id, "title": title}
        )

    async def create_task(self, project_id, payload):
        self._next += 1
        task = {
            "id": self._next,
            "project_id": project_id,
            "done": False,
            "priority": 0,
            "labels": [],
            **payload,
        }
        self.tasks[task["id"]] = task
        return dict(task)

    async def get_task(self, task_id):
        return dict(self.tasks[int(task_id)])

    async def update_task(self, task_id, changes):
        task = self.tasks[int(task_id)]
        for key, value in changes.items():
            task[key] = "0001-01-01T00:00:00Z" if value is None else value
        return dict(task)

    async def list_tasks(self, filter_query="", **kwargs):
        return [dict(task) for task in self.tasks.values() if not task.get("done")]

    async def create_comment(self, task_id, text):
        self.comments.append((int(task_id), text))
        return {"id": 1, "comment": text}


class RecurrenceRoutingTests(unittest.IsolatedAsyncioTestCase):
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
        self.sent = []

        async def send_message(umo, chain):
            self.sent.append((umo, chain.text))
            return True

        self.plugin.context = types.SimpleNamespace(send_message=send_message)
        self.event = types.SimpleNamespace(
            unified_msg_origin="qq",
            get_group_id=lambda: "",
            get_platform_name=lambda: "qq_official",
            get_sender_id=lambda: "me",
        )

    def _only_task(self):
        return list(self.plugin.client.tasks.values())[0]

    async def test_weekly_chore_becomes_a_repeating_task_without_the_model_asking(self):
        result = await self.plugin.vikunja_create_task(self.event, "每周日浇花")
        task = self._only_task()
        self.assertEqual(task["repeat_after"], 604800)
        self.assertEqual(task.get("repeat_mode", 0), 0)
        due = domain.from_vikunja_time(task["due_date"]).astimezone(TZ)
        self.assertEqual(due.weekday(), 6)
        self.assertEqual(due.hour, 21)
        self.assertIn("已设为 每周日 重复", result)
        self.assertIn("还没约定重复到什么时候结束", result)

    async def test_daily_monitoring_task_with_an_end_date(self):
        result = await self.plugin.vikunja_create_task(
            self.event,
            "每天看一下 SMX 计算跑得怎么样",
            repeat_until="2026-10-05",
        )
        task = self._only_task()
        self.assertEqual(task["repeat_after"], 86400)
        until, _ = domain.parse_repeat_limit(task["description"], TZ)
        self.assertEqual(until.date(), datetime(2026, 10, 5).date())
        self.assertIn("重复到 2026-10-05 为止", result)

    async def test_condition_based_end_is_recorded_and_surfaced_in_reminders(self):
        await self.plugin.vikunja_create_task(
            self.event, "每天看一下计算", repeat_until="计算跑完"
        )
        task = self._only_task()
        self.assertEqual(
            domain.parse_repeat_limit(task["description"], TZ)[1], "计算跑完"
        )
        text = self.plugin._reminder_text(task, {1: "Inbox"}, NOW)
        self.assertIn("重复到「计算跑完」为止", text)

    async def test_explicit_repeat_from_the_model_is_respected(self):
        await self.plugin.vikunja_create_task(
            self.event,
            "浇花",
            due="2026-09-27T09:00",
            repeat="3d",
            repeat_from_completion=True,
        )
        task = self._only_task()
        self.assertEqual(task["repeat_after"], 259200)
        self.assertEqual(task["repeat_mode"], 2)

    async def test_one_off_task_is_left_alone(self):
        result = await self.plugin.vikunja_create_task(self.event, "写引言")
        task = self._only_task()
        self.assertEqual(task.get("repeat_after", 0), 0)
        self.assertNotIn("重复", result)

    async def test_limit_can_be_added_later_and_replaced(self):
        await self.plugin.vikunja_create_task(self.event, "每周日浇花")
        task_id = self._only_task()["id"]
        await self.plugin.vikunja_update_task(
            self.event, task_id, repeat_until="2026-12-31"
        )
        description = self.plugin.client.tasks[task_id]["description"]
        self.assertEqual(
            domain.parse_repeat_limit(description, TZ)[0].date(),
            datetime(2026, 12, 31).date(),
        )
        await self.plugin.vikunja_update_task(
            self.event, task_id, repeat_until="2027-01-31"
        )
        description = self.plugin.client.tasks[task_id]["description"]
        self.assertEqual(description.count("重复至"), 1)
        self.assertEqual(
            domain.parse_repeat_limit(description, TZ)[0].date(),
            datetime(2027, 1, 31).date(),
        )
        await self.plugin.vikunja_update_task(self.event, task_id, repeat_until="none")
        self.assertEqual(
            domain.parse_repeat_limit(
                self.plugin.client.tasks[task_id]["description"], TZ
            ),
            (None, ""),
        )

    async def test_scheduler_stops_the_recurrence_after_the_agreed_date(self):
        await self.plugin.vikunja_create_task(
            self.event, "每天看一下计算", repeat_until="2026-09-30"
        )
        task_id = self._only_task()["id"]

        # Before the end date nothing changes.
        await self.plugin._enforce_repeat_limits()
        self.assertEqual(self.plugin.client.tasks[task_id]["repeat_after"], 86400)
        self.assertEqual(self.sent, [])

        self.plugin._now = lambda: NOW + timedelta(days=10)
        await self.plugin._enforce_repeat_limits()
        self.assertEqual(self.plugin.client.tasks[task_id]["repeat_after"], 0)
        self.assertEqual(self.plugin.client.tasks[task_id]["repeat_mode"], 0)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("已停止重复", self.sent[0][1])
        self.assertTrue(
            any("已自动取消重复规则" in text for _, text in self.plugin.client.comments)
        )

        # Idempotent: the notice is not repeated on the next poll.
        await self.plugin._enforce_repeat_limits()
        self.assertEqual(len(self.sent), 1)

    async def test_condition_limits_are_never_auto_stopped(self):
        await self.plugin.vikunja_create_task(
            self.event, "每天看一下计算", repeat_until="计算跑完"
        )
        task_id = self._only_task()["id"]
        self.plugin._now = lambda: NOW + timedelta(days=400)
        await self.plugin._enforce_repeat_limits()
        self.assertEqual(self.plugin.client.tasks[task_id]["repeat_after"], 86400)
        self.assertEqual(self.sent, [])


if __name__ == "__main__":
    unittest.main()
