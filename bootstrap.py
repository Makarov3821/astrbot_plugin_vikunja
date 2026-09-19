"""Idempotent Vikunja workspace setup: labels, saved filters, cross-project board.

Vikunja keeps three orthogonal dimensions that this plugin relies on:

* project    - where a task belongs (PhD topic, internship project, errands)
* label      - in which context it can be done, and how long it takes
* date fields- due_date is a real deadline, start_date/end_date is a time block

A project only ever shows its own tasks, and parent projects do not aggregate
their children, so the single big board has to be a *saved filter*: those are
cross-project and get their own list/gantt/table/kanban views.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .vikunja import VikunjaClient, VikunjaError


def saved_filter_id_from_project_id(project_id: int) -> int:
    """Vikunja: filter_id = project_id * -1 - 1 (see models/saved_filters.go)."""
    return int(project_id) * -1 - 1


def project_id_from_saved_filter_id(filter_id: int) -> int:
    return int(filter_id) * -1 - 1


@dataclass(frozen=True)
class LabelSpec:
    title: str
    hex_color: str
    purpose: str


@dataclass(frozen=True)
class BucketSpec:
    title: str
    filter_query: str
    include_nulls: bool = False


@dataclass(frozen=True)
class FilterSpec:
    title: str
    filter_query: str
    description: str = ""
    sort_by: tuple[str, ...] = ("due_date", "priority")
    order_by: tuple[str, ...] = ("asc", "desc")
    include_nulls: bool = False
    buckets: tuple[BucketSpec, ...] = ()


@dataclass(frozen=True)
class ProjectSpec:
    title: str
    children: tuple[str, ...] = ()


LABEL_SPECS: tuple[LabelSpec, ...] = (
    LabelSpec("@深度", "1E6FD9", "需要一整块安静时间"),
    LabelSpec("@碎片", "4CAF50", "十几分钟能做完"),
    LabelSpec("@外出", "FF9800", "出门时顺手办"),
    LabelSpec("@要找人", "9C27B0", "需要联系别人"),
    LabelSpec("@等待中", "795548", "卡在别人身上，不进今日清单"),
    LabelSpec("est15", "B0BEC5", "预计 15 分钟"),
    LabelSpec("est30", "90A4AE", "预计 30 分钟"),
    LabelSpec("est1h", "78909C", "预计 1 小时"),
    LabelSpec("est2h", "607D8B", "预计 2 小时"),
    LabelSpec("est4h", "546E7A", "预计半天"),
)

# ``{label:xxx}`` is replaced with the numeric label id, which the API requires.
NO_DATE_QUERY = "due_date > now+50y"

FILTER_SPECS: tuple[FilterSpec, ...] = (
    FilterSpec(
        title="☀️ 今天",
        filter_query="done = false && (due_date < now/d+1d || start_date < now/d+1d)",
        description="今天到期、已逾期，或今天排了时间块的事。建议设为首页过滤器。",
        sort_by=("due_date", "priority"),
        order_by=("asc", "desc"),
    ),
    FilterSpec(
        title="⏰ 逾期",
        filter_query="done = false && due_date < now",
        description="真的过了死线的事，需要重新安排而不是继续拖。",
    ),
    FilterSpec(
        title="🗓 本周",
        filter_query="done = false && (due_date < now/w+1w || start_date < now/w+1w)",
        description="本周内到期或已排期的事。",
    ),
    FilterSpec(
        title="🧭 总看板",
        filter_query="done = false",
        description="所有项目的未完成任务，按时间自动分列。用 Kanban 视图看。",
        buckets=(
            BucketSpec("等待中", "labels in {label:@等待中}"),
            BucketSpec("今天", "due_date < now/d+1d || start_date < now/d+1d"),
            BucketSpec("本周", "due_date > now/d+1d && due_date < now/w+1w"),
            BucketSpec("以后", "due_date > now/w+1w"),
            BucketSpec("没排期", NO_DATE_QUERY, include_nulls=True),
        ),
    ),
    FilterSpec(
        title="📈 时间线",
        filter_query="done = false && start_date > now-14d",
        description="跨项目甘特图，看 start_date/end_date 排出来的时间块。用 Gantt 视图看。",
        sort_by=("start_date",),
        order_by=("asc",),
    ),
    FilterSpec(
        title="⏳ 等待中",
        filter_query="done = false && labels in {label:@等待中}",
        description="等别人回复或等条件满足的事，定期扫一遍。",
    ),
    FilterSpec(
        title="🚶 出门顺手",
        filter_query="done = false && labels in {label:@外出}",
        description="出门前问秘书一句就能拿到的清单。",
    ),
    FilterSpec(
        title="📥 没排期",
        filter_query=f"done = false && {NO_DATE_QUERY}",
        description="既没有死线也没有时间块的事，别让它无声堆积。",
        include_nulls=True,
    ),
)

PROJECT_SPECS: tuple[ProjectSpec, ...] = (
    ProjectSpec("Inbox"),
    ProjectSpec("PhD", ("课题一", "课题二", "课题三")),
    ProjectSpec("实习", ("项目一", "项目二")),
    ProjectSpec("生活", ("购物与家务", "长期与学习")),
)


@dataclass
class SetupReport:
    created: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    board_url: str = ""

    def text(self) -> str:
        lines: list[str] = ["🛠 Vikunja 秘书结构初始化完成"]
        if self.created:
            lines.append("新建：" + "、".join(self.created))
        if self.skipped:
            lines.append("已存在，跳过：" + "、".join(self.skipped))
        for warning in self.warnings:
            lines.append(f"⚠️ {warning}")
        if self.board_url:
            lines.append(f"总看板：{self.board_url}")
        lines.append(
            "提示：过滤器的“包含未设置该字段的任务”默认开启会让清单失真，"
            "本插件建的过滤器已经按需关掉了。"
        )
        return "\n".join(lines)


def _resolve_placeholders(query: str, label_ids: dict[str, int]) -> str:
    result = query
    for title, label_id in label_ids.items():
        result = result.replace(f"{{label:{title}}}", str(label_id))
    return result


async def ensure_labels(client: VikunjaClient, report: SetupReport) -> dict[str, int]:
    existing = {
        str(label.get("title", "")).casefold(): int(label["id"])
        for label in await client.list_labels()
        if label.get("id")
    }
    label_ids: dict[str, int] = {}
    for spec in LABEL_SPECS:
        key = spec.title.casefold()
        if key in existing:
            label_ids[spec.title] = existing[key]
            report.skipped.append(f"标签 {spec.title}")
            continue
        created = await client.create_label(spec.title, spec.hex_color)
        label_ids[spec.title] = int(created["id"])
        report.created.append(f"标签 {spec.title}")
    return label_ids


async def ensure_saved_filters(
    client: VikunjaClient, label_ids: dict[str, int], report: SetupReport
) -> dict[str, int]:
    """Create the cross-project boards. Returns title -> pseudo project id."""
    existing = {
        str(item.get("title", "")): int(item["id"])
        for item in await client.list_saved_filters()
    }
    pseudo_ids: dict[str, int] = {}
    for spec in FILTER_SPECS:
        query = _resolve_placeholders(spec.filter_query, label_ids)
        if spec.title in existing:
            pseudo_ids[spec.title] = existing[spec.title]
            report.skipped.append(f"过滤器 {spec.title}")
        else:
            created = await client.create_saved_filter(
                spec.title,
                query,
                description=spec.description,
                sort_by=list(spec.sort_by),
                order_by=list(spec.order_by),
                include_nulls=spec.include_nulls,
            )
            filter_id = int(created.get("id") or 0)
            pseudo_ids[spec.title] = project_id_from_saved_filter_id(filter_id)
            report.created.append(f"过滤器 {spec.title}")
        if spec.buckets:
            await _configure_filter_buckets(
                client, spec, pseudo_ids[spec.title], label_ids, report
            )
    return pseudo_ids


async def _configure_filter_buckets(
    client: VikunjaClient,
    spec: FilterSpec,
    pseudo_project_id: int,
    label_ids: dict[str, int],
    report: SetupReport,
) -> None:
    """Turn the kanban view of a saved filter into automatic, filter-driven columns."""
    try:
        views = await client.list_views(pseudo_project_id)
    except VikunjaError as exc:
        report.warnings.append(
            f"{spec.title} 的视图列表读取失败，看板分列请在网页上手动设置：{exc}"
        )
        return
    kanban = next(
        (view for view in views if str(view.get("view_kind")) == "kanban"), None
    )
    if not kanban:
        report.warnings.append(f"{spec.title} 没有 Kanban 视图，跳过分列配置")
        return
    if str(kanban.get("bucket_configuration_mode")) == "filter" and kanban.get(
        "bucket_configuration"
    ):
        report.skipped.append(f"{spec.title} 看板分列")
        return
    payload = {
        "id": int(kanban["id"]),
        "project_id": int(pseudo_project_id),
        "title": str(kanban.get("title") or "Kanban"),
        "view_kind": "kanban",
        "bucket_configuration_mode": "filter",
        "bucket_configuration": [
            {
                "title": bucket.title,
                "filter": {
                    "filter": _resolve_placeholders(bucket.filter_query, label_ids),
                    "filter_include_nulls": bucket.include_nulls,
                },
            }
            for bucket in spec.buckets
        ],
    }
    try:
        await client.update_view(pseudo_project_id, int(kanban["id"]), payload)
        report.created.append(f"{spec.title} 看板分列")
    except VikunjaError as exc:
        report.warnings.append(
            f"{spec.title} 看板自动分列写入失败，可在网页上手动配置：{exc}"
        )


async def ensure_projects(client: VikunjaClient, report: SetupReport) -> None:
    projects = await client.list_projects()
    top_level = {
        str(project.get("title", "")).casefold(): int(project["id"])
        for project in projects
        if not int(project.get("parent_project_id") or 0)
    }
    children: dict[int, set[str]] = {}
    for project in projects:
        parent = int(project.get("parent_project_id") or 0)
        if parent:
            children.setdefault(parent, set()).add(
                str(project.get("title", "")).casefold()
            )
    for spec in PROJECT_SPECS:
        key = spec.title.casefold()
        if key in top_level:
            parent_id = top_level[key]
            report.skipped.append(f"项目 {spec.title}")
        else:
            created = await client.create_project(spec.title)
            parent_id = int(created["id"])
            report.created.append(f"项目 {spec.title}")
        for child in spec.children:
            if child.casefold() in children.get(parent_id, set()):
                report.skipped.append(f"项目 {spec.title}/{child}")
                continue
            await client.create_project(child, parent_project_id=parent_id)
            report.created.append(f"项目 {spec.title}/{child}")


async def apply(client: VikunjaClient, include_projects: bool = False) -> SetupReport:
    report = SetupReport()
    if include_projects:
        await ensure_projects(client, report)
    label_ids = await ensure_labels(client, report)
    pseudo_ids = await ensure_saved_filters(client, label_ids, report)
    board = pseudo_ids.get("🧭 总看板")
    if board and client.web_url:
        report.board_url = f"{client.web_url}/projects/{board}"
    return report


def board_links(client: VikunjaClient, pseudo_ids: dict[str, int]) -> str:
    base = client.web_url
    lines = ["🧭 你的跨项目看板"]
    for spec in FILTER_SPECS:
        pseudo = pseudo_ids.get(spec.title)
        if not pseudo:
            continue
        lines.append(f"• {spec.title}：{base}/projects/{pseudo}")
    lines.append("总看板用 Kanban 视图、时间线用 Gantt 视图打开效果最好。")
    return "\n".join(lines)


def structure_help() -> str:
    lines = [
        "📚 秘书使用的三个维度",
        "1) 项目 = 这件事属于谁（PhD/课题、实习/项目、生活/杂事）",
        "2) 标签 = 什么场景能做 + 要多久："
        + "、".join(spec.title for spec in LABEL_SPECS),
        "3) 日期 = due_date 只填真死线，start_date/end_date 是打算什么时候做",
        "",
        "跨项目的大看板靠“保存的过滤器”，项目页面只会显示自己的任务：",
    ]
    lines.extend(f"• {spec.title}：{spec.description}" for spec in FILTER_SPECS)
    return "\n".join(lines)
