"""Asynchronous client for the Vikunja API (verified against server v2.5.0).

Only endpoints that exist in v2.5.0 are used. Saved filters are exposed by the
server as pseudo projects with negative ids inside ``GET /projects``; there is no
``GET /filters`` in this version, so listing happens through that route.
"""

from __future__ import annotations

from typing import Any, Iterable
from urllib.parse import urlsplit, urlunsplit

try:
    import aiohttp
except (
    ModuleNotFoundError
):  # Allows schema/unit tests before plugin dependencies are installed.
    aiohttp = None  # type: ignore[assignment]


# The server reports max_items_per_page = 50; asking for more is silently clamped.
PAGE_SIZE = 50

# Vikunja's "empty" timestamp; sending it clears a date field.
ZERO_DATE = "0001-01-01T00:00:00Z"

DATE_TASK_FIELDS = {"due_date", "start_date", "end_date"}

# Fields that may be written back on POST /tasks/{id}. bucket_id and position are
# view-scoped and therefore only sent when explicitly requested.
WRITABLE_TASK_FIELDS = (
    "title",
    "description",
    "done",
    "due_date",
    "start_date",
    "end_date",
    "priority",
    "percent_done",
    "hex_color",
    "repeat_after",
    "repeat_mode",
    "reminders",
    "project_id",
    "is_favorite",
)

RELATION_KINDS = {
    "subtask",
    "parenttask",
    "related",
    "duplicateof",
    "duplicates",
    "blocking",
    "blocked",
    "precedes",
    "follows",
}


class VikunjaError(RuntimeError):
    """A user-presentable Vikunja API error."""


def normalize_api_url(value: str) -> str:
    value = value.strip().rstrip("/")
    if not value:
        return ""
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ValueError("Vikunja 地址必须是完整的 http(s) URL")
    path = parts.path.rstrip("/")
    if not path.endswith("/api/v1"):
        path += "/api/v1"
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def web_base_url(api_url: str) -> str:
    """Turn the API base url back into the browser url for board links."""
    if not api_url:
        return ""
    parts = urlsplit(api_url)
    path = parts.path
    if path.endswith("/api/v1"):
        path = path[: -len("/api/v1")]
    return urlunsplit((parts.scheme, parts.netloc, path.rstrip("/"), "", ""))


class VikunjaClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        timeout: int = 15,
        filter_timezone: str = "",
    ):
        self.base_url = normalize_api_url(base_url)
        self.token = token.strip()
        self.timeout = max(1, int(timeout))
        self.filter_timezone = filter_timezone.strip()
        self._session: aiohttp.ClientSession | None = None

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.token)

    @property
    def web_url(self) -> str:
        return web_base_url(self.base_url)

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def _get_session(self) -> aiohttp.ClientSession:
        if aiohttp is None:
            raise VikunjaError("缺少 aiohttp 依赖，请安装插件 requirements.txt")
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.timeout),
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Accept": "application/json",
                },
            )
        return self._session

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: list[tuple[str, str]] | dict[str, Any] | None = None,
        json: Any | None = None,
    ) -> tuple[Any, Any]:
        if not self.configured:
            raise VikunjaError("插件尚未配置 Vikunja 地址和 API Token")
        session = await self._get_session()
        url = f"{self.base_url}/{path.lstrip('/')}"
        try:
            async with session.request(
                method, url, params=params, json=json
            ) as response:
                try:
                    payload = await response.json(content_type=None)
                except Exception:
                    payload = {"message": (await response.text())[:500]}
                if response.status < 200 or response.status >= 300:
                    message = (
                        payload.get("message")
                        if isinstance(payload, dict)
                        else str(payload)
                    )
                    raise VikunjaError(
                        f"Vikunja 请求失败（HTTP {response.status}）：{message or '未知错误'}"
                    )
                return payload, response.headers
        except VikunjaError:
            raise
        except ((aiohttp.ClientError if aiohttp else OSError), TimeoutError) as exc:
            raise VikunjaError(f"无法连接 Vikunja：{exc}") from exc

    async def _paged(
        self,
        path: str,
        params: list[tuple[str, str]] | None = None,
        limit_pages: int = 40,
    ) -> list[dict[str, Any]]:
        collected: list[dict[str, Any]] = []
        page = 1
        while page <= limit_pages:
            payload, headers = await self._request(
                "GET",
                path,
                params=[
                    *(params or []),
                    ("page", str(page)),
                    ("per_page", str(PAGE_SIZE)),
                ],
            )
            if payload is None:
                break
            if not isinstance(payload, list):
                raise VikunjaError(f"Vikunja 对 {path} 返回了无法识别的列表")
            collected.extend(payload)
            total_pages = int(headers.get("x-pagination-total-pages", page) or page)
            if page >= total_pages or not payload:
                break
            page += 1
        return collected

    # ------------------------------------------------------------------ server

    async def info(self) -> dict[str, Any]:
        payload, _ = await self._request("GET", "info")
        return payload if isinstance(payload, dict) else {}

    # ---------------------------------------------------------------- projects

    async def list_projects(self) -> list[dict[str, Any]]:
        """Real projects only: archived ones and saved-filter pseudo projects are dropped."""
        raw = await self._paged("projects")
        return [
            project
            for project in raw
            if int(project.get("id") or 0) > 0 and not project.get("is_archived")
        ]

    async def list_saved_filters(self) -> list[dict[str, Any]]:
        """Saved filters, reported by the server as projects with negative ids."""
        raw = await self._paged("projects")
        return [project for project in raw if int(project.get("id") or 0) < 0]

    async def get_project(self, project_id: int) -> dict[str, Any]:
        payload, _ = await self._request("GET", f"projects/{project_id}")
        return payload

    async def create_project(
        self, title: str, parent_project_id: int = 0, description: str = ""
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"title": title}
        if parent_project_id:
            body["parent_project_id"] = int(parent_project_id)
        if description:
            body["description"] = description
        payload, _ = await self._request("PUT", "projects", json=body)
        return payload

    # ------------------------------------------------------------------- tasks

    async def create_task(
        self, project_id: int, task: dict[str, Any]
    ) -> dict[str, Any]:
        payload, _ = await self._request(
            "PUT", f"projects/{project_id}/tasks", json=task
        )
        return payload

    async def get_task(self, task_id: int) -> dict[str, Any]:
        payload, _ = await self._request("GET", f"tasks/{task_id}")
        return payload

    async def update_task(
        self, task_id: int, changes: dict[str, Any]
    ) -> dict[str, Any]:
        """Merge onto the current task before POSTing.

        Vikunja treats omitted date fields as "clear this date", which is why the
        web frontend always sends the full task back. A ``None`` value in
        ``changes`` explicitly clears the field.
        """
        current = await self.get_task(task_id)
        body: dict[str, Any] = {
            key: current[key] for key in WRITABLE_TASK_FIELDS if key in current
        }
        for key, value in changes.items():
            if value is None:
                body[key] = ZERO_DATE if key in DATE_TASK_FIELDS else None
            else:
                body[key] = value
        payload, _ = await self._request("POST", f"tasks/{task_id}", json=body)
        return payload

    async def delete_task(self, task_id: int) -> None:
        await self._request("DELETE", f"tasks/{task_id}")

    async def complete_task(self, task_id: int) -> dict[str, Any]:
        return await self.update_task(task_id, {"done": True})

    async def list_tasks(
        self,
        filter_query: str = "",
        *,
        project_id: int | None = None,
        include_done: bool = False,
        include_nulls: bool = False,
        sort: Iterable[tuple[str, str]] = (("priority", "desc"), ("due_date", "asc")),
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        if project_id is not None:
            clauses.append(f"project_id = {int(project_id)}")
        if not include_done:
            clauses.append("done = false")
        if filter_query.strip():
            clauses.append(f"({filter_query.strip()})")
        params: list[tuple[str, str]] = []
        for field, direction in sort:
            params.append(("sort_by", field))
            params.append(("order_by", direction))
        if clauses:
            params.append(("filter", " && ".join(clauses)))
        if include_nulls:
            params.append(("filter_include_nulls", "true"))
        if self.filter_timezone:
            params.append(("filter_timezone", self.filter_timezone))
        try:
            return await self._paged("tasks", params)
        except VikunjaError:
            if project_id is None:
                raise
            # Some deployments reject project_id inside /tasks filters; fall back.
            fallback = await self.list_tasks(
                filter_query,
                include_done=include_done,
                include_nulls=include_nulls,
                sort=sort,
            )
            return [
                task
                for task in fallback
                if int(task.get("project_id") or 0) == int(project_id)
            ]

    # ------------------------------------------------------------------ labels

    async def list_labels(self) -> list[dict[str, Any]]:
        return await self._paged("labels")

    async def create_label(
        self, title: str, hex_color: str = "", description: str = ""
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"title": title}
        if hex_color:
            body["hex_color"] = hex_color.lstrip("#")
        if description:
            body["description"] = description
        payload, _ = await self._request("PUT", "labels", json=body)
        return payload

    async def add_label_to_task(self, task_id: int, label_id: int) -> None:
        await self._request(
            "PUT", f"tasks/{task_id}/labels", json={"label_id": int(label_id)}
        )

    async def remove_label_from_task(self, task_id: int, label_id: int) -> None:
        await self._request("DELETE", f"tasks/{task_id}/labels/{int(label_id)}")

    # --------------------------------------------------------------- relations

    async def create_relation(
        self, task_id: int, other_task_id: int, kind: str
    ) -> None:
        if kind not in RELATION_KINDS:
            raise VikunjaError(f"不支持的任务关系：{kind}")
        await self._request(
            "PUT",
            f"tasks/{task_id}/relations",
            json={
                "task_id": int(task_id),
                "other_task_id": int(other_task_id),
                "relation_kind": kind,
            },
        )

    async def delete_relation(
        self, task_id: int, other_task_id: int, kind: str
    ) -> None:
        await self._request(
            "DELETE", f"tasks/{task_id}/relations/{kind}/{int(other_task_id)}"
        )

    # ---------------------------------------------------------------- comments

    async def create_comment(self, task_id: int, text: str) -> dict[str, Any]:
        payload, _ = await self._request(
            "PUT", f"tasks/{task_id}/comments", json={"comment": text}
        )
        return payload

    async def list_comments(self, task_id: int) -> list[dict[str, Any]]:
        return await self._paged(f"tasks/{task_id}/comments", [("order_by", "desc")])

    # ------------------------------------------------------------------- views

    async def list_views(self, project_id: int) -> list[dict[str, Any]]:
        payload, _ = await self._request("GET", f"projects/{project_id}/views")
        return payload if isinstance(payload, list) else []

    async def update_view(
        self, project_id: int, view_id: int, view: dict[str, Any]
    ) -> dict[str, Any]:
        payload, _ = await self._request(
            "POST", f"projects/{project_id}/views/{view_id}", json=view
        )
        return payload

    async def list_buckets(self, project_id: int, view_id: int) -> list[dict[str, Any]]:
        payload, _ = await self._request(
            "GET", f"projects/{project_id}/views/{view_id}/buckets"
        )
        return payload if isinstance(payload, list) else []

    # ----------------------------------------------------------- saved filters

    async def create_saved_filter(
        self,
        title: str,
        filter_query: str,
        *,
        description: str = "",
        sort_by: list[str] | None = None,
        order_by: list[str] | None = None,
        include_nulls: bool = False,
    ) -> dict[str, Any]:
        body = {
            "title": title,
            "description": description,
            "filters": {
                "filter": filter_query,
                "filter_include_nulls": include_nulls,
                "sort_by": sort_by or ["due_date", "priority"],
                "order_by": order_by or ["asc", "desc"],
            },
        }
        payload, _ = await self._request("PUT", "filters", json=body)
        return payload

    async def update_saved_filter(
        self,
        filter_id: int,
        title: str,
        filter_query: str,
        *,
        description: str = "",
        sort_by: list[str] | None = None,
        order_by: list[str] | None = None,
        include_nulls: bool = False,
    ) -> dict[str, Any]:
        body = {
            "id": int(filter_id),
            "title": title,
            "description": description,
            "filters": {
                "filter": filter_query,
                "filter_include_nulls": include_nulls,
                "sort_by": sort_by or ["due_date", "priority"],
                "order_by": order_by or ["asc", "desc"],
            },
        }
        payload, _ = await self._request("POST", f"filters/{filter_id}", json=body)
        return payload
