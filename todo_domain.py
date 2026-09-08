"""Parsing and presentation logic independent from AstrBot."""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


LONG_TERM_WORDS = {
    "论文",
    "paper",
    "项目",
    "计划",
    "长期",
    "持续",
    "习惯",
    "学习",
    "推进",
    "研究",
}
WORK_WORDS = LONG_TERM_WORDS | {"工作", "fudan", "smx", "实验", "会议", "周报"}


def get_timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"未知时区：{name}") from exc


def parse_datetime(value: str, tz: ZoneInfo, now: datetime | None = None) -> datetime:
    """Parse common Chinese/ISO due-time expressions and return an aware datetime."""
    value = value.strip().replace("：", ":")
    now = (now or datetime.now(tz)).astimezone(tz)

    relative = re.fullmatch(r"(\d+)\s*(分钟|分|小时|天)后", value)
    if relative:
        amount = int(relative.group(1))
        unit = relative.group(2)
        delta = timedelta(
            minutes=amount if unit in {"分钟", "分"} else 0,
            hours=amount if unit == "小时" else 0,
            days=amount if unit == "天" else 0,
        )
        return now + delta

    day_match = re.fullmatch(
        r"(今天|明天|后天)(?:\s*(\d{1,2})(?::|点)(\d{1,2})?(?:分)?)?", value
    )
    if day_match:
        offset = {"今天": 0, "明天": 1, "后天": 2}[day_match.group(1)]
        hour = int(day_match.group(2) or 23)
        minute = int(day_match.group(3) or (59 if day_match.group(2) is None else 0))
        return datetime.combine(
            now.date() + timedelta(days=offset), time(hour, minute), tz
        )

    weekday_match = re.fullmatch(
        r"(本周|下周|周|星期)([一二三四五六日天])"
        r"(?:\s*(\d{1,2})(?::|点)(\d{1,2})?(?:分)?)?",
        value,
    )
    if weekday_match:
        target_weekday = "一二三四五六日天".index(weekday_match.group(2))
        target_weekday = min(target_weekday, 6)
        days = (target_weekday - now.weekday()) % 7
        if weekday_match.group(1) in {"下周", "本周"}:
            days = (
                target_weekday
                - now.weekday()
                + (7 if weekday_match.group(1) == "下周" else 0)
            )
        hour = int(weekday_match.group(3) or 23)
        minute = int(
            weekday_match.group(4) or (59 if weekday_match.group(3) is None else 0)
        )
        result = datetime.combine(
            now.date() + timedelta(days=days), time(hour, minute), tz
        )
        if weekday_match.group(1) != "本周" and result <= now:
            result += timedelta(days=7)
        return result

    formats = (
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%m-%d %H:%M",
        "%m-%d",
        "%H:%M",
    )
    for fmt in formats:
        try:
            parsed = datetime.strptime(value, fmt)
        except ValueError:
            continue
        year = parsed.year if "%Y" in fmt else now.year
        month = parsed.month if "%m" in fmt else now.month
        day = parsed.day if "%d" in fmt else now.day
        hour = parsed.hour if "%H" in fmt else 23
        minute = parsed.minute if "%M" in fmt else 59
        result = datetime(year, month, day, hour, minute, tzinfo=tz)
        if "%Y" not in fmt and "%m" in fmt and result < now - timedelta(days=1):
            result = result.replace(year=year + 1)
        return result
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return (
            parsed.replace(tzinfo=tz)
            if parsed.tzinfo is None
            else parsed.astimezone(tz)
        )
    except ValueError as exc:
        raise ValueError(
            "无法识别时间，可用示例：明天9点、2小时后、2026-07-12 18:00"
        ) from exc


def to_vikunja_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def from_vikunja_time(value: str | None) -> datetime | None:
    if not value or value.startswith("0001-"):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def parse_repeat(value: str | None) -> tuple[int, int]:
    if not value or value.lower() in {"none", "off", "不重复", "无"}:
        return 0, 0
    normalized = value.lower()
    aliases = {
        "daily": (86400, 0),
        "每天": (86400, 0),
        "weekly": (604800, 0),
        "每周": (604800, 0),
        "monthly": (0, 1),
        "每月": (0, 1),
    }
    if normalized in aliases:
        return aliases[normalized]
    match = re.fullmatch(r"(\d+)\s*([mhdw])", normalized)
    if match:
        multiplier = {"m": 60, "h": 3600, "d": 86400, "w": 604800}[match.group(2)]
        return int(match.group(1)) * multiplier, 0
    raise ValueError("重复规则可用：daily/每天、weekly/每周、monthly/每月、2d、12h")


def parse_duration_minutes(value: str) -> int:
    match = re.fullmatch(r"(\d+)\s*(m|h|d|分钟|小时|天)?", value.lower())
    if not match:
        raise ValueError("提醒时间可用：30m、2h、1d")
    amount = int(match.group(1))
    unit = match.group(2) or "m"
    return (
        amount * {"m": 1, "分钟": 1, "h": 60, "小时": 60, "d": 1440, "天": 1440}[unit]
    )


def secretary_clarification_reason(
    title: str, due: str, repeat: str, project: str, is_reminder: bool
) -> str | None:
    """Return why an LLM must clarify instead of creating a task."""
    lowered = title.casefold()
    reasons: list[str] = []
    if is_reminder and (
        not due
        or not (
            ":" in due
            or "点" in due
            or re.search(r"\d+\s*(分钟|分|小时|天)后", due)
            or "T" in due
        )
    ):
        reasons.append("这是提醒事项，但还没有明确到具体时间的截止日期")
    if not repeat and any(word in lowered for word in {"每天", "每周", "每月", "定期"}):
        reasons.append("标题包含周期含义，需要确认具体重复频率")
    return "；".join(reasons) if reasons else None


def sender_is_allowed(sender_id: str, configured: list[Any] | None) -> bool:
    allowed = {str(value).strip() for value in (configured or []) if str(value).strip()}
    return not allowed or str(sender_id) in allowed


def platform_sender_is_allowed(
    platform: str, sender_id: str, allowed_qq_sender_ids: list[Any] | None
) -> bool:
    if platform == "weixin_oc":
        return True
    if platform in {"qq_official", "qq_official_webhook"}:
        return sender_is_allowed(sender_id, allowed_qq_sender_ids)
    return False


@dataclass(slots=True)
class AddSpec:
    title: str
    project_selector: str = ""
    description: str = ""
    due: datetime | None = None
    priority: int = 0
    repeat_after: int = 0
    repeat_mode: int = 0
    reminder_minutes: int | None = None

    def payload(self) -> dict[str, Any]:
        result: dict[str, Any] = {"title": self.title, "priority": self.priority}
        if self.description:
            result["description"] = self.description
        if self.due:
            result["due_date"] = to_vikunja_time(self.due)
        if self.repeat_after or self.repeat_mode:
            result["repeat_after"] = self.repeat_after
            result["repeat_mode"] = self.repeat_mode
        return result


def parse_add_arguments(
    text: str, tz: ZoneInfo, now: datetime | None = None
) -> AddSpec:
    try:
        tokens = shlex.split(text)
    except ValueError as exc:
        raise ValueError("参数引号没有闭合") from exc
    values: dict[str, str] = {}
    title_parts: list[str] = []
    aliases = {
        "--due": "due",
        "-d": "due",
        "--priority": "priority",
        "-p": "priority",
        "--repeat": "repeat",
        "-r": "repeat",
        "--remind": "remind",
        "--desc": "description",
        "--project": "project",
        "-P": "project",
    }
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token in aliases:
            if index + 1 >= len(tokens):
                raise ValueError(f"{token} 后缺少参数")
            values[aliases[token]] = tokens[index + 1]
            index += 2
        elif token.startswith("-"):
            raise ValueError(f"未知选项：{token}")
        else:
            title_parts.append(token)
            index += 1
    title = " ".join(title_parts).strip()
    if not title:
        raise ValueError("任务标题不能为空")
    priority = int(values.get("priority", 0))
    if priority < 0 or priority > 5:
        raise ValueError("优先级应为 0 到 5")
    repeat_after, repeat_mode = parse_repeat(values.get("repeat"))
    due = parse_datetime(values["due"], tz, now) if "due" in values else None
    if (repeat_after or repeat_mode) and due is None:
        raise ValueError("重复任务必须同时设置 --due")
    remind = parse_duration_minutes(values["remind"]) if "remind" in values else None
    if remind is not None and due is None:
        raise ValueError("自定义提醒必须同时设置 --due")
    return AddSpec(
        title=title,
        project_selector=values.get("project", ""),
        description=values.get("description", ""),
        due=due,
        priority=priority,
        repeat_after=repeat_after,
        repeat_mode=repeat_mode,
        reminder_minutes=remind,
    )


def task_sort_key(task: dict[str, Any]) -> tuple[int, datetime, int]:
    due = from_vikunja_time(task.get("due_date")) or datetime.max.replace(
        tzinfo=timezone.utc
    )
    return (-int(task.get("priority") or 0), due, int(task.get("id") or 0))


def select_tasks(
    tasks: list[dict[str, Any]], scope: str, tz: ZoneInfo, now: datetime | None = None
) -> list[dict[str, Any]]:
    now = (now or datetime.now(tz)).astimezone(tz)
    result: list[dict[str, Any]] = []
    for task in tasks:
        if task.get("done"):
            continue
        due = from_vikunja_time(task.get("due_date"))
        local_due = due.astimezone(tz) if due else None
        if scope == "today" and (not local_due or local_due.date() > now.date()):
            continue
        if scope == "overdue" and (not local_due or local_due >= now):
            continue
        if scope == "week" and (
            not local_due or local_due.date() > now.date() + timedelta(days=7)
        ):
            continue
        result.append(task)
    return sorted(result, key=task_sort_key)


def format_task_list(
    tasks: list[dict[str, Any]],
    title: str,
    tz: ZoneInfo,
    limit: int = 20,
    project_paths: dict[int, str] | None = None,
) -> str:
    if not tasks:
        return f"{title}\n🎉 没有未完成任务"
    lines = [title]
    for task in tasks[: max(1, limit)]:
        priority = int(task.get("priority") or 0)
        due = from_vikunja_time(task.get("due_date"))
        due_text = due.astimezone(tz).strftime("%m-%d %H:%M") if due else "无截止时间"
        repeat = (
            " ↻"
            if int(task.get("repeat_after") or 0) or int(task.get("repeat_mode") or 0)
            else ""
        )
        lines.append(
            f"{len(lines)}. [P{priority}] #{task.get('id')} {task.get('title', '')}{repeat}\n"
            f"   📁 {(project_paths or {}).get(int(task.get('project_id') or 0), '未知项目')}  ⏰ {due_text}"
        )
    if len(tasks) > limit:
        lines.append(f"…另有 {len(tasks) - limit} 项未显示")
    return "\n".join(lines)


def build_project_paths(projects: list[dict[str, Any]]) -> dict[int, str]:
    by_id = {int(project["id"]): project for project in projects}
    cache: dict[int, str] = {}

    def build(project_id: int, visiting: set[int]) -> str:
        if project_id in cache:
            return cache[project_id]
        project = by_id[project_id]
        title = str(project.get("title") or project_id)
        parent_id = int(project.get("parent_project_id") or 0)
        if not parent_id or parent_id not in by_id or parent_id in visiting:
            cache[project_id] = title
        else:
            cache[project_id] = f"{build(parent_id, visiting | {project_id})}/{title}"
        return cache[project_id]

    for project_id in by_id:
        build(project_id, set())
    return cache


def resolve_project(
    projects: list[dict[str, Any]], selector: str
) -> tuple[dict[str, Any], str]:
    selector = selector.strip().replace(" > ", "/").strip("/")
    if not selector:
        raise ValueError("没有指定目标项目")
    paths = build_project_paths(projects)
    by_id = {int(project["id"]): project for project in projects}
    if selector.isdigit() and int(selector) in by_id:
        project = by_id[int(selector)]
        return project, paths[int(selector)]
    normalized = selector.casefold()
    path_matches = [pid for pid, path in paths.items() if path.casefold() == normalized]
    if len(path_matches) == 1:
        pid = path_matches[0]
        return by_id[pid], paths[pid]
    title_matches = [
        int(project["id"])
        for project in projects
        if str(project.get("title", "")).casefold() == normalized
    ]
    if len(title_matches) == 1:
        pid = title_matches[0]
        return by_id[pid], paths[pid]
    matches = path_matches or title_matches
    if matches:
        choices = "、".join(f"{paths[pid]} (#{pid})" for pid in matches)
        raise ValueError(f"项目名不唯一，请使用完整路径或 ID：{choices}")
    raise ValueError(f"找不到项目：{selector}。请先查询项目树")


def format_project_tree(projects: list[dict[str, Any]]) -> str:
    children: dict[int, list[dict[str, Any]]] = {}
    ids = {int(project["id"]) for project in projects}
    for project in projects:
        parent = int(project.get("parent_project_id") or 0)
        if parent not in ids:
            parent = 0
        children.setdefault(parent, []).append(project)

    lines = ["📁 Vikunja 项目树"]

    def walk(parent: int, depth: int) -> None:
        for project in sorted(
            children.get(parent, []),
            key=lambda item: str(item.get("title", "")).casefold(),
        ):
            lines.append(
                f"{'  ' * depth}• {project.get('title', '')} (#{project['id']})"
            )
            walk(int(project["id"]), depth + 1)

    walk(0, 0)
    return "\n".join(lines)
