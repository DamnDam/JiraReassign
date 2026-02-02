from typing import Optional, List, Any, overload
import asyncio
from contextlib import asynccontextmanager
from enum import Enum

import pydantic
import httpx

from .term import DualConsole


class TaskStatus(str, Enum):
    ENQUEUED = "ENQUEUED"
    RUNNING = "RUNNING"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCELLED = "CANCELLED"
    DEAD = "DEAD"


class User(pydantic.BaseModel):
    accountId: str
    emailAddress: Optional[str]
    displayName: Optional[str]


class Task(pydantic.BaseModel):
    taskId: str
    status: TaskStatus
    progressPercent: int
    totalIssueCount: int = 0
    processedAccessibleIssues: list[Any] = []

    @pydantic.computed_field
    def is_finished(self) -> bool:
        return self.status in {
            TaskStatus.COMPLETE,
            TaskStatus.FAILED,
            TaskStatus.CANCEL_REQUESTED,
            TaskStatus.CANCELLED,
            TaskStatus.DEAD,
        }


class JiraApiError(Exception):
    def __init__(
        self,
        status_code: int,
        reason: str,
        method: str,
        url: str,
        request_headers: dict,
        request_body: Optional[str],
        response_headers: dict,
        response_json: Optional[Any],
        response_text: Optional[str],
    ) -> None:
        self.status_code = status_code
        self.reason = reason
        self.method = method
        self.url = url
        self.request_headers = request_headers
        self.request_body = request_body
        self.response_headers = response_headers
        self.response_json = response_json
        self.response_text = response_text

    def __str__(self) -> str:
        base = f"HTTP {self.status_code} {self.reason} for {self.method} {self.url}\nReturned: {self.response_json or self.response_text}"
        return base


class JiraClient:
    def __init__(
        self,
        console: DualConsole,
        base_url: str,
        concurrency: int,
        auth: tuple[str, str],
    ) -> None:
        self._console = console
        self._semaphore = asyncio.Semaphore(concurrency)
        self._client = httpx.AsyncClient(
            base_url=base_url,
            auth=auth,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            timeout=30.0,
        )

    async def close(self):
        await self._client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.close()

    @property
    def _log_error(self):
        return self._console.log_error

    def _mask_headers(self, headers: Any) -> dict:
        try:
            items = dict(headers)
        except Exception:
            items = {}
        masked = {}
        for k, v in items.items():
            lk = k.lower()
            if lk in ("authorization", "cookie"):
                masked[k] = "[secure]"
            else:
                masked[k] = v
        return masked

    def _build_error(self, exc: httpx.HTTPStatusError) -> JiraApiError:
        req = exc.request
        resp = exc.response
        # request body
        if req.content:
            try:
                req_body = (
                    req.content.decode("utf-8", "replace")
                    if isinstance(req.content, (bytes, bytearray))
                    else str(req.content)
                )
            except Exception:
                req_body = None
        else:
            req_body = None
        # response body
        resp_json: Optional[Any] = None
        resp_text: Optional[str] = None
        ct = resp.headers.get("content-type", "")
        if ct.startswith("application/json"):
            try:
                resp_json = resp.json()
            except Exception:
                try:
                    resp_text = resp.text
                except Exception:
                    resp_text = None
        else:
            try:
                resp_text = resp.text
            except Exception:
                resp_text = None

        return JiraApiError(
            status_code=resp.status_code,
            reason=resp.reason_phrase or "",
            method=req.method,
            url=str(req.url),
            request_headers=self._mask_headers(req.headers),
            request_body=req_body,
            response_headers=dict(resp.headers),
            response_json=resp_json,
            response_text=resp_text,
        )

    @asynccontextmanager
    async def _rate_limit(self, stagger_order: int = 0, delay: float = 0.1):
        """Context manager to stagger API requests to avoid rate limiting.
        If stagger_order > 0, waits for (stagger_order * delay) seconds before acquiring the semaphore.
        """
        await asyncio.sleep(stagger_order * delay)
        await self._semaphore.acquire()
        try:
            yield
        finally:
            self._semaphore.release()

    async def get_self(self) -> User:
        """Get information about the authenticated user."""
        async with self._rate_limit():
            resp = await self._client.get("/rest/api/3/myself")
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise self._build_error(exc) from exc
        data = resp.json()
        return User(**data)

    async def resolve_user(self, identifier: str) -> Optional[User]:
        """Resolve a user identifier (email or accountId) to a Jira Cloud accountId."""
        async with self._rate_limit():
            resp = await self._client.get(
                "/rest/api/3/user/search", params={"query": identifier}
            )
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise self._build_error(exc) from exc
        users = resp.json()
        if isinstance(users, list):
            if len(users) == 1:
                u = User(**users[0])
                return u
            elif len(users) > 1:
                for u in users:
                    u = User(**u)
                    if u.accountId == identifier or u.emailAddress == identifier:
                        return u
                    else:
                        self._log_error(
                            f"Multiple users found for '{identifier}'; no exact match."
                        )

        return None

    async def get_filters_for_user(self, user: User) -> List[str]:
        """Get the IDs of filters owned by the given user."""
        async with self._rate_limit():
            resp = await self._client.get(
                "/rest/api/2/filter/search",
                params={
                    "accountId": user.accountId,
                    "overrideSharePermissions": True,
                },
            )
        filters: List[str] = []
        while True:
            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise self._build_error(exc) from exc
            data = resp.json()
            filters.extend([f["id"] for f in data.get("values", [])])
            if next_page := data.get("nextPage"):
                async with self._rate_limit():
                    resp = await self._client.get(next_page)
                continue
            return filters

    async def set_filter_owner(self, filter_id: str, new_owner_account_id: str) -> None:
        """Set the owner of a filter to a new user."""
        async with self._rate_limit():
            resp = await self._client.put(
                f"/rest/api/3/filter/{filter_id}/owner",
                json={"accountId": new_owner_account_id},
            )
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise self._build_error(exc) from exc

    async def search_issue_keys_for_user_field(
        self, field_name: str, user: User, project_key: Optional[str] = None
    ) -> List[str]:
        """Search for issue keys where the given user is set in the specified user field."""
        jql_parts = [f"{field_name} = {user.accountId}"]
        if project_key:
            jql_parts.append(f"project = {project_key}")
        jql = " AND ".join(jql_parts)
        next_page_token: str = ""
        keys: List[str] = []
        while True:
            async with self._rate_limit():
                resp = await self._client.get(
                    "/rest/api/3/search/jql",
                    params={
                        "jql": jql,
                        "maxResults": 100,
                        "fields": "key",
                        "nextPageToken": next_page_token,
                    },
                )
            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise self._build_error(exc) from exc
            data = resp.json()
            issues = data.get("issues", [])
            keys.extend([i["key"] for i in issues])
            if next_page_token := data.get("nextPageToken"):
                continue
            break
        return keys

    async def bulk_update_user_field(
        self, issue_keys: List[str], field_name: str, new_account_id: str
    ) -> list[str]:
        """Use Jira Cloud bulk update endpoint to set user field in batches.
        Returns list of task IDs for tracking progress.
        """
        batch_size = 50
        tasks = []

        async def send_batch(chunk: List[str], batch_index: int) -> httpx.Response:
            payload = {
                "selectedActions": [field_name],
                "selectedIssueIdsOrKeys": chunk,
                "editedFieldsInput": {
                    "singleSelectClearableUserPickerFields": [
                        {"fieldId": field_name, "user": {"accountId": new_account_id}}
                    ]
                },
            }
            async with self._rate_limit(stagger_order=batch_index, delay=0.5):
                res = await self._client.post(
                    "/rest/api/3/bulk/issues/fields", json=payload
                )
            return res

        responses = await asyncio.gather(
            *(
                send_batch(chunk, idx)
                for idx, chunk in enumerate(
                    issue_keys[i : i + batch_size]
                    for i in range(0, len(issue_keys), batch_size)
                )
            ),
            return_exceptions=True,
        )
        for resp in responses:
            if isinstance(resp, BaseException):
                self._log_error(str(resp))
                continue
            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                jira_exc = self._build_error(exc)
                self._log_error(str(jira_exc))
                continue
            data = (
                resp.json()
                if resp.headers.get("content-type", "").startswith("application/json")
                else {}
            )
            if taskId := data.get("taskId"):
                tasks.append(taskId)
                continue
        return tasks

    @overload
    async def get_task_status(self, *, task_id: str, batch_index: int = 0) -> Task: ...
    @overload
    async def get_task_status(
        self, *, task: Optional[Task], batch_index: int = 0
    ) -> Task: ...

    async def get_task_status(
        self,
        *,
        task_id: Optional[str] = None,
        task: Optional[Task] = None,
        batch_index: int = 0,
    ) -> Task:
        """Get the status of a bulk operation task"""
        if task is not None:
            task_id = task.taskId
        async with self._rate_limit(stagger_order=batch_index):
            resp = await self._client.get(f"/rest/api/3/bulk/queue/{task_id}")
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise self._build_error(exc) from exc
        data = resp.json()
        new_task = Task(**data)
        if task is not None:
            task.status = new_task.status
            task.progressPercent = new_task.progressPercent
            task.totalIssueCount = new_task.totalIssueCount
            task.processedAccessibleIssues = new_task.processedAccessibleIssues
            return task
        return new_task
