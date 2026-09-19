"""Load plugin modules and the plugin class without importing AstrBot."""

import ast
import asyncio
import importlib
import logging
import shlex
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

from state_store import StateStore

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "planner_test_plugin"

if PACKAGE not in sys.modules:
    package = types.ModuleType(PACKAGE)
    package.__path__ = [str(ROOT)]
    sys.modules[PACKAGE] = package

local_planner_module = importlib.import_module(f"{PACKAGE}.local_planner")
domain = importlib.import_module(f"{PACKAGE}.todo_domain")
agenda_module = importlib.import_module(f"{PACKAGE}.agenda")
bootstrap_module = importlib.import_module(f"{PACKAGE}.bootstrap")
vikunja_module = importlib.import_module(f"{PACKAGE}.vikunja")
LocalPlanner = local_planner_module.LocalPlanner


class FakeMessageChain:
    """Stand-in for astrbot MessageChain, enough for push assertions."""

    def __init__(self):
        self.text = ""

    def message(self, text):
        self.text += text
        return self


def load_plugin_class():
    """Exec main.py with AstrBot-only imports and decorators removed."""
    tree = ast.parse((ROOT / "main.py").read_text())
    tree.body = [
        node for node in tree.body if not isinstance(node, (ast.Import, ast.ImportFrom))
    ]
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            node.decorator_list = []
    ns: dict = {
        "Star": object,
        "asyncio": asyncio,
        "shlex": shlex,
        "logger": logging.getLogger("test"),
        "StateStore": StateStore,
        "LocalPlanner": LocalPlanner,
        "MessageChain": FakeMessageChain,
        "bootstrap": bootstrap_module,
        "datetime": datetime,
        "timedelta": timedelta,
        "timezone": timezone,
        "Any": object,
    }
    ns.update(vars(domain))
    ns.update(vars(agenda_module))
    ns.update(
        VikunjaClient=vikunja_module.VikunjaClient,
        VikunjaError=vikunja_module.VikunjaError,
        RELATION_KINDS=vikunja_module.RELATION_KINDS,
    )
    exec(
        compile(
            ast.fix_missing_locations(tree),
            str(ROOT / "main.py"),
            "exec",
            flags=__import__("__future__").annotations.compiler_flag,
        ),
        ns,
    )
    return ns["VikunjaPlugin"]
