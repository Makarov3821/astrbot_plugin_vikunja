"""Small asynchronous client for the Vikunja v2.3 API."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit, urlunsplit

try:
    import aiohttp
except ModuleNotFoundError:  # Allows schema/unit tests before plugin dependencies are installed.
    aiohttp = None  # type: ignore[assignment]


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


class VikunjaClient:
    def __init__(self, base_url: str, token: str, timeout: int = 15):
        self.base_url = normalize_api_url(base_url)
        self.token = token.strip()
        self.timeout = max(1, int(timeout))
        self._session: aiohttp.ClientSession | None = None

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
        json: dict[str, Any] | None = None,
    ) -> tuple[Any, aiohttp.typedefs.LooseHeaders]:
        if not self.base_url or not self.token:
            raise VikunjaError("插件尚未配置 Vikunja 地址和 API Token")
        session = await self._get_session()
        url = f"{self.base_url}/{path.lstrip('/')}"
        try:
            async with session.request(method, url, params=params, json=json) as response:
                try:
                    payload = await response.json(content_type=None)
                except Exception:
                    payload = {"message": (await response.text())[:500]}
                if response.status < 200 or response.status >= 300:
                    message = payload.get("message") if isinstance(payload, dict) else str(payload)
                    raise VikunjaError(f"Vikunja 请求失败（HTTP {response.status}）：{message or '未知错误'}")
                return payload, response.headers
        except VikunjaError:
            raise
        except ((aiohttp.ClientError if aiohttp else OSError), TimeoutError) as exc:
            raise VikunjaError(f"无法连接 Vikunja：{exc}") from exc

    async def get_project(self, project_id: int) -> dict[str, Any]:
        payload, _ = await self._request("GET", f"projects/{project_id}")
        return payload

    async def list_projects(self) -> list[dict[str, Any]]:
        projects: list[dict[str, Any]] = []
        page = 1
        while True:
            payload, headers = await self._request(
                "GET", "projects", params={"page": page, "per_page": 100}
            )
            if not isinstance(payload, list):
                raise VikunjaError("Vikunja 返回了无法识别的项目列表")
            projects.extend(project for project in payload if not project.get("is_archived"))
            total_pages = int(headers.get("x-pagination-total-pages", page) or page)
            if page >= total_pages or not payload:
                break
            page += 1
        return projects

    async def create_task(self, project_id: int, task: dict[str, Any]) -> dict[str, Any]:
        payload, _ = await self._request("PUT", f"projects/{project_id}/tasks", json=task)
        return payload

    async def get_task(self, task_id: int) -> dict[str, Any]:
        payload, _ = await self._request("GET", f"tasks/{task_id}")
        return payload

    async def update_task(self, task_id: int, changes: dict[str, Any]) -> dict[str, Any]:
        payload, _ = await self._request("POST", f"tasks/{task_id}", json=changes)
        return payload

    async def complete_task(self, task_id: int, project_id: int | None = None) -> dict[str, Any]:
        current = await self.get_task(task_id)
        if project_id is not None and int(current.get("project_id", 0)) != int(project_id):
            raise VikunjaError("该任务不属于你绑定的项目")
        return await self.update_task(task_id, {"done": True})

    async def list_tasks(
        self, project_id: int | None = None, include_done: bool = False
    ) -> list[dict[str, Any]]:
        filters: list[str] = []
        if project_id is not None:
            filters.append(f"project_id = {int(project_id)}")
        if not include_done:
            filters.append("done = false")
        base_params = [
            ("per_page", "100"),
            ("sort_by", "priority"),
            ("order_by", "desc"),
            ("sort_by", "due_date"),
            ("order_by", "asc"),
        ]
        if filters:
            base_params.append(("filter", " && ".join(filters)))
        tasks: list[dict[str, Any]] = []
        page = 1
        while True:
            payload, headers = await self._request(
                "GET", "tasks", params=[*base_params, ("page", str(page))]
            )
            if not isinstance(payload, list):
                raise VikunjaError("Vikunja 返回了无法识别的任务列表")
            tasks.extend(payload)
            total_pages = int(headers.get("x-pagination-total-pages", page) or page)
            if page >= total_pages or not payload:
                break
            page += 1
        return tasks
