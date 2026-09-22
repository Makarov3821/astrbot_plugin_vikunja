"""Description HTML, active labelling, progress and repeat handling."""

import unittest

from plugin_loader import domain

checklist_progress = domain.checklist_progress
description_to_html = domain.description_to_html
format_repeat = domain.format_repeat
html_to_text = domain.html_to_text
label_usage = domain.label_usage
parse_repeat = domain.parse_repeat
suggest_labels = domain.suggest_labels
AddSpec = domain.AddSpec
REPEAT_MODE_FROM_COMPLETION = domain.REPEAT_MODE_FROM_COMPLETION
parse_add_arguments = domain.parse_add_arguments


class DescriptionTests(unittest.TestCase):
    def test_plain_lines_become_paragraphs_and_escape_html(self):
        html = description_to_html("先读文献\n再写提纲 <b>不要</b> 当标签")
        self.assertEqual(
            html,
            "<p>先读文献</p><p>再写提纲 &lt;b&gt;不要&lt;/b&gt; 当标签</p>",
        )

    def test_checklist_lines_become_tiptap_task_items(self):
        html = description_to_html("- [ ] 找数据\n- [x] 读综述\n- 普通条目")
        self.assertIn('<ul data-type="taskList">', html)
        self.assertIn(
            '<li data-checked="false" data-type="taskItem"><p>找数据</p></li>', html
        )
        self.assertIn(
            '<li data-checked="true" data-type="taskItem"><p>读综述</p></li>', html
        )
        self.assertIn("<ul><li><p>普通条目</p></li></ul>", html)
        # Vikunja's web UI counts progress exactly like this.
        self.assertEqual(checklist_progress(html), (1, 2))

    def test_existing_html_is_left_alone(self):
        original = "<p>已经是 HTML</p>"
        self.assertEqual(description_to_html(original), original)
        self.assertEqual(description_to_html("  "), "")

    def test_html_round_trips_back_to_readable_text(self):
        html = description_to_html("目标：跑通流程\n- [x] 装环境\n- [ ] 跑 demo")
        text = html_to_text(html)
        self.assertEqual(text, "目标：跑通流程\n[x] 装环境\n[ ] 跑 demo")
        self.assertEqual(html_to_text("<p>a&amp;b</p><br>c"), "a&b\nc")
        self.assertEqual(html_to_text(""), "")

    def test_add_spec_sends_html_description(self):
        payload = AddSpec(title="写引言", description="- [ ] 列大纲").payload()
        self.assertIn('data-checked="false"', payload["description"])


class LabelTests(unittest.TestCase):
    def test_errand_wording_gets_outside_and_short_estimate(self):
        self.assertEqual(suggest_labels("下班去便利店买牛奶"), ["est15", "@外出"])

    def test_deep_work_wording_gets_deep_and_long_estimate(self):
        self.assertEqual(suggest_labels("重写课题一的引言"), ["est2h", "@深度"])

    def test_explicit_duration_in_title_wins(self):
        self.assertEqual(suggest_labels("花半小时整理文献"), ["est30", "@深度"])

    def test_contacting_people_and_waiting(self):
        self.assertEqual(suggest_labels("问师兄要仿真数据"), ["est30", "@要找人"])
        self.assertEqual(suggest_labels("等师兄给数据"), ["est15", "@等待中"])

    def test_recurring_check_ins_and_chores_are_short_not_deep_work(self):
        # "跑" alone looks like deep work; a daily check-in is not.
        self.assertEqual(suggest_labels("每天看一下计算跑得怎么样"), ["est15", "@碎片"])
        self.assertEqual(suggest_labels("每周日浇花"), ["est15", "@碎片"])
        self.assertEqual(suggest_labels("每周三组会"), ["est30", "@要找人"])

    def test_unknown_wording_returns_nothing_rather_than_guessing_wildly(self):
        self.assertEqual(suggest_labels("xyz"), [])

    def test_label_usage_counts_across_tasks(self):
        tasks = [
            {"labels": [{"title": "est1h"}, {"title": "@深度"}]},
            {"labels": [{"title": "est1h"}]},
            {"labels": []},
        ]
        self.assertEqual(label_usage(tasks), {"est1h": 2, "@深度": 1})


class RepeatTests(unittest.TestCase):
    def test_new_aliases(self):
        self.assertEqual(parse_repeat("每两周"), (1209600, 0))
        self.assertEqual(parse_repeat("每年"), (31536000, 0))
        self.assertEqual(parse_repeat("每3天"), (259200, 0))
        self.assertEqual(parse_repeat("monthly"), (0, 1))
        self.assertEqual(parse_repeat(""), (0, 0))
        with self.assertRaisesRegex(ValueError, "重复规则可用"):
            parse_repeat("有空就做")

    def test_from_completion_flag_sets_repeat_mode_two(self):
        spec = parse_add_arguments(
            '浇花 --due "2026-09-23T09:00" --repeat 3d --from-completion',
            domain.get_timezone("Asia/Shanghai"),
        )
        self.assertEqual(spec.repeat_after, 259200)
        self.assertEqual(spec.repeat_mode, REPEAT_MODE_FROM_COMPLETION)
        payload = spec.payload()
        self.assertEqual(payload["repeat_mode"], 2)

    def test_repeat_allows_start_date_only(self):
        spec = parse_add_arguments(
            '每周复盘 --start "2026-09-23T20:00" --repeat weekly',
            domain.get_timezone("Asia/Shanghai"),
        )
        self.assertEqual(spec.repeat_after, 604800)

    def test_repeat_without_any_date_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "--due 或 --start"):
            parse_add_arguments(
                "浇花 --repeat 3d", domain.get_timezone("Asia/Shanghai")
            )

    def test_human_readable_repeat(self):
        self.assertEqual(format_repeat({"repeat_after": 86400}), "每天")
        self.assertEqual(format_repeat({"repeat_after": 259200}), "每3天")
        self.assertEqual(format_repeat({"repeat_after": 604800}), "每周")
        self.assertEqual(format_repeat({"repeat_mode": 1}), "每月")
        self.assertEqual(
            format_repeat({"repeat_after": 259200, "repeat_mode": 2}), "每3天(完成后算)"
        )
        self.assertEqual(format_repeat({}), "")


class ProgressTests(unittest.TestCase):
    def test_progress_flag_is_stored_as_fraction(self):
        # Vikunja models percent_done as a float between 0 and 1.
        spec = parse_add_arguments(
            "写引言 --progress 50", domain.get_timezone("Asia/Shanghai")
        )
        self.assertEqual(spec.payload()["percent_done"], 0.5)

    def test_percent_display_handles_both_encodings(self):
        self.assertEqual(domain.format_percent({"percent_done": 0.5}), "50%")
        self.assertEqual(domain.format_percent({"percent_done": 80}), "80%")
        self.assertEqual(domain.format_percent({"percent_done": 0}), "")

    def test_task_line_shows_checklist_and_repeat(self):
        line = domain.format_task_line(
            {
                "id": 5,
                "title": "写引言",
                "description": description_to_html("- [x] 大纲\n- [ ] 初稿"),
                "repeat_after": 604800,
                "percent_done": 0.5,
            },
            domain.get_timezone("Asia/Shanghai"),
        )
        self.assertIn("☑ 1/2", line)
        self.assertIn("↻ 每周", line)
        self.assertIn("📈 50%", line)


if __name__ == "__main__":
    unittest.main()
