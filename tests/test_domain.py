from datetime import datetime
import unittest
from zoneinfo import ZoneInfo

from todo_domain import (
    AddSpec,
    build_project_paths,
    build_reminders,
    format_task_line,
    scope_filter_query,
    format_task_list,
    format_project_tree,
    parse_add_arguments,
    parse_datetime,
    parse_repeat,
    platform_sender_is_allowed,
    resolve_project,
    secretary_clarification_reason,
    sender_is_allowed,
    select_tasks,
)


class DomainTests(unittest.TestCase):
    def setUp(self):
        self.tz = ZoneInfo("Asia/Shanghai")
        self.now = datetime(2026, 7, 11, 10, 0, tzinfo=self.tz)

    def test_parse_chinese_due_time(self):
        result = parse_datetime("明天18点30分", self.tz, self.now)
        self.assertEqual(result, datetime(2026, 7, 12, 18, 30, tzinfo=self.tz))

    def test_parse_weekday_due_time(self):
        result = parse_datetime("周日 20:00", self.tz, self.now)
        self.assertEqual(result, datetime(2026, 7, 12, 20, 0, tzinfo=self.tz))

    def test_parse_add_with_repeat_and_reminder(self):
        spec = parse_add_arguments(
            '每日复盘 --due "今天 22:00" --priority 4 --repeat daily --remind 1h',
            self.tz,
            self.now,
        )
        self.assertEqual(spec.title, "每日复盘")
        self.assertEqual(spec.priority, 4)
        self.assertEqual(spec.repeat_after, 86400)
        self.assertEqual(spec.reminder_minutes, 60)

    def test_monthly_repeat_uses_repeat_mode(self):
        self.assertEqual(parse_repeat("monthly"), (0, 1))

    def test_custom_reminder_requires_due_date(self):
        with self.assertRaisesRegex(ValueError, "必须同时设置"):
            parse_add_arguments("写周报 --remind 30m", self.tz, self.now)

    def test_today_includes_overdue_and_sorts_priority(self):
        tasks = [
            {
                "id": 1,
                "title": "低",
                "priority": 1,
                "due_date": "2026-07-10T12:00:00Z",
                "done": False,
            },
            {
                "id": 2,
                "title": "高",
                "priority": 5,
                "due_date": "2026-07-11T12:00:00Z",
                "done": False,
            },
            {
                "id": 3,
                "title": "未来",
                "priority": 5,
                "due_date": "2026-07-13T12:00:00Z",
                "done": False,
            },
        ]
        selected = select_tasks(tasks, "today", self.tz, self.now)
        self.assertEqual([task["id"] for task in selected], [2, 1])
        text = format_task_list(selected, "今日", self.tz)
        self.assertIn("[P5] #2 高", text)

    def test_resolve_nested_project_path(self):
        projects = [
            {"id": 1, "title": "Inbox", "parent_project_id": 0},
            {"id": 2, "title": "fudan-work", "parent_project_id": 0},
            {"id": 3, "title": "SMX", "parent_project_id": 2},
            {"id": 4, "title": "paper", "parent_project_id": 3},
        ]
        paths = build_project_paths(projects)
        self.assertEqual(paths[4], "fudan-work/SMX/paper")
        project, path = resolve_project(projects, "fudan-work/smx/PAPER")
        self.assertEqual(project["id"], 4)
        self.assertEqual(path, "fudan-work/SMX/paper")
        tree = format_project_tree(projects)
        self.assertIn("    • paper (#4)", tree)

    def test_ambiguous_project_title_requires_path(self):
        projects = [
            {"id": 1, "title": "work", "parent_project_id": 0},
            {"id": 2, "title": "paper", "parent_project_id": 1},
            {"id": 3, "title": "personal", "parent_project_id": 0},
            {"id": 4, "title": "paper", "parent_project_id": 3},
        ]
        with self.assertRaisesRegex(ValueError, "不唯一"):
            resolve_project(projects, "paper")

    def test_secretary_guard_combines_missing_information(self):
        reason = secretary_clarification_reason(
            "推进 SMX paper", due="", repeat="", project="", is_reminder=True
        )
        self.assertIn("具体时间", reason)
        self.assertNotIn("目标项目", reason)
        self.assertNotIn("周期较长", reason)

    def test_unscheduled_research_is_allowed(self):
        self.assertIsNone(
            secretary_clarification_reason("思考论文方案", "", "", "", False)
        )

    def test_next_week_is_calendar_week(self):
        self.assertEqual(
            parse_datetime("下周二14点", self.tz, self.now),
            datetime(2026, 7, 14, 14, tzinfo=self.tz),
        )

    def test_secretary_guard_allows_clear_everyday_task(self):
        self.assertIsNone(
            secretary_clarification_reason(
                "买牛奶", due="明天18点", repeat="", project="", is_reminder=True
            )
        )

    def test_sender_whitelist(self):
        self.assertTrue(sender_is_allowed("qq-uid", []))
        self.assertTrue(sender_is_allowed("qq-uid", ["qq-uid", "wx-id"]))
        self.assertFalse(sender_is_allowed("someone-else", ["qq-uid", "wx-id"]))

    def test_qq_whitelist_does_not_block_weixin(self):
        qq_ids = ["my-qq-uid"]
        self.assertTrue(platform_sender_is_allowed("qq_official", "my-qq-uid", qq_ids))
        self.assertFalse(platform_sender_is_allowed("qq_official", "other-qq", qq_ids))
        self.assertTrue(
            platform_sender_is_allowed("weixin_oc", "any-weixin-id", qq_ids)
        )

    def test_reminder_with_date_but_no_clock_time_requires_clarification(self):
        reason = secretary_clarification_reason(
            "买牛奶", due="明天", repeat="", project="", is_reminder=True
        )
        self.assertIn("具体时间", reason)

    def test_reminder_accepts_separate_remind_time(self):
        self.assertIsNone(
            secretary_clarification_reason(
                "买牛奶",
                due="",
                repeat="",
                project="",
                is_reminder=True,
                reminder_at="2026-09-20T18:30",
            )
        )

    def test_parse_iso_and_meridiem_expressions(self):
        self.assertEqual(
            parse_datetime("2026-09-20T18:30", self.tz, self.now),
            datetime(2026, 9, 20, 18, 30, tzinfo=self.tz),
        )
        self.assertEqual(
            parse_datetime("2026-09-20T10:30:00Z", self.tz, self.now),
            datetime(2026, 9, 20, 18, 30, tzinfo=self.tz),
        )
        self.assertEqual(
            parse_datetime("下周三下午3点", self.tz, self.now),
            datetime(2026, 7, 15, 15, 0, tzinfo=self.tz),
        )
        self.assertEqual(
            parse_datetime("明天晚上8点半", self.tz, self.now),
            datetime(2026, 7, 12, 20, 30, tzinfo=self.tz),
        )
        self.assertEqual(
            parse_datetime("今晚9点", self.tz, self.now),
            datetime(2026, 7, 11, 21, 0, tzinfo=self.tz),
        )

    def test_add_arguments_support_time_blocks_and_labels(self):
        spec = parse_add_arguments(
            '写引言 --start "2026-07-12T09:00" --end "2026-07-12T11:00" '
            "--label est2h,@深度 --priority 4",
            self.tz,
            self.now,
        )
        self.assertEqual(spec.labels, ["est2h", "@深度"])
        payload = spec.payload()
        self.assertEqual(payload["start_date"], "2026-07-12T01:00:00Z")
        self.assertEqual(payload["end_date"], "2026-07-12T03:00:00Z")
        self.assertNotIn("due_date", payload)
        self.assertNotIn("reminders", payload)

    def test_end_must_follow_start(self):
        with self.assertRaisesRegex(ValueError, "必须晚于"):
            parse_add_arguments(
                '开会 --start "2026-07-12T11:00" --end "2026-07-12T10:00"',
                self.tz,
                self.now,
            )

    def test_reminders_payload_mixes_absolute_and_relative(self):
        due = datetime(2026, 7, 12, 18, 0, tzinfo=self.tz)
        absolute = datetime(2026, 7, 12, 9, 0, tzinfo=self.tz)
        entries = build_reminders([absolute], 30, has_due=True)
        self.assertEqual(entries[0]["reminder"], "2026-07-12T01:00:00Z")
        self.assertEqual(entries[1]["relative_to"], "due_date")
        self.assertEqual(entries[1]["relative_period"], -1800)
        payload = AddSpec(title="交周报", due=due, reminder_minutes=60).payload()
        self.assertEqual(payload["reminders"][0]["relative_period"], -3600)
        # Without any date there is nothing to anchor a relative reminder to.
        self.assertEqual(build_reminders(None, 30), [])

    def test_scope_selection_understands_time_blocks_and_labels(self):
        tasks = [
            {"id": 1, "title": "块", "start_date": "2026-07-11T02:00:00Z"},
            {"id": 2, "title": "没排期"},
            {
                "id": 3,
                "title": "等人",
                "labels": [{"id": 9, "title": "@等待中"}],
            },
        ]
        self.assertEqual(
            [t["id"] for t in select_tasks(tasks, "today", self.tz, self.now)], [1]
        )
        self.assertEqual(
            [t["id"] for t in select_tasks(tasks, "scheduled", self.tz, self.now)], [1]
        )
        self.assertEqual(
            [t["id"] for t in select_tasks(tasks, "unscheduled", self.tz, self.now)],
            [2, 3],
        )
        self.assertEqual(
            [t["id"] for t in select_tasks(tasks, "waiting", self.tz, self.now)], [3]
        )
        query, include_nulls = scope_filter_query("today")
        self.assertIn("now/d+1d", query)
        self.assertFalse(include_nulls)

    def test_task_line_shows_block_labels_and_progress(self):
        line = format_task_line(
            {
                "id": 7,
                "title": "写引言",
                "priority": 4,
                "start_date": "2026-07-11T01:00:00Z",
                "end_date": "2026-07-11T03:00:00Z",
                "percent_done": 0.5,
                "labels": [{"id": 1, "title": "est2h"}],
            },
            self.tz,
            {0: "未知项目"},
        )
        self.assertIn("09:00-11:00", line)
        self.assertIn("est2h", line)
        self.assertIn("50%", line)


if __name__ == "__main__":
    unittest.main()
