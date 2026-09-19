"""Parsing and presentation logic independent from AstrBot."""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ZERO_VIKUNJA_DATE = "0001-01-01T00:00:00Z"


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

# Context labels the bootstrap creates. Used for estimates and agenda grouping.
LABEL_WAITING = "@等待中"
LABEL_DEEP = "@深度"
LABEL_QUICK = "@碎片"
LABEL_OUTSIDE = "@外出"
LABEL_CONTACT = "@要找人"

ESTIMATE_LABELS: dict[str, int] = {
    "est15": 15,
    "est30": 30,
    "est1h": 60,
    "est2h": 120,
    "est4h": 240,
}

MERIDIEM_PM = {"下午", "晚上", "傍晚", "中午"}
MERIDIEM_AM = {"凌晨", "早上", "上午", "早晨"}
_CN_HOURS = {
    "十二": "12",
    "十一": "11",
    "十": "10",
    "九": "9",
    "八": "8",
    "七": "7",
    "六": "6",
    "五": "5",
    "四": "4",
    "三": "3",
    "二": "2",
    "两": "2",
    "一": "1",
}
_ISO_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}([T ]|$)")


def get_timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"未知时区：{name}") from exc


def _normalize_clock(value: str) -> tuple[str, bool]:
    """Strip Chinese meridiem words and numerals, reporting whether PM was meant."""
    pm = False
    for word in MERIDIEM_PM:
        if word in value:
            pm = True
            value = value.replace(word, "")
    for word in MERIDIEM_AM:
        value = value.replace(word, "")
    value = value.replace("点半", ":30").replace("点整", ":00")
    for chinese, digits in _CN_HOURS.items():
        value = value.replace(f"{chinese}点", f"{digits}点")
        value = value.replace(f"{chinese}:", f"{digits}:")
    return value.strip(), pm


def parse_datetime(value: str, tz: ZoneInfo, now: datetime | None = None) -> datetime:
    """Parse ISO 8601 first, then common Chinese expressions; returns an aware datetime."""
    value = value.strip().replace("：", ":")
    now = (now or datetime.now(tz)).astimezone(tz)

    if _ISO_PREFIX.match(value):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            parsed = None
        if parsed is not None:
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=tz)
            return parsed.astimezone(tz)

    value, pm = _normalize_clock(value)
    result = _parse_relative_or_calendar(value, tz, now)
    if pm and result.hour < 12:
        result += timedelta(hours=12)
    return result


def _parse_relative_or_calendar(value: str, tz: ZoneInfo, now: datetime) -> datetime:
    relative = re.fullmatch(r"(\d+)\s*(分钟|分|小时|天|周)后", value)
    if relative:
        amount = int(relative.group(1))
        unit = relative.group(2)
        delta = timedelta(
            minutes=amount if unit in {"分钟", "分"} else 0,
            hours=amount if unit == "小时" else 0,
            days=amount * (7 if unit == "周" else 1) if unit in {"天", "周"} else 0,
        )
        return now + delta

    day_match = re.fullmatch(
        r"(今天|今晚|明天|明晚|后天|大后天)(?:\s*(\d{1,2})(?::|点)(\d{1,2})?(?:分)?)?",
        value,
    )
    if day_match:
        offset = {
            "今天": 0,
            "今晚": 0,
            "明天": 1,
            "明晚": 1,
            "后天": 2,
            "大后天": 3,
        }[day_match.group(1)]
        hour = int(day_match.group(2) or 23)
        minute = int(day_match.group(3) or (59 if day_match.group(2) is None else 0))
        if day_match.group(1) in {"今晚", "明晚"} and hour < 12:
            hour += 12
        return datetime.combine(
            now.date() + timedelta(days=offset), time(hour, minute), tz
        )

    weekday_match = re.fullmatch(
        r"(本周|下周|下下周|周|星期)([一二三四五六日天])"
        r"(?:\s*(\d{1,2})(?::|点)(\d{1,2})?(?:分)?)?",
        value,
    )
    if weekday_match:
        target_weekday = "一二三四五六日天".index(weekday_match.group(2))
        target_weekday = min(target_weekday, 6)
        prefix = weekday_match.group(1)
        days = (target_weekday - now.weekday()) % 7
        if prefix in {"下周", "本周", "下下周"}:
            days = target_weekday - now.weekday()
            days += {"本周": 0, "下周": 7, "下下周": 14}[prefix]
        hour = int(weekday_match.group(3) or 23)
        minute = int(
            weekday_match.group(4) or (59 if weekday_match.group(3) is None else 0)
        )
        result = datetime.combine(
            now.date() + timedelta(days=days), time(hour, minute), tz
        )
        if prefix not in {"本周", "下周", "下下周"} and result <= now:
            result += timedelta(days=7)
        return result

    formats = (
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%m-%d %H:%M",
        "%m-%d",
        "%m/%d %H:%M",
        "%m/%d",
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
            "无法识别时间。推荐使用 ISO 格式 2026-07-12T18:00，"
            "也支持明天9点、下周三下午3点、2小时后"
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
    title: str,
    due: str,
    repeat: str,
    project: str,
    is_reminder: bool,
    reminder_at: str = "",
) -> str | None:
    """Return why an LLM must clarify instead of creating a task."""
    lowered = title.casefold()
    reasons: list[str] = []
    has_clock = any(
        ":" in value
        or "点" in value
        or re.search(r"\d+\s*(分钟|分|小时|天)后", value)
        or "T" in value
        for value in (due, reminder_at)
        if value
    )
    if is_reminder and not has_clock:
        reasons.append("这是提醒事项，但还没有明确到具体时间的提醒或截止时间")
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
    start: datetime | None = None
    end: datetime | None = None
    priority: int = 0
    repeat_after: int = 0
    repeat_mode: int = 0
    reminder_minutes: int | None = None
    labels: list[str] = field(default_factory=list)
    reminders: list[datetime] = field(default_factory=list)
    percent_done: int = 0

    def payload(self) -> dict[str, Any]:
        result: dict[str, Any] = {"title": self.title, "priority": self.priority}
        if self.description:
            result["description"] = self.description
        if self.due:
            result["due_date"] = to_vikunja_time(self.due)
        if self.start:
            result["start_date"] = to_vikunja_time(self.start)
        if self.end:
            result["end_date"] = to_vikunja_time(self.end)
        if self.percent_done:
            result["percent_done"] = max(0, min(100, int(self.percent_done))) / 100
        if self.repeat_after or self.repeat_mode:
            result["repeat_after"] = self.repeat_after
            result["repeat_mode"] = self.repeat_mode
        reminders = build_reminders(
            absolute=self.reminders,
            before_minutes=self.reminder_minutes,
            has_due=self.due is not None,
            has_start=self.start is not None,
        )
        if reminders:
            result["reminders"] = reminders
        return result


def build_reminders(
    absolute: list[datetime] | None = None,
    before_minutes: int | None = None,
    *,
    has_due: bool = False,
    has_start: bool = False,
) -> list[dict[str, Any]]:
    """Build Vikunja ``reminders`` entries: absolute stamps plus one relative offset."""
    reminders: list[dict[str, Any]] = [
        {"reminder": to_vikunja_time(value)} for value in (absolute or [])
    ]
    if before_minutes is not None and before_minutes >= 0:
        anchor = "due_date" if has_due else ("start_date" if has_start else "")
        if anchor:
            reminders.append(
                {
                    "relative_to": anchor,
                    "relative_period": -int(before_minutes) * 60,
                    "reminder": ZERO_VIKUNJA_DATE,
                }
            )
    return reminders


def parse_label_list(value: str) -> list[str]:
    separators = re.split(r"[,，;；\s]+", value.strip())
    return [item for item in (part.strip() for part in separators) if item]


def task_labels(task: dict[str, Any]) -> list[str]:
    return [
        str(label.get("title", ""))
        for label in (task.get("labels") or [])
        if isinstance(label, dict) and label.get("title")
    ]


def estimate_minutes(task: dict[str, Any], default: int = 30) -> int:
    for title in task_labels(task):
        key = title.strip().lower()
        if key in ESTIMATE_LABELS:
            return ESTIMATE_LABELS[key]
    return default


def has_label(task: dict[str, Any], label: str) -> bool:
    target = label.casefold()
    return any(title.casefold() == target for title in task_labels(task))


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
        "--start": "start",
        "-s": "start",
        "--end": "end",
        "-e": "end",
        "--priority": "priority",
        "-p": "priority",
        "--repeat": "repeat",
        "-r": "repeat",
        "--remind": "remind",
        "--label": "label",
        "-l": "label",
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
            key = aliases[token]
            if key == "label" and key in values:
                values[key] = f"{values[key]},{tokens[index + 1]}"
            else:
                values[key] = tokens[index + 1]
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
    start = parse_datetime(values["start"], tz, now) if "start" in values else None
    end = parse_datetime(values["end"], tz, now) if "end" in values else None
    if (repeat_after or repeat_mode) and due is None:
        raise ValueError("重复任务必须同时设置 --due")
    remind = parse_duration_minutes(values["remind"]) if "remind" in values else None
    if remind is not None and due is None and start is None:
        raise ValueError("自定义提醒必须同时设置 --due 或 --start")
    if end and start and end <= start:
        raise ValueError("--end 必须晚于 --start")
    return AddSpec(
        title=title,
        project_selector=values.get("project", ""),
        description=values.get("description", ""),
        due=due,
        start=start,
        end=end,
        priority=priority,
        repeat_after=repeat_after,
        repeat_mode=repeat_mode,
        reminder_minutes=remind,
        labels=parse_label_list(values.get("label", "")),
    )


def task_sort_key(task: dict[str, Any]) -> tuple[int, datetime, int]:
    due = from_vikunja_time(task.get("due_date")) or datetime.max.replace(
        tzinfo=timezone.utc
    )
    return (-int(task.get("priority") or 0), due, int(task.get("id") or 0))


def task_block_sort_key(task: dict[str, Any]) -> tuple[datetime, int, int]:
    start = from_vikunja_time(task.get("start_date")) or datetime.max.replace(
        tzinfo=timezone.utc
    )
    return (start, -int(task.get("priority") or 0), int(task.get("id") or 0))


SCOPES = {
    "today",
    "week",
    "overdue",
    "all",
    "unscheduled",
    "waiting",
    "scheduled",
}


def scope_filter_query(scope: str) -> tuple[str, bool]:
    """Coarse server-side filter for a scope: (filter query, include_nulls).

    Results are always refined locally by :func:`select_tasks`, so the query only
    needs to be a superset. ``now/d`` is resolved by the server in the timezone
    passed as ``filter_timezone``.
    """
    if scope == "overdue":
        return "due_date < now", False
    if scope == "today":
        return "due_date < now/d+1d || start_date < now/d+1d", False
    if scope == "week":
        return "due_date < now/w+1w || start_date < now/w+1w", False
    if scope == "scheduled":
        return "start_date > now/d && start_date < now/d+1d", False
    return "", False


def select_tasks(
    tasks: list[dict[str, Any]],
    scope: str,
    tz: ZoneInfo,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    now = (now or datetime.now(tz)).astimezone(tz)
    today = now.date()
    result: list[dict[str, Any]] = []
    for task in tasks:
        if task.get("done"):
            continue
        due = from_vikunja_time(task.get("due_date"))
        start = from_vikunja_time(task.get("start_date"))
        local_due = due.astimezone(tz) if due else None
        local_start = start.astimezone(tz) if start else None
        if scope == "today":
            if not (
                (local_due and local_due.date() <= today)
                or (local_start and local_start.date() <= today)
            ):
                continue
        elif scope == "overdue":
            if not local_due or local_due >= now:
                continue
        elif scope == "week":
            horizon = today + timedelta(days=7)
            if not (
                (local_due and local_due.date() <= horizon)
                or (local_start and local_start.date() <= horizon)
            ):
                continue
        elif scope == "scheduled":
            if not local_start or local_start.date() != today:
                continue
        elif scope == "unscheduled":
            if local_due or local_start:
                continue
        elif scope == "waiting":
            if not has_label(task, LABEL_WAITING):
                continue
        result.append(task)
    if scope == "scheduled":
        return sorted(result, key=task_block_sort_key)
    return sorted(result, key=task_sort_key)


def format_time_block(task: dict[str, Any], tz: ZoneInfo) -> str:
    start = from_vikunja_time(task.get("start_date"))
    end = from_vikunja_time(task.get("end_date"))
    if not start:
        return ""
    local_start = start.astimezone(tz)
    if end:
        return f"{local_start.strftime('%H:%M')}-{end.astimezone(tz).strftime('%H:%M')}"
    return local_start.strftime("%m-%d %H:%M")


def format_percent(task: dict[str, Any]) -> str:
    raw = task.get("percent_done") or 0
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return ""
    if value <= 0:
        return ""
    percent = value * 100 if value <= 1 else value
    return f"{int(round(percent))}%"


def format_task_line(
    task: dict[str, Any],
    tz: ZoneInfo,
    project_paths: dict[int, str] | None = None,
) -> str:
    priority = int(task.get("priority") or 0)
    due = from_vikunja_time(task.get("due_date"))
    due_text = due.astimezone(tz).strftime("%m-%d %H:%M") if due else "无截止"
    repeat = (
        " ↻"
        if int(task.get("repeat_after") or 0) or int(task.get("repeat_mode") or 0)
        else ""
    )
    block = format_time_block(task, tz)
    labels = task_labels(task)
    percent = format_percent(task)
    details = [
        f"📁 {(project_paths or {}).get(int(task.get('project_id') or 0), '未知项目')}"
    ]
    details.append(f"⏰ {due_text}")
    if block:
        details.append(f"🕘 {block}")
    if labels:
        details.append("🏷 " + " ".join(labels))
    if percent:
        details.append(f"📈 {percent}")
    return (
        f"[P{priority}] #{task.get('id')} {task.get('title', '')}{repeat}\n"
        f"   " + "  ".join(details)
    )


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
    for index, task in enumerate(tasks[: max(1, limit)], start=1):
        lines.append(f"{index}. {format_task_line(task, tz, project_paths)}")
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
