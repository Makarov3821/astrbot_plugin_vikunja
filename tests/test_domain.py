from datetime import datetime
import unittest
from zoneinfo import ZoneInfo

from todo_domain import (
    build_project_paths,
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
            {"id": 1, "title": "低", "priority": 1, "due_date": "2026-07-10T12:00:00Z", "done": False},
            {"id": 2, "title": "高", "priority": 5, "due_date": "2026-07-11T12:00:00Z", "done": False},
            {"id": 3, "title": "未来", "priority": 5, "due_date": "2026-07-13T12:00:00Z", "done": False},
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
        self.assertIn("目标项目", reason)
        self.assertIn("周期较长", reason)

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
        self.assertTrue(platform_sender_is_allowed("weixin_oc", "any-weixin-id", qq_ids))

    def test_reminder_with_date_but_no_clock_time_requires_clarification(self):
        reason = secretary_clarification_reason(
            "买牛奶", due="明天", repeat="", project="", is_reminder=True
        )
        self.assertIn("具体时间", reason)


if __name__ == "__main__":
    unittest.main()
