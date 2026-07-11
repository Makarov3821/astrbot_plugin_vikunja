"""AstrBot plugin entry point for a single-user Vikunja secretary."""

from __future__ import annotations

import asyncio
import shlex
from datetime import datetime, timedelta, timezone
from typing import Any

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star

from .state_store import StateStore
from .todo_domain import (
    AddSpec,
    build_project_paths,
    format_project_tree,
    format_task_list,
    from_vikunja_time,
    get_timezone,
    parse_add_arguments,
    parse_datetime,
    parse_repeat,
    platform_sender_is_allowed,
    resolve_project,
    secretary_clarification_reason,
    select_tasks,
)
from .vikunja import VikunjaClient, VikunjaError


HELP_TEXT = """Vikunja 私人待办秘书（仅支持私聊）
/todo projects                         查看完整项目树
/todo add <标题> [--project 项目路径] [--due 时间] [--priority 0-5]
               [--repeat 规则] [--remind 30m]
/todo done <任务ID>                    完成任务
/todo today                            所有项目的今日及逾期待办
/todo list [all|week|overdue] [--project 项目路径]
/todo remind <on|off>                  开关当前 QQ/微信入口的提醒

例：
/todo add 买牛奶 --due "明天 18:00"
/todo add 修改论文引言 --project "fudan-work/SMX/paper" --due "周日 20:00" --priority 4
/todo add 每日复盘 --project personal-project --due "今天 22:00" --repeat daily"""


SECRETARY_PROMPT = """
你可以使用 Vikunja 工具充当用户的私人待办秘书。遵守以下规则：
1. 这是单用户系统，QQ 私聊和微信私聊共享同一个 Vikunja 工作空间。
2. 用户要求提醒时，必须确认明确的截止日期和时间；缺少时先询问，不得调用创建工具。
3. 对论文、项目推进、学习计划、习惯等明显长期事项，若缺少截止时间、持续时长或重复频率，先询问确认。
4. 日常琐事可放入 Inbox；工作或项目事项必须选到合适的项目层级。项目不明确时先调用项目树工具，仍有歧义就询问用户。
5. 修改或完成事项前，若目标任务不唯一，先查询并请用户确认任务 ID。
6. 只有工具明确返回成功后才能告诉用户已创建或已修改；工具要求补充信息时，继续询问用户。
7. 用简洁、自然的中文交流，不要求用户记忆斜杠命令。
"""


class PrivateOnlyError(ValueError):
    pass


class VikunjaPlugin(Star):
    SUPPORTED_PLATFORMS = {"qq_official", "qq_official_webhook", "weixin_oc"}

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.tz = get_timezone(str(config.get("timezone", "Asia/Shanghai")))
        self.client = VikunjaClient(
            str(config.get("vikunja_url", "")),
            str(config.get("api_token", "")),
            int(config.get("request_timeout_seconds", 15)),
        )
        self.state = StateStore(self._load_state, self._save_state)
        self._ready = False
        self._ready_lock = asyncio.Lock()
        self._reminder_task: asyncio.Task[None] | None = None

    async def _load_state(self) -> dict[str, Any]:
        return await self.get_kv_data("state_v2", {})

    async def _save_state(self, state: dict[str, Any]) -> None:
        await self.put_kv_data("state_v2", state)

    async def _ensure_ready(self) -> None:
        if self._ready:
            return
        async with self._ready_lock:
            if not self._ready:
                await self.state.initialize()
                self._ready = True

    def _sender_allowed(self, event: AstrMessageEvent) -> bool:
        return platform_sender_is_allowed(
            event.get_platform_name(),
            str(event.get_sender_id()),
            self.config.get("allowed_qq_sender_ids", []),
        )

    def _assert_private(self, event: AstrMessageEvent) -> None:
        if event.get_group_id():
            raise PrivateOnlyError("Vikunja 私人秘书只支持私聊，不会在群聊中读取或修改任务")
        if event.get_platform_name() not in self.SUPPORTED_PLATFORMS:
            raise PrivateOnlyError("Vikunja 私人秘书只支持 QQ 官方机器人和 weixin_oc")
        if not self._sender_allowed(event):
            raise PrivateOnlyError("当前 QQ 发送者不在 Vikunja 私人秘书白名单中")

    async def _register_channel(self, event: AstrMessageEvent) -> str:
        self._assert_private(event)
        await self._ensure_ready()
        key = str(event.unified_msg_origin)
        if not self.state.channel(key):
            await self.state.register_channel(
                key,
                {
                    "umo": key,
                    "sender_id": str(event.get_sender_id()),
                    "reminders_enabled": True,
                },
            )
        return key

    @staticmethod
    def _command_tail(event: AstrMessageEvent) -> str:
        parts = event.message_str.strip().split(maxsplit=2)
        return parts[2].strip() if len(parts) == 3 else ""

    async def _resolve_project(
        self, selector: str = ""
    ) -> tuple[dict[str, Any], str, list[dict[str, Any]]]:
        projects = await self.client.list_projects()
        target = selector.strip() or str(self.config.get("default_project", "Inbox")).strip()
        project, path = resolve_project(projects, target)
        return project, path, projects

    async def _create(
        self, event: AstrMessageEvent, spec: AddSpec
    ) -> tuple[dict[str, Any], str]:
        await self._register_channel(event)
        project, path, _ = await self._resolve_project(spec.project_selector)
        task = await self.client.create_task(int(project["id"]), spec.payload())
        if spec.reminder_minutes is not None:
            await self.state.set_task_reminder(int(task["id"]), spec.reminder_minutes)
        return task, path

    async def _list(
        self, event: AstrMessageEvent, scope: str, project_selector: str = ""
    ) -> str:
        await self._register_channel(event)
        projects = await self.client.list_projects()
        project_id: int | None = None
        suffix = ""
        if project_selector:
            project, path = resolve_project(projects, project_selector)
            project_id = int(project["id"])
            suffix = f" · {path}"
        tasks = await self.client.list_tasks(project_id)
        selected = select_tasks(tasks, scope, self.tz)
        titles = {
            "today": "📋 今日及逾期待办（按优先级）",
            "week": "📅 未来 7 天待办（按优先级）",
            "overdue": "⚠️ 已逾期待办（按优先级）",
            "all": "🗂️ 全部待办（按优先级）",
        }
        return format_task_list(
            selected,
            titles[scope] + suffix,
            self.tz,
            int(self.config.get("max_list_items", 20)),
            build_project_paths(projects),
        )

    @staticmethod
    def _parse_list_tail(text: str) -> tuple[str, str]:
        tokens = shlex.split(text)
        aliases = {
            "all": "all",
            "全部": "all",
            "week": "week",
            "本周": "week",
            "overdue": "overdue",
            "逾期": "overdue",
            "today": "today",
            "今天": "today",
        }
        scope = "all"
        project = ""
        index = 0
        if tokens and tokens[0].lower() in aliases:
            scope = aliases[tokens[0].lower()]
            index = 1
        while index < len(tokens):
            if tokens[index] not in {"--project", "-P"} or index + 1 >= len(tokens):
                raise ValueError("用法：/todo list [all|week|overdue|today] [--project 项目路径]")
            project = tokens[index + 1]
            index += 2
        return scope, project

    @filter.on_astrbot_loaded()
    async def on_astrbot_loaded(self) -> None:
        await self._ensure_ready()
        if not self.client.base_url or not self.client.token:
            logger.warning("Vikunja 插件未配置 URL 或 API Token，提醒调度未启动")
            return
        if self._reminder_task is None or self._reminder_task.done():
            self._reminder_task = asyncio.create_task(
                self._reminder_loop(), name="astrbot-vikunja-reminders"
            )

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest) -> None:
        if event.get_group_id() or not self._sender_allowed(event):
            return
        req.system_prompt = (req.system_prompt or "") + SECRETARY_PROMPT

    async def _reminder_loop(self) -> None:
        interval = max(30, int(self.config.get("poll_interval_seconds", 60)))
        while True:
            try:
                await self._dispatch_reminders()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error(f"Vikunja 提醒轮询失败：{exc}")
            await asyncio.sleep(interval)

    async def _dispatch_reminders(self) -> None:
        channels = {
            key: channel
            for key, channel in self.state.channels().items()
            if channel.get("reminders_enabled", True)
        }
        if not channels:
            return
        tasks = await self.client.list_tasks()
        projects = await self.client.list_projects()
        paths = build_project_paths(projects)
        now = datetime.now(timezone.utc)
        default_minutes = max(0, int(self.config.get("reminder_minutes", 30)))
        for task in tasks:
            due = from_vikunja_time(task.get("due_date"))
            if not due:
                continue
            minutes = self.state.task_reminder_minutes(int(task["id"]), default_minutes)
            if due > now + timedelta(minutes=minutes):
                continue
            due_local = due.astimezone(self.tz).strftime("%m-%d %H:%M")
            overdue = due < now
            project_path = paths.get(int(task.get("project_id") or 0), "未知项目")
            text = (
                f"{'⚠️ 已逾期' if overdue else '⏰ 即将到期'}："
                f"[P{int(task.get('priority') or 0)}] #{task['id']} {task.get('title', '')}\n"
                f"项目：{project_path}\n截止：{due_local}"
            )
            for channel_key, channel in channels.items():
                sent_key = f"{channel_key}|{task['id']}|{task.get('due_date', '')}"
                if self.state.was_sent(sent_key):
                    continue
                try:
                    sent = await self.context.send_message(
                        str(channel["umo"]), MessageChain().message(text)
                    )
                    if sent is not False:
                        await self.state.mark_sent(sent_key, now.isoformat())
                    else:
                        logger.warning(f"找不到提醒会话：{channel['umo']}")
                except Exception as exc:
                    logger.error(f"向会话 {channel['umo']} 推送 Vikunja 提醒失败：{exc}")

    @filter.command_group("todo", alias={"待办"})
    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    def todo(self):
        """管理 Vikunja 待办，仅支持私聊。"""
        pass

    @todo.command("help", alias={"帮助"})
    async def todo_help(self, event: AstrMessageEvent):
        try:
            await self._register_channel(event)
            yield event.plain_result(HELP_TEXT)
        except PrivateOnlyError as exc:
            yield event.plain_result(str(exc))

    @todo.command("projects", alias={"项目", "项目树"})
    async def todo_projects(self, event: AstrMessageEvent):
        try:
            await self._register_channel(event)
            yield event.plain_result(format_project_tree(await self.client.list_projects()))
        except (PrivateOnlyError, VikunjaError) as exc:
            yield event.plain_result(str(exc))

    @todo.command("add", alias={"添加", "新建"})
    async def todo_add(self, event: AstrMessageEvent):
        try:
            spec = parse_add_arguments(self._command_tail(event), self.tz)
            task, project_path = await self._create(event, spec)
            due = from_vikunja_time(task.get("due_date"))
            due_text = due.astimezone(self.tz).strftime("%Y-%m-%d %H:%M") if due else "未设置"
            repeat_text = "，重复任务" if spec.repeat_after or spec.repeat_mode else ""
            yield event.plain_result(
                f"✅ 已创建 #{task['id']} {task['title']}\n"
                f"项目：{project_path}，优先级：P{task.get('priority', 0)}，截止：{due_text}{repeat_text}"
            )
        except (ValueError, VikunjaError) as exc:
            yield event.plain_result(f"创建失败：{exc}")

    @todo.command("done", alias={"完成", "勾选"})
    async def todo_done(self, event: AstrMessageEvent):
        try:
            await self._register_channel(event)
            task_id = int(self._command_tail(event).lstrip("#"))
            task = await self.client.complete_task(task_id)
            repeated = not task.get("done", True)
            suffix = "；重复规则已推进到下一周期" if repeated else ""
            yield event.plain_result(f"✅ 已完成 #{task_id} {task.get('title', '')}{suffix}")
        except ValueError:
            yield event.plain_result("用法：/todo done <任务ID>")
        except (PrivateOnlyError, VikunjaError) as exc:
            yield event.plain_result(f"完成失败：{exc}")

    @todo.command("today", alias={"今天", "今日"})
    async def todo_today(self, event: AstrMessageEvent):
        try:
            yield event.plain_result(await self._list(event, "today"))
        except (ValueError, VikunjaError) as exc:
            yield event.plain_result(f"查询失败：{exc}")

    @todo.command("list", alias={"列表", "查询"})
    async def todo_list(self, event: AstrMessageEvent):
        try:
            scope, project = self._parse_list_tail(self._command_tail(event))
            yield event.plain_result(await self._list(event, scope, project))
        except (ValueError, VikunjaError) as exc:
            yield event.plain_result(f"查询失败：{exc}")

    @todo.command("remind", alias={"提醒"})
    async def todo_remind(self, event: AstrMessageEvent):
        value = self._command_tail(event).lower()
        enabled = value in {"on", "开", "开启"}
        if value not in {"on", "off", "开", "关", "开启", "关闭"}:
            yield event.plain_result("用法：/todo remind <on|off>")
            return
        try:
            key = await self._register_channel(event)
            await self.state.set_reminders_enabled(key, enabled)
            yield event.plain_result(f"✅ 当前私聊入口的临期提醒已{'开启' if enabled else '关闭'}")
        except PrivateOnlyError as exc:
            yield event.plain_result(str(exc))

    @filter.llm_tool(name="vikunja_list_projects")
    async def vikunja_list_projects(self, event: AstrMessageEvent):
        """查询完整的 Vikunja 项目层级；创建工作或项目任务前应先用它确认位置。

        Args:
        """
        try:
            await self._register_channel(event)
            yield event.plain_result(format_project_tree(await self.client.list_projects()))
        except (PrivateOnlyError, VikunjaError) as exc:
            yield event.plain_result(str(exc))

    @filter.llm_tool(name="vikunja_create_task")
    async def vikunja_create_task(
        self,
        event: AstrMessageEvent,
        title: str,
        project: str = "",
        due: str = "",
        priority: int = 0,
        repeat: str = "",
        is_reminder: bool = False,
    ):
        """确认信息充分后创建 Vikunja 任务。用户说“提醒”时 due 必填；工作事项 project 必填。

        Args:
            title(string): 简洁的任务标题
            project(string): 项目完整路径、唯一名称或 ID；日常琐事可为空并进入默认 Inbox
            due(string): 已经和用户确认的截止时间，如“明天18点”；无截止时间可为空
            priority(number): 优先级 0 到 5
            repeat(string): 已确认的重复规则 daily、weekly、monthly 或为空
            is_reminder(boolean): 用户是否明确要求“提醒我”；若为 true，due 不可为空
        """
        try:
            reason = secretary_clarification_reason(title, due, repeat, project, is_reminder)
            if reason:
                yield event.plain_result(f"需要先向用户确认：{reason}。尚未创建任务。")
                return
            repeat_after, repeat_mode = parse_repeat(repeat)
            due_at = parse_datetime(due, self.tz) if due else None
            if (repeat_after or repeat_mode) and not due_at:
                raise ValueError("重复任务必须先确认首次截止时间")
            task, path = await self._create(
                event,
                AddSpec(
                    title=title,
                    project_selector=project,
                    due=due_at,
                    priority=max(0, min(5, int(priority))),
                    repeat_after=repeat_after,
                    repeat_mode=repeat_mode,
                ),
            )
            yield event.plain_result(f"已创建任务 #{task['id']} {task['title']}，项目：{path}")
        except (ValueError, VikunjaError) as exc:
            yield event.plain_result(f"创建失败：{exc}")

    @filter.llm_tool(name="vikunja_complete_task")
    async def vikunja_complete_task(self, event: AstrMessageEvent, task_id: int):
        """完成一个已经由用户确认 ID 的 Vikunja 任务。

        Args:
            task_id(number): 用户已确认的 Vikunja 任务 ID
        """
        try:
            await self._register_channel(event)
            task = await self.client.complete_task(int(task_id))
            yield event.plain_result(f"已完成 #{task_id} {task.get('title', '')}")
        except (PrivateOnlyError, VikunjaError) as exc:
            yield event.plain_result(f"完成失败：{exc}")

    @filter.llm_tool(name="vikunja_list_tasks")
    async def vikunja_list_tasks(
        self, event: AstrMessageEvent, scope: str = "today", project: str = ""
    ):
        """查询所有项目或指定项目的未完成任务，并按优先级排序。

        Args:
            scope(string): 查询范围，today、week、overdue 或 all
            project(string): 可选的项目完整路径、唯一名称或 ID
        """
        try:
            normalized = scope.lower() if scope.lower() in {"today", "week", "overdue", "all"} else "today"
            yield event.plain_result(await self._list(event, normalized, project))
        except (ValueError, VikunjaError) as exc:
            yield event.plain_result(f"查询失败：{exc}")

    async def terminate(self) -> None:
        if self._reminder_task and not self._reminder_task.done():
            self._reminder_task.cancel()
            try:
                await self._reminder_task
            except asyncio.CancelledError:
                pass
        await self.client.close()
