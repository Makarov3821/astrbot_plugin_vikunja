"""Daily agenda composition and time-block scheduling.

Pure functions over Vikunja task dicts so the behaviour is unit-testable without
a server or an LLM.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from .todo_domain import (
    LABEL_WAITING,
    block_covers_day,
    checklist_progress,
    estimate_minutes,
    format_percent,
    format_repeat,
    format_task_line,
    from_vikunja_time,
    has_label,
    is_time_block,
    task_labels,
    task_sort_key,
)

DEFAULT_WINDOWS = "09:00-12:00,14:00-18:00,19:30-22:00"


@dataclass
class Agenda:
    overdue: list[dict[str, Any]] = field(default_factory=list)
    blocks: list[dict[str, Any]] = field(default_factory=list)
    due_today: list[dict[str, Any]] = field(default_factory=list)
    due_soon: list[dict[str, Any]] = field(default_factory=list)
    waiting: list[dict[str, Any]] = field(default_factory=list)
    unscheduled: list[dict[str, Any]] = field(default_factory=list)
    # Cross-cutting view: anything with progress > 0 that is not finished yet.
    in_progress: list[dict[str, Any]] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not (
            self.overdue
            or self.blocks
            or self.due_today
            or self.due_soon
            or self.waiting
            or self.unscheduled
        )


def split_agenda(
    tasks: list[dict[str, Any]],
    tz: ZoneInfo,
    now: datetime | None = None,
    soon_days: int = 3,
) -> Agenda:
    now = (now or datetime.now(tz)).astimezone(tz)
    today = now.date()
    agenda = Agenda()
    for task in tasks:
        if task.get("done"):
            continue
        due = from_vikunja_time(task.get("due_date"))
        local_due = due.astimezone(tz) if due else None
        if has_label(task, LABEL_WAITING):
            agenda.waiting.append(task)
            continue
        if is_time_block(task, tz) and block_covers_day(task, tz, today):
            agenda.blocks.append(task)
            continue
        if local_due and local_due < now:
            agenda.overdue.append(task)
            continue
        if local_due and local_due.date() == today:
            agenda.due_today.append(task)
            continue
        if local_due and local_due.date() <= today + timedelta(days=soon_days):
            agenda.due_soon.append(task)
            continue
        if not local_due and not is_time_block(task, tz):
            # A bare start_date anchor only exists to draw a Gantt bar.
            agenda.unscheduled.append(task)
    agenda.overdue.sort(key=task_sort_key)
    agenda.due_today.sort(key=task_sort_key)
    agenda.due_soon.sort(key=task_sort_key)
    agenda.waiting.sort(key=task_sort_key)
    agenda.unscheduled.sort(key=task_sort_key)
    agenda.blocks.sort(
        key=lambda task: (
            from_vikunja_time(task.get("start_date"))
            or datetime.max.replace(tzinfo=tz),
            -int(task.get("priority") or 0),
        )
    )
    agenda.in_progress = sorted(
        (
            task
            for task in tasks
            if not task.get("done")
            and _progress_started(task)
            and not has_label(task, LABEL_WAITING)
        ),
        key=task_sort_key,
    )
    return agenda


def _progress_started(task: dict[str, Any]) -> bool:
    """Something was already done on this task: percent_done or a ticked checklist."""
    try:
        percent = float(task.get("percent_done") or 0)
    except (TypeError, ValueError):
        percent = 0.0
    if percent > 0:
        return True
    checked, _ = checklist_progress(str(task.get("description") or ""))
    return checked > 0


def format_agenda(
    agenda: Agenda,
    tz: ZoneInfo,
    project_paths: dict[int, str] | None = None,
    limit_per_group: int = 8,
) -> str:
    if agenda.is_empty:
        return "今天没有排期、没有到期、也没有逾期的任务。"
    groups = (
        ("⚠️ 逾期，需要重新安排", agenda.overdue),
        ("🕘 今天的时间块", agenda.blocks),
        ("📌 今天到期", agenda.due_today),
        ("📅 近几天到期", agenda.due_soon),
        ("⏳ 等别人（不占今天）", agenda.waiting),
        ("📥 没排期（可以挑一件排进今天）", agenda.unscheduled),
    )
    lines: list[str] = []
    for title, tasks in groups:
        if not tasks:
            continue
        lines.append(title)
        for task in tasks[:limit_per_group]:
            lines.append("• " + format_task_line(task, tz, project_paths))
        if len(tasks) > limit_per_group:
            lines.append(f"  …另有 {len(tasks) - limit_per_group} 项")
    return "\n".join(lines)


def agenda_digest(
    agenda: Agenda,
    tz: ZoneInfo,
    project_paths: dict[int, str] | None = None,
) -> str:
    """Compact, machine-friendly digest for LLM prompts."""

    def render(tasks: Iterable[dict[str, Any]]) -> str:
        rows = []
        for task in tasks:
            due = from_vikunja_time(task.get("due_date"))
            start = from_vikunja_time(task.get("start_date"))
            description = str(task.get("description") or "")
            checked, total = checklist_progress(description)
            extras = []
            percent = format_percent(task)
            if percent:
                extras.append(f"进度={percent}")
            if total:
                extras.append(f"清单={checked}/{total}")
            repeat = format_repeat(task)
            if repeat:
                extras.append(f"重复={repeat}")
            if description and not total:
                extras.append("有描述")
            rows.append(
                "#{id} {title} | 项目={project} | 优先级=P{priority} | 截止={due} | "
                "时间块={start} | 估时={est}分钟 | 标签={labels}{extras}".format(
                    id=task.get("id"),
                    title=task.get("title", ""),
                    project=(project_paths or {}).get(
                        int(task.get("project_id") or 0), "?"
                    ),
                    priority=int(task.get("priority") or 0),
                    due=due.astimezone(tz).strftime("%m-%d %H:%M") if due else "无",
                    start=start.astimezone(tz).strftime("%m-%d %H:%M")
                    if start
                    else "无",
                    est=estimate_minutes(task),
                    labels=",".join(task_labels(task)) or "无",
                    extras=(" | " + " | ".join(extras)) if extras else "",
                )
            )
        return "\n".join(rows) if rows else "（无）"

    return (
        f"逾期：\n{render(agenda.overdue)}\n"
        f"今天已排时间块：\n{render(agenda.blocks)}\n"
        f"今天到期：\n{render(agenda.due_today)}\n"
        f"近几天到期：\n{render(agenda.due_soon)}\n"
        f"等别人：\n{render(agenda.waiting)}\n"
        f"没排期：\n{render(agenda.unscheduled)}\n"
        f"已经动过手但没做完（优先续上，不要重新开新的）：\n{render(agenda.in_progress)}"
    )


def parse_windows(
    text: str, tz: ZoneInfo, day: datetime
) -> list[tuple[datetime, datetime]]:
    """Parse ``09:00-12:00,14:00-18:00`` into concrete intervals on ``day``."""
    windows: list[tuple[datetime, datetime]] = []
    for chunk in re.split(r"[,，;；]+", text.strip()):
        chunk = chunk.strip().replace("：", ":").replace("~", "-").replace("—", "-")
        if not chunk:
            continue
        match = re.fullmatch(
            r"(\d{1,2})(?::(\d{2}))?\s*-\s*(\d{1,2})(?::(\d{2}))?", chunk
        )
        if not match:
            raise ValueError(
                f"无法识别时间窗“{chunk}”，请使用 09:00-12:00,14:00-18:00 这样的格式"
            )
        start_hour, start_minute, end_hour, end_minute = (
            int(match.group(1)),
            int(match.group(2) or 0),
            int(match.group(3)),
            int(match.group(4) or 0),
        )
        if not (0 <= start_hour <= 23 and 0 <= end_hour <= 24):
            raise ValueError(f"时间窗“{chunk}”超出合法范围")
        start = datetime.combine(day.date(), time(start_hour, start_minute), tz)
        end = (
            datetime.combine(day.date(), time(0, 0), tz) + timedelta(days=1)
            if end_hour == 24
            else datetime.combine(day.date(), time(end_hour, end_minute), tz)
        )
        if end <= start:
            raise ValueError(f"时间窗“{chunk}”的结束时间必须晚于开始时间")
        windows.append((start, end))
    if not windows:
        raise ValueError("没有可用的时间窗")
    return sorted(windows)


def busy_intervals(
    tasks: list[dict[str, Any]], tz: ZoneInfo, day: datetime
) -> list[tuple[datetime, datetime]]:
    """Existing time blocks on ``day``, so planning never double-books."""
    intervals: list[tuple[datetime, datetime]] = []
    for task in tasks:
        start = from_vikunja_time(task.get("start_date"))
        if not start or not is_time_block(task, tz):
            continue
        local_start = start.astimezone(tz)
        if local_start.date() != day.date():
            continue
        end = from_vikunja_time(task.get("end_date"))
        local_end = (
            end.astimezone(tz)
            if end
            else local_start + timedelta(minutes=estimate_minutes(task))
        )
        if local_end <= local_start:
            local_end = local_start + timedelta(minutes=estimate_minutes(task))
        intervals.append((local_start, local_end))
    return sorted(intervals)


def free_slots(
    windows: list[tuple[datetime, datetime]],
    busy: list[tuple[datetime, datetime]],
    earliest: datetime,
) -> list[tuple[datetime, datetime]]:
    slots: list[tuple[datetime, datetime]] = []
    for window_start, window_end in windows:
        cursor = max(window_start, earliest)
        for busy_start, busy_end in busy:
            if busy_end <= cursor or busy_start >= window_end:
                continue
            if busy_start > cursor:
                slots.append((cursor, min(busy_start, window_end)))
            cursor = max(cursor, busy_end)
        if cursor < window_end:
            slots.append((cursor, window_end))
    return [(start, end) for start, end in slots if end > start]


def plan_blocks(
    candidates: list[dict[str, Any]],
    windows: list[tuple[datetime, datetime]],
    busy: list[tuple[datetime, datetime]],
    now: datetime,
    *,
    default_minutes: int = 30,
    gap_minutes: int = 10,
    max_items: int = 8,
) -> tuple[list[tuple[dict[str, Any], datetime, datetime]], list[dict[str, Any]]]:
    """Greedily pack candidates into free time, earliest deadline first.

    Returns the planned blocks and the candidates that did not fit.
    """
    slots = free_slots(windows, busy, now)
    planned: list[tuple[dict[str, Any], datetime, datetime]] = []
    unplanned: list[dict[str, Any]] = []
    for task in candidates[: max(1, max_items)]:
        minutes = estimate_minutes(task, default_minutes)
        placed = False
        for index, (slot_start, slot_end) in enumerate(slots):
            if (slot_end - slot_start) >= timedelta(minutes=minutes):
                block_end = slot_start + timedelta(minutes=minutes)
                planned.append((task, slot_start, block_end))
                next_start = block_end + timedelta(minutes=gap_minutes)
                slots[index] = (min(next_start, slot_end), slot_end)
                placed = True
                break
        if not placed:
            unplanned.append(task)
    return planned, unplanned


def planning_candidates(agenda: Agenda) -> list[dict[str, Any]]:
    """What is worth putting into today's plan, most urgent first."""
    # Tasks that already own a block today must not be scheduled a second time.
    seen: set[int] = {int(task.get("id") or 0) for task in agenda.blocks}
    ordered: list[dict[str, Any]] = []
    for group in (
        agenda.overdue,
        agenda.due_today,
        agenda.in_progress,
        agenda.due_soon,
        agenda.unscheduled,
    ):
        for task in group:
            task_id = int(task.get("id") or 0)
            if task_id in seen:
                continue
            seen.add(task_id)
            ordered.append(task)
    return ordered


def format_plan(
    planned: list[tuple[dict[str, Any], datetime, datetime]],
    unplanned: list[dict[str, Any]],
    tz: ZoneInfo,
    project_paths: dict[int, str] | None = None,
    applied: bool = False,
) -> str:
    if not planned:
        return "可用时间里排不进任何任务；要么时间窗太窄，要么今天已经排满了。"
    lines = ["🗓 今日排程建议" if not applied else "🗓 今日排程已写入 Vikunja"]
    for task, start, end in planned:
        path = (project_paths or {}).get(int(task.get("project_id") or 0), "?")
        lines.append(
            f"{start.astimezone(tz).strftime('%H:%M')}-"
            f"{end.astimezone(tz).strftime('%H:%M')}  "
            f"#{task.get('id')} {task.get('title', '')}（{path}）"
        )
    if unplanned:
        lines.append(
            "没排进去：" + "、".join(f"#{task.get('id')}" for task in unplanned)
        )
    if applied:
        lines.append("已写入 start_date/end_date，可在网页甘特图上直接拖动调整。")
    return "\n".join(lines)


def briefing_text(
    agenda: Agenda,
    tz: ZoneInfo,
    now: datetime,
    project_paths: dict[int, str] | None = None,
    board_url: str = "",
) -> str:
    """Deterministic morning briefing, also used as the LLM's source material."""
    header = f"☀️ {now.astimezone(tz).strftime('%m-%d %A')} 早报"
    body = format_agenda(agenda, tz, project_paths, limit_per_group=6)
    counts = (
        f"逾期 {len(agenda.overdue)} · 今天到期 {len(agenda.due_today)} · "
        f"已排块 {len(agenda.blocks)} · 等别人 {len(agenda.waiting)} · "
        f"没排期 {len(agenda.unscheduled)}"
    )
    lines = [header, counts, "", body]
    if board_url:
        lines.append("")
        lines.append(f"总看板：{board_url}")
    lines.append("想排今天的时间块，直接说“帮我排一下今天”。")
    return "\n".join(lines)
