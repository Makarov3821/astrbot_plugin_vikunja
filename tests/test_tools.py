import types
import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from state_store import StateStore

from plugin_loader import load_plugin_class

TZ = ZoneInfo("Asia/Shanghai")


class FakeVikunja:
    """In-memory Vikunja good enough to exercise the plugin's tool layer."""

    configured = True
    web_url = "https://vkj.example.com"

    def __init__(self):
        self.projects = [
            {"id": 1, "title": "Inbox", "parent_project_id": 0},
            {"id": 2, "title": "PhD", "parent_project_id": 0},
            {"id": 3, "title": "课题一", "parent_project_id": 2},
        ]
        self.labels = [{"id": 11, "title": "est1h"}, {"id": 12, "title": "@深度"}]
        self.tasks: dict[int, dict] = {}
        self.relations: list[tuple] = []
        self.comments: list[tuple] = []
        self._next = 100

    async def list_projects(self):
        return list(self.projects)

    async def list_labels(self):
        return list(self.labels)

    async def create_label(self, title, hex_color=""):
        label = {"id": self._new_id(), "title": title}
        self.labels.append(label)
        return label

    def _new_id(self):
        self._next += 1
        return self._next

    async def create_task(self, project_id, payload):
        task = {
            "id": self._new_id(),
            "project_id": project_id,
            "done": False,
            "priority": 0,
            "labels": [],
            **payload,
        }
        self.tasks[task["id"]] = task
        return dict(task)

    async def get_task(self, task_id):
        task = dict(self.tasks[int(task_id)])
        task["related_tasks"] = self._related(int(task_id))
        return task

    def _related(self, task_id):
        """Mirror Vikunja's related_tasks map, including the inverse relations."""
        inverse = {
            "parenttask": "subtask",
            "subtask": "parenttask",
            "blocked": "blocking",
            "blocking": "blocked",
            "precedes": "follows",
            "follows": "precedes",
            "related": "related",
        }
        grouped: dict[str, list[dict]] = {}
        for source, other, kind in self.relations:
            if int(source) == task_id:
                grouped.setdefault(kind, []).append(dict(self.tasks[int(other)]))
            elif int(other) == task_id:
                grouped.setdefault(inverse[kind], []).append(
                    dict(self.tasks[int(source)])
                )
        return grouped

    async def list_comments(self, task_id):
        return [
            {"id": index, "comment": text, "created": "2026-09-22T10:00:00Z"}
            for index, (target, text) in enumerate(self.comments, 1)
            if int(target) == int(task_id)
        ]

    async def update_task(self, task_id, changes):
        task = self.tasks[int(task_id)]
        for key, value in changes.items():
            task[key] = "0001-01-01T00:00:00Z" if value is None else value
        return dict(task)

    async def complete_task(self, task_id):
        return await self.update_task(task_id, {"done": True})

    async def delete_task(self, task_id):
        self.tasks.pop(int(task_id), None)

    async def list_tasks(self, filter_query="", **kwargs):
        project_id = kwargs.get("project_id")
        tasks = [dict(task) for task in self.tasks.values() if not task.get("done")]
        if project_id is not None:
            tasks = [t for t in tasks if int(t["project_id"]) == int(project_id)]
        return tasks

    async def add_label_to_task(self, task_id, label_id):
        title = next(
            (label["title"] for label in self.labels if label["id"] == label_id), ""
        )
        self.tasks[int(task_id)].setdefault("labels", []).append(
            {"id": label_id, "title": title}
        )

    async def remove_label_from_task(self, task_id, label_id):
        task = self.tasks[int(task_id)]
        task["labels"] = [
            label for label in task.get("labels", []) if label["id"] != label_id
        ]

    async def create_relation(self, task_id, other_task_id, kind):
        self.relations.append((task_id, other_task_id, kind))

    async def create_comment(self, task_id, text):
        self.comments.append((task_id, text))
        return {"id": self._new_id(), "comment": text}


class ToolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.saved = {}

        async def load():
            return self.saved

        async def save(value):
            self.saved = value

        self.state = StateStore(load, save)
        await self.state.initialize()
        plugin_class = load_plugin_class()
        self.plugin = plugin_class.__new__(plugin_class)
        self.plugin.state = self.state
        self.plugin.tz = TZ
        self.plugin.tz_name = "Asia/Shanghai"
        self.plugin.config = {
            "default_project": "Inbox",
            "max_list_items": 20,
            "work_windows": "09:00-12:00",
            "default_block_minutes": 30,
        }
        self.plugin._ready = True
        self.plugin._projects_cache = None
        self.plugin._labels_cache = None
        self.plugin.client = FakeVikunja()
        self.plugin.SUPPORTED_PLATFORMS = plugin_class.SUPPORTED_PLATFORMS
        self.event = types.SimpleNamespace(
            unified_msg_origin="qq",
            get_group_id=lambda: "",
            get_platform_name=lambda: "qq_official",
            get_sender_id=lambda: "me",
        )

    async def test_create_task_with_block_labels_and_reminder(self):
        result = await self.plugin.vikunja_create_task(
            self.event,
            "写引言",
            project="PhD/课题一",
            start="2026-09-21T09:00",
            end="2026-09-21T11:00",
            priority=4,
            labels="est2h,@深度",
            remind_before_minutes=15,
            due="2026-09-25T18:00",
        )
        self.assertIn("已创建", result)
        self.assertIn("PhD/课题一", result)
        task = list(self.plugin.client.tasks.values())[0]
        self.assertEqual(task["project_id"], 3)
        self.assertEqual(task["start_date"], "2026-09-21T01:00:00Z")
        self.assertEqual(task["reminders"][0]["relative_period"], -900)
        self.assertEqual(
            [label["title"] for label in task["labels"]], ["est2h", "@深度"]
        )
        # est2h did not exist yet and was created on the fly
        self.assertIn("est2h", [label["title"] for label in self.plugin.client.labels])

    async def test_reminder_without_any_time_is_refused(self):
        result = await self.plugin.vikunja_create_task(
            self.event, "提醒我买牛奶", is_reminder=True
        )
        self.assertIn("需要先向用户确认", result)
        self.assertEqual(self.plugin.client.tasks, {})

    async def test_update_reschedules_clears_and_relabels(self):
        await self.plugin.vikunja_create_task(
            self.event, "交周报", due="2026-09-21T18:00", labels="est1h"
        )
        task_id = next(iter(self.plugin.client.tasks))
        result = await self.plugin.vikunja_update_task(
            self.event,
            task_id,
            due="2026-09-23T18:00",
            start="none",
            priority=5,
            percent_done=50,
            add_labels="@等待中",
            remove_labels="est1h",
        )
        self.assertIn("已更新", result)
        task = self.plugin.client.tasks[task_id]
        self.assertEqual(task["due_date"], "2026-09-23T10:00:00Z")
        self.assertEqual(task["start_date"], "0001-01-01T00:00:00Z")
        self.assertEqual(task["priority"], 5)
        self.assertEqual(task["percent_done"], 0.5)
        self.assertEqual([label["title"] for label in task["labels"]], ["@等待中"])

    async def test_subtask_is_linked_to_parent_project(self):
        await self.plugin.vikunja_create_task(
            self.event, "写论文", project="PhD/课题一"
        )
        parent_id = next(iter(self.plugin.client.tasks))
        result = await self.plugin.vikunja_add_subtask(
            self.event, parent_id, "列引言大纲", labels="est30"
        )
        self.assertIn("子任务", result)
        child = [t for t in self.plugin.client.tasks.values() if t["id"] != parent_id][
            0
        ]
        self.assertEqual(child["project_id"], 3)
        self.assertIn(
            (child["id"], parent_id, "parenttask"), self.plugin.client.relations
        )

    async def test_plan_day_writes_blocks_without_double_booking(self):
        now = datetime.now(TZ).replace(hour=9, minute=0, second=0, microsecond=0)
        self.plugin._now = lambda: now
        await self.plugin.vikunja_create_task(
            self.event,
            "逾期的事",
            due=(now - timedelta(days=1)).isoformat(),
            labels="est30",
        )
        await self.plugin.vikunja_create_task(
            self.event,
            "今天到期",
            due=(now + timedelta(hours=6)).isoformat(),
            labels="est1h",
        )
        result = await self.plugin.vikunja_plan_day(
            self.event, windows="09:00-12:00", apply_changes=True
        )
        self.assertIn("已写入", result)
        blocks = [
            task
            for task in self.plugin.client.tasks.values()
            if task.get("start_date") and task.get("end_date")
        ]
        self.assertEqual(len(blocks), 2)
        starts = sorted(task["start_date"] for task in blocks)
        self.assertNotEqual(starts[0], starts[1])

    async def test_labels_are_guessed_when_the_model_forgets_them(self):
        result = await self.plugin.vikunja_create_task(self.event, "下班去便利店买牛奶")
        self.assertIn("标签是按标题猜的", result)
        task = list(self.plugin.client.tasks.values())[0]
        self.assertEqual(
            [label["title"] for label in task["labels"]], ["est15", "@外出"]
        )

    async def test_unguessable_title_reports_that_it_has_no_labels(self):
        result = await self.plugin.vikunja_create_task(self.event, "zzz")
        self.assertIn("没有任何标签", result)

    async def test_description_is_stored_as_html_and_appended(self):
        await self.plugin.vikunja_create_task(
            self.event, "写引言", description="目标：重写引言\n- [ ] 列大纲"
        )
        task_id = next(iter(self.plugin.client.tasks))
        stored = self.plugin.client.tasks[task_id]["description"]
        self.assertIn("<p>目标：重写引言</p>", stored)
        self.assertIn('data-checked="false"', stored)

        await self.plugin.vikunja_update_task(
            self.event, task_id, description="- [ ] 找三篇对照文献"
        )
        appended = self.plugin.client.tasks[task_id]["description"]
        self.assertIn("列大纲", appended)
        self.assertIn("找三篇对照文献", appended)

        await self.plugin.vikunja_update_task(
            self.event,
            task_id,
            description="只留这一句",
            description_mode="replace",
        )
        replaced = self.plugin.client.tasks[task_id]["description"]
        self.assertEqual(replaced, "<p>只留这一句</p>")

    async def test_repeat_can_be_changed_and_counted_from_completion(self):
        await self.plugin.vikunja_create_task(
            self.event,
            "浇花",
            due="2026-09-23T09:00",
            repeat="3d",
            repeat_from_completion=True,
        )
        task_id = next(iter(self.plugin.client.tasks))
        task = self.plugin.client.tasks[task_id]
        self.assertEqual(task["repeat_after"], 259200)
        self.assertEqual(task["repeat_mode"], 2)

        result = await self.plugin.vikunja_update_task(
            self.event, task_id, repeat="weekly"
        )
        self.assertIn("重复 每周", result)
        self.assertEqual(self.plugin.client.tasks[task_id]["repeat_after"], 604800)
        self.assertEqual(self.plugin.client.tasks[task_id]["repeat_mode"], 0)

        await self.plugin.vikunja_update_task(self.event, task_id, repeat="none")
        self.assertEqual(self.plugin.client.tasks[task_id]["repeat_after"], 0)

    async def test_finishing_subtasks_rolls_progress_up_to_the_parent(self):
        await self.plugin.vikunja_create_task(
            self.event, "写论文", project="PhD/课题一"
        )
        parent_id = next(iter(self.plugin.client.tasks))
        await self.plugin.vikunja_add_subtask(self.event, parent_id, "列引言大纲")
        await self.plugin.vikunja_add_subtask(self.event, parent_id, "写初稿")
        children = [
            task_id for task_id in self.plugin.client.tasks if task_id != parent_id
        ]
        self.assertEqual(self.plugin.client.tasks[parent_id].get("percent_done", 0), 0)

        result = await self.plugin.vikunja_complete_task(self.event, children[0])
        self.assertIn("进度更新为 50%", result)
        self.assertEqual(self.plugin.client.tasks[parent_id]["percent_done"], 0.5)

        await self.plugin.vikunja_complete_task(self.event, children[1])
        self.assertEqual(self.plugin.client.tasks[parent_id]["percent_done"], 1.0)

    async def test_task_detail_shows_description_relations_and_comments(self):
        await self.plugin.vikunja_create_task(
            self.event,
            "写论文",
            project="PhD/课题一",
            description="目标：投出去\n- [x] 大纲\n- [ ] 初稿",
        )
        parent_id = next(iter(self.plugin.client.tasks))
        await self.plugin.vikunja_add_subtask(self.event, parent_id, "写初稿")
        await self.plugin.vikunja_comment(self.event, parent_id, "今天推进了大纲")
        detail = await self.plugin.vikunja_task_detail(self.event, parent_id)
        self.assertIn("目标：投出去", detail)
        self.assertIn("[x] 大纲", detail)
        self.assertIn("清单 1/2", detail)
        self.assertIn("子任务：", detail)
        self.assertIn("写初稿", detail)
        self.assertIn("今天推进了大纲", detail)

    async def test_progress_reaches_the_prompt_as_in_progress_work(self):
        await self.plugin.vikunja_create_task(
            self.event, "写引言", project="PhD/课题一"
        )
        task_id = next(iter(self.plugin.client.tasks))
        await self.plugin.vikunja_update_task(self.event, task_id, percent_done=40)
        req = types.SimpleNamespace(system_prompt="")
        await self.plugin.on_llm_request(self.event, req)
        self.assertIn("已经动过手但没做完", req.system_prompt)
        self.assertIn("进度=40%", req.system_prompt)
        self.assertIn("现有标签与当前用量", req.system_prompt)

    async def test_comment_and_query_and_group_guard(self):
        await self.plugin.vikunja_create_task(self.event, "买牛奶")
        task_id = next(iter(self.plugin.client.tasks))
        self.assertIn(
            "进展日志",
            await self.plugin.vikunja_comment(self.event, task_id, "顺延一天"),
        )
        self.assertEqual(self.plugin.client.comments[0][1], "顺延一天")
        listing = await self.plugin.vikunja_query_tasks(self.event, scope="unscheduled")
        self.assertIn("买牛奶", listing)
        self.event.get_group_id = lambda: "group"
        self.assertIn(
            "只支持私聊", await self.plugin.vikunja_query_tasks(self.event, scope="all")
        )

    async def test_complete_and_delete(self):
        await self.plugin.vikunja_create_task(self.event, "买牛奶")
        task_id = next(iter(self.plugin.client.tasks))
        self.assertIn(
            "已完成", await self.plugin.vikunja_complete_task(self.event, task_id)
        )
        self.assertTrue(self.plugin.client.tasks[task_id]["done"])
        self.assertIn(
            "已删除", await self.plugin.vikunja_delete_task(self.event, task_id)
        )
        self.assertEqual(self.plugin.client.tasks, {})

    async def test_agenda_digest_reaches_the_prompt(self):
        await self.plugin.vikunja_create_task(
            self.event, "交周报", due=datetime.now(TZ).isoformat()
        )
        req = types.SimpleNamespace(system_prompt="")
        await self.plugin.on_llm_request(self.event, req)
        self.assertIn("今日议程摘要", req.system_prompt)
        self.assertIn("交周报", req.system_prompt)
        self.assertIn("Vikunja 项目树", req.system_prompt)


if __name__ == "__main__":
    unittest.main()
