"""Persistent plugin state backed by AstrBot's per-plugin KV storage."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from typing import Any, Awaitable, Callable

LoadFn = Callable[[], Awaitable[dict[str, Any]]]
SaveFn = Callable[[dict[str, Any]], Awaitable[None]]


class StateStore:
    def __init__(self, load: LoadFn, save: SaveFn):
        self._load = load
        self._save = save
        self._lock = asyncio.Lock()
        self._state: dict[str, Any] = {
            "channels": {},
            "task_reminder_minutes": {},
            "sent": {},
            "local_items": {},
        }

    async def initialize(self) -> None:
        loaded = await self._load()
        if isinstance(loaded, dict):
            for key in self._state:
                if isinstance(loaded.get(key), dict):
                    self._state[key] = loaded[key]

    def channel(self, key: str) -> dict[str, Any] | None:
        value = self._state["channels"].get(key)
        return deepcopy(value) if value else None

    def local_items(self, channel: str) -> list[dict[str, Any]]:
        return [
            deepcopy(x)
            for x in self._state["local_items"].values()
            if x["channel"] == channel
        ]

    async def put_local_item(self, item: dict[str, Any]) -> None:
        async with self._lock:
            updated = deepcopy(self._state)
            updated["local_items"][item["id"]] = deepcopy(item)
            await self._save(updated)
            self._state = updated

    def channels(self) -> dict[str, dict[str, Any]]:
        return deepcopy(self._state["channels"])

    def task_reminder_minutes(self, task_id: int, default: int) -> int:
        return int(self._state["task_reminder_minutes"].get(str(task_id), default))

    def was_sent(self, key: str) -> bool:
        return key in self._state["sent"]

    async def register_channel(self, key: str, channel: dict[str, Any]) -> None:
        async with self._lock:
            current = self._state["channels"].get(key, {})
            channel.setdefault(
                "reminders_enabled", current.get("reminders_enabled", True)
            )
            self._state["channels"][key] = channel
            await self._save(deepcopy(self._state))

    async def set_reminders_enabled(self, key: str, enabled: bool) -> None:
        async with self._lock:
            if key not in self._state["channels"]:
                raise KeyError(key)
            self._state["channels"][key]["reminders_enabled"] = enabled
            await self._save(deepcopy(self._state))

    async def set_task_reminder(self, task_id: int, minutes: int) -> None:
        async with self._lock:
            self._state["task_reminder_minutes"][str(task_id)] = int(minutes)
            await self._save(deepcopy(self._state))

    async def mark_sent(self, key: str, sent_at: str) -> None:
        async with self._lock:
            self._state["sent"][key] = sent_at
            if len(self._state["sent"]) > 2000:
                oldest = sorted(self._state["sent"].items(), key=lambda item: item[1])[
                    :500
                ]
                for old_key, _ in oldest:
                    self._state["sent"].pop(old_key, None)
            await self._save(deepcopy(self._state))
