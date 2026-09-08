"""Persistent local reminders, independent of Vikunja and the LLM."""

import asyncio
import re
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from .todo_domain import parse_datetime, from_vikunja_time


class LocalPlanner:
    def __init__(self, state, tz):
        self.state = state
        self.tz = tz
        self.lock = asyncio.Lock()

    def future(self, value):
        if not re.search(r"\d\s*[:：点]|\d+\s*(分钟|分|小时|天)后", value):
            raise ValueError(
                "请确认具体提醒时间，例如今天18:00；只有日期不能设置定时提醒"
            )
        date = parse_datetime(value, self.tz)
        if date <= datetime.now(timezone.utc):
            raise ValueError("提醒时间必须在未来，请确认具体日期和时间")
        return date.isoformat()

    async def create(
        self,
        channel,
        title,
        when="",
        kind="task",
        deadline="",
        next_step="",
        condition="",
        retry=30,
    ):
        if not title.strip():
            raise ValueError("标题不能为空")
        if kind not in {"task", "reminder", "review", "start"}:
            raise ValueError("类型须为 task/reminder/review/start")
        if kind != "task" and not when:
            raise ValueError("请先询问用户大概几点方便提醒；例如几点下班。未创建提醒")
        if not 1 <= retry <= 1440:
            raise ValueError("重复提醒间隔须为 1 到 1440 分钟")
        item = dict(
            id="L" + uuid4().hex[:10],
            channel=channel,
            title=title.strip(),
            kind=kind,
            next_at=self.future(when) if when else None,
            deadline=parse_datetime(deadline, self.tz).isoformat()
            if deadline
            else None,
            next_step=next_step,
            condition=condition,
            retry=retry,
            status="active",
            sent_count=0,
        )
        async with self.lock:
            await self.state.put_local_item(item)
        return item

    async def change(self, channel, item_id, action, when=""):
        async with self.lock:
            matches = [x for x in self.state.local_items(channel) if x["id"] == item_id]
            if not matches:
                raise ValueError(
                    "找不到当前会话中的本地事项，请查询列表确认 L 开头的 ID"
                )
            item = matches[0]
            if action in {"done", "cancel", "pause"}:
                item["status"] = action
                item["next_at"] = None
            elif action == "snooze":
                item["next_at"] = self.future(when)
                item["status"] = "active"
            elif action == "ready":
                item["condition"] = ""
                item["next_at"] = (
                    self.future(when)
                    if when
                    else datetime.now(timezone.utc).isoformat()
                )
                item["status"] = "active"
            else:
                raise ValueError("操作须为 done/cancel/pause/snooze/ready")
            await self.state.put_local_item(item)
            return item

    async def dispatch(self, send, now=None):
        now = now or datetime.now(timezone.utc)
        async with self.lock:
            for channel, settings in self.state.channels().items():
                if not settings.get("reminders_enabled", True):
                    continue
                for item in self.state.local_items(channel):
                    due = from_vikunja_time(item["next_at"])
                    if item["status"] != "active" or not due or due > now:
                        continue
                    kind = item["kind"]
                    label = {"review": "值得回顾一下", "start": "可以开始推进了"}.get(
                        kind, "记得处理这件事"
                    )
                    if item["condition"]:
                        label = "确认一下前置条件是否满足"
                    text = f"⏰ {label}：{item['title']}（{item['id']}）"
                    if item["next_step"]:
                        text += f"\n下一步：{item['next_step']}"
                    if item["condition"]:
                        text += f"\n等待：{item['condition']}"
                    if item["deadline"]:
                        text += f"\n截止：{item['deadline']}"
                    text += f"\n完成请回复“{item['id']} 完成”；也可以说稍后提醒或暂停。"
                    # Persist the next attempt first: failures never flood every poll.
                    item["next_at"] = (
                        now + timedelta(minutes=item["retry"])
                    ).isoformat()
                    await self.state.put_local_item(item)
                    try:
                        success = await send(channel, text)
                    except Exception:
                        continue
                    if success is False:
                        continue
                    item["sent_count"] += 1
                    if kind != "reminder":
                        item["next_at"] = None  # Gentle reviews do not nag.
                    await self.state.put_local_item(item)
