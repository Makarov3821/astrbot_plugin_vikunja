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
        return dict(self.tasks[int(task_id)])

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
