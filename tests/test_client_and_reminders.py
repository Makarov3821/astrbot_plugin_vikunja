import types
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

from state_store import StateStore

from plugin_loader import agenda_module, load_plugin_class, vikunja_module

VikunjaClient = vikunja_module.VikunjaClient
VikunjaError = vikunja_module.VikunjaError
TZ = ZoneInfo("Asia/Shanghai")


def stub_client(responses):
    """A client whose HTTP layer is replaced by a recorded response map."""
    client = VikunjaClient(
        "https://vkj.example.com", "token", filter_timezone="Asia/Shanghai"
    )
    client.calls = []

    async def _request(method, path, *, params=None, json=None):
        client.calls.append((method, path, params, json))
        key = (method, path)
        payload = responses.get(key, [])
        if callable(payload):
            payload = payload(json)
        return payload, {"x-pagination-total-pages": "1"}

    client._request = _request
    return client


class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_list_projects_drops_archived_and_saved_filters(self):
        client = stub_client(
            {
                ("GET", "projects"): [
                    {"id": 1, "title": "Inbox"},
                    {"id": 2, "title": "旧项目", "is_archived": True},
                    {"id": -2, "title": "🧭 总看板"},
                ]
            }
        )
        projects = await client.list_projects()
        self.assertEqual([p["id"] for p in projects], [1])
        filters = await client.list_saved_filters()
        self.assertEqual([f["id"] for f in filters], [-2])

    async def test_list_tasks_sends_server_side_filter_and_timezone(self):
        client = stub_client({("GET", "tasks"): []})
        await client.list_tasks("due_date < now/d+1d", project_id=7)
        _, _, params, _ = client.calls[0]
        flat = dict(params)
        self.assertEqual(
            flat["filter"],
            "project_id = 7 && done = false && (due_date < now/d+1d)",
        )
        self.assertEqual(flat["filter_timezone"], "Asia/Shanghai")
        self.assertEqual(flat["per_page"], "50")
        self.assertIn(("sort_by", "priority"), params)
        self.assertIn(("order_by", "desc"), params)

    async def test_update_task_merges_writable_fields_and_clears_dates(self):
        current = {
            "id": 5,
            "title": "写引言",
            "priority": 3,
            "due_date": "2026-09-20T10:00:00Z",
            "start_date": "2026-09-20T08:00:00Z",
            "description": "",
            "done": False,
            "labels": [{"id": 1, "title": "est1h"}],
            "identifier": "#5",
            "created_by": {"id": 1},
        }
        client = stub_client(
            {("GET", "tasks/5"): current, ("POST", "tasks/5"): lambda body: body}
        )
        await client.update_task(5, {"due_date": None, "priority": 5})
        _, _, _, body = client.calls[-1]
        self.assertEqual(body["due_date"], vikunja_module.ZERO_DATE)
        self.assertEqual(body["priority"], 5)
        # untouched writable field survives, read-only fields are not echoed back
        self.assertEqual(body["start_date"], "2026-09-20T08:00:00Z")
        self.assertNotIn("labels", body)
        self.assertNotIn("identifier", body)
        self.assertNotIn("created_by", body)

    async def test_relation_kind_is_validated(self):
        client = stub_client({})
        with self.assertRaisesRegex(VikunjaError, "不支持的任务关系"):
            await client.create_relation(1, 2, "nonsense")
        await client.create_relation(1, 2, "parenttask")
        method, path, _, body = client.calls[-1]
        self.assertEqual((method, path), ("PUT", "tasks/1/relations"))
        self.assertEqual(body["relation_kind"], "parenttask")


class ReminderTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.saved = {}

        async def load():
            return self.saved

        async def save(value):
            self.saved = value

        self.state = StateStore(load, save)
        await self.state.initialize()
        await self.state.register_channel("qq", {"umo": "qq"})
        self.plugin = load_plugin_class().__new__(load_plugin_class())
        self.plugin.state = self.state
        self.plugin.tz = TZ
        self.plugin.tz_name = "Asia/Shanghai"
        self.plugin.config = {"reminder_minutes": 30}
        self.plugin._ready = True
        self.plugin._projects_cache = None
        self.plugin._labels_cache = None
        self.sent = []

        async def send_message(umo, chain):
            self.sent.append((umo, chain))
            return True

        self.plugin.context = types.SimpleNamespace(send_message=send_message)

    def test_absolute_and_relative_reminders_are_both_resolved(self):
        due = datetime(2026, 9, 20, 18, 0, tzinfo=timezone.utc)
        task = {
            "id": 1,
            "due_date": due.isoformat().replace("+00:00", "Z"),
            "reminders": [
                {"reminder": "2026-09-20T09:00:00Z"},
                {
                    "relative_to": "due_date",
                    "relative_period": -3600,
                    "reminder": "0001-01-01T00:00:00Z",
                },
            ],
        }
        moments = self.plugin._reminder_moments(task)
        self.assertEqual(
            sorted(moments),
            [
                datetime(2026, 9, 20, 9, 0, tzinfo=timezone.utc),
                due - timedelta(hours=1),
            ],
        )

    def test_default_reminder_only_when_task_has_none(self):
        due = datetime(2026, 9, 20, 18, 0, tzinfo=timezone.utc)
        task = {"id": 2, "due_date": due.isoformat().replace("+00:00", "Z")}
        self.assertEqual(
            self.plugin._reminder_moments(task), [due - timedelta(minutes=30)]
        )
        self.assertEqual(self.plugin._reminder_moments({"id": 3}), [])

    async def test_dispatch_sends_once_per_moment_and_skips_stale(self):
        now = datetime.now(timezone.utc)
        fresh = {
            "id": 10,
            "title": "交周报",
            "project_id": 1,
            "priority": 3,
            "due_date": (now + timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
        }
        stale = {
            "id": 11,
            "title": "上周的事",
            "project_id": 1,
            "due_date": (now - timedelta(days=5)).isoformat().replace("+00:00", "Z"),
        }
        self.plugin.client = types.SimpleNamespace(
            configured=True,
            list_tasks=AsyncMock(return_value=[fresh, stale]),
            list_projects=AsyncMock(return_value=[{"id": 1, "title": "Inbox"}]),
        )
        self.plugin._projects = AsyncMock(return_value=[{"id": 1, "title": "Inbox"}])
        await self.plugin._dispatch_reminders()
        self.assertEqual(len(self.sent), 1)
        await self.plugin._dispatch_reminders()
        self.assertEqual(len(self.sent), 1)

    async def test_disabled_channel_receives_nothing(self):
        await self.state.set_reminders_enabled("qq", False)
        self.plugin.client = types.SimpleNamespace(
            configured=True, list_tasks=AsyncMock(return_value=[])
        )
        await self.plugin._dispatch_reminders()
        self.assertEqual(self.sent, [])


class BriefingTests(unittest.IsolatedAsyncioTestCase):
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
        self.plugin.config = {
            "briefing_enabled": True,
            "briefing_time": "07:30",
            "briefing_use_llm": False,
        }
        self.plugin._ready = True
        self.plugin.client = types.SimpleNamespace(
            configured=True, web_url="https://vkj.example.com"
        )
        self.plugin._agenda = AsyncMock(
            return_value=(agenda_module.Agenda(), {}, "", [])
        )
        self.sent = []

        async def send_message(umo, chain):
            self.sent.append((umo, chain.text))
            return True

        self.plugin.context = types.SimpleNamespace(send_message=send_message)

    def _set_now(self, hour, minute):
        moment = datetime.now(TZ).replace(
            hour=hour, minute=minute, second=0, microsecond=0
        )
        self.plugin._now = lambda: moment
        return moment

    async def test_sends_once_per_day_inside_the_window(self):
        self._set_now(7, 35)
        await self.plugin._maybe_send_briefing()
        self.assertEqual(len(self.sent), 1)
        self.assertIn("早报", self.sent[0][1])
        await self.plugin._maybe_send_briefing()
        self.assertEqual(len(self.sent), 1)

    async def test_does_not_fire_before_time_or_after_two_hours(self):
        self._set_now(7, 0)
        await self.plugin._maybe_send_briefing()
        self._set_now(10, 0)
        await self.plugin._maybe_send_briefing()
        self.assertEqual(self.sent, [])

    async def test_disabled_briefing_is_silent(self):
        self.plugin.config["briefing_enabled"] = False
        self._set_now(7, 35)
        await self.plugin._maybe_send_briefing()
        self.assertEqual(self.sent, [])


if __name__ == "__main__":
    unittest.main()
