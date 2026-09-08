import importlib.util
import ast
import asyncio
import sys
import types
import unittest
from pathlib import Path
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

from state_store import StateStore

# Load the plugin package without importing AstrBot's entry point.
ROOT = Path(__file__).resolve().parents[1]
package = types.ModuleType("planner_test_plugin")
package.__path__ = [str(ROOT)]
sys.modules[package.__name__] = package
spec = importlib.util.spec_from_file_location(
    "planner_test_plugin.local_planner", ROOT / "local_planner.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
LocalPlanner = module.LocalPlanner


class PlannerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.saved = {}

        async def load():
            return self.saved

        async def save(value):
            self.saved = value

        self.state = StateStore(load, save)
        await self.state.initialize()
        await self.state.register_channel("qq", {"umo": "qq"})
        self.planner = LocalPlanner(self.state, ZoneInfo("Asia/Shanghai"))
        self.send = AsyncMock(return_value=True)
        self.when = (datetime.now(timezone.utc) + timedelta(hours=2)).replace(
            second=0, microsecond=0
        )

    async def create(self, kind="reminder"):
        return await self.planner.create("qq", "买牛奶", self.when.isoformat(), kind)

    async def test_retry_then_complete_survives_restart(self):
        item = await self.create()
        await self.planner.dispatch(self.send, self.when)
        await self.planner.dispatch(self.send, self.when + timedelta(minutes=29))
        self.assertEqual(self.send.await_count, 1)
        await self.state.initialize()
        await self.planner.dispatch(self.send, self.when + timedelta(minutes=30))
        self.assertEqual(self.send.await_count, 2)
        await self.planner.change("qq", item["id"], "done")
        await self.planner.dispatch(self.send, self.when + timedelta(days=1))
        self.assertEqual(self.send.await_count, 2)

    async def test_review_only_once(self):
        await self.create("review")
        await self.planner.dispatch(self.send, self.when)
        await self.planner.dispatch(self.send, self.when + timedelta(days=3))
        self.assertEqual(self.send.await_count, 1)

    async def test_missing_time_and_date_only_rejected(self):
        for when in ("", "明天"):
            with self.assertRaises(ValueError):
                await self.planner.create("qq", "买牛奶", when, "reminder")
        self.assertEqual(self.state.local_items("qq"), [])

    async def test_no_deadline_task(self):
        item = await self.planner.create("qq", "思考论文结构")
        self.assertIsNone(item["deadline"])
        self.assertIsNone(item["next_at"])

    async def test_pause_snooze_and_isolation(self):
        item = await self.create()
        with self.assertRaises(ValueError):
            await self.planner.change("other", item["id"], "done")
        await self.planner.change("qq", item["id"], "pause")
        await self.planner.dispatch(self.send, self.when)
        self.send.assert_not_awaited()
        later = self.when + timedelta(hours=1)
        await self.planner.change("qq", item["id"], "snooze", later.isoformat())
        await self.planner.dispatch(self.send, self.when)
        self.send.assert_not_awaited()
        await self.planner.dispatch(self.send, later)
        self.send.assert_awaited_once()

    async def test_delivery_failure_is_retried_not_completed(self):
        await self.create("review")
        self.send.return_value = False
        await self.planner.dispatch(self.send, self.when)
        self.assertEqual(self.state.local_items("qq")[0]["sent_count"], 0)
        await self.planner.dispatch(self.send, self.when + timedelta(minutes=1))
        self.assertEqual(self.send.await_count, 1)
        self.send.return_value = True
        await self.planner.dispatch(self.send, self.when + timedelta(minutes=30))
        self.assertIsNone(self.state.local_items("qq")[0]["next_at"])

    async def test_disabled_channel(self):
        await self.create()
        await self.state.set_reminders_enabled("qq", False)
        await self.planner.dispatch(self.send, self.when)
        self.send.assert_not_awaited()

    async def test_plugin_tools_and_confirmation_context(self):
        # Exercise actual plugin methods with only AstrBot decorators/imports removed.
        # This checks plugin integration, not the external AstrBot executor.
        tree = ast.parse((ROOT / "main.py").read_text())
        tree.body = [
            n for n in tree.body if not isinstance(n, (ast.Import, ast.ImportFrom))
        ]
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                node.decorator_list = []
        ns = dict(module.__dict__)
        ns.update(Star=object, asyncio=asyncio, StateStore=StateStore)
        from planner_test_plugin import todo_domain

        ns.update(vars(todo_domain))
        exec(
            compile(
                ast.fix_missing_locations(tree),
                str(ROOT / "main.py"),
                "exec",
                flags=__import__("__future__").annotations.compiler_flag,
            ),
            ns,
        )
        plugin = ns["VikunjaPlugin"].__new__(ns["VikunjaPlugin"])
        plugin.state, plugin.planner, plugin.tz = (
            self.state,
            self.planner,
            self.planner.tz,
        )
        plugin.config, plugin._ready = {}, True
        event = types.SimpleNamespace(
            unified_msg_origin="qq",
            get_group_id=lambda: "",
            get_platform_name=lambda: "qq_official",
            get_sender_id=lambda: "me",
        )
        result = await plugin.planner_create(
            event, "买牛奶", self.when.isoformat(), "reminder"
        )
        self.assertIn("已记录", result)
        await self.planner.dispatch(self.send, self.when)
        req = types.SimpleNamespace(system_prompt="")
        await plugin.on_llm_request(event, req)
        self.assertIn("已提醒次数=1", req.system_prompt)
        item = self.state.local_items("qq")[0]
        result = await plugin.planner_change(event, item["id"], "done")
        self.assertIn("done", result)
        await self.planner.dispatch(self.send, self.when + timedelta(days=1))
        self.assertEqual(self.send.await_count, 1)
        event.get_group_id = lambda: "group"
        self.assertIn("只支持私聊", await plugin.planner_list(event))
