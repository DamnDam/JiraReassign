from typing import Optional, List, Any, overload
import asyncio
from enum import Enum

import pydantic

from .base import BaseClient, User


class TaskStatus(str, Enum):
    ENQUEUED = "ENQUEUED"
    RUNNING = "RUNNING"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCELLED = "CANCELLED"
    DEAD = "DEAD"


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


class JiraClient(BaseClient):
    async def get_self(self) -> User:
        """Get information about the authenticated user."""
        async with self._rate_limit():
            resp = await self.request("GET", "/rest/api/3/myself")
        return User(**resp)

    async def resolve_user(self, identifier: str) -> Optional[User]:
        """Resolve a user identifier (email or accountId) to a Jira Cloud accountId."""
        async with self._rate_limit():
            resp = await self.request(
                "GET", "/rest/api/3/user/search", params={"query": identifier}
            )
        users = resp
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
            resp = await self.request(
                "GET",
                "/rest/api/2/filter/search",
                params={
                    "accountId": user.accountId,
                    "overrideSharePermissions": True,
                },
            )
        filters: List[str] = []
        while True:
            filters.extend([f["id"] for f in resp.get("values", [])])
            if next_page := resp.get("nextPage"):
                async with self._rate_limit():
                    resp = await self.request("GET", next_page)
                continue
            return filters

    async def set_filter_owner(self, filter_id: str, new_owner_account_id: str) -> None:
        """Set the owner of a filter to a new user."""
        async with self._rate_limit():
            await self.request(
                "PUT",
                f"/rest/api/3/filter/{filter_id}/owner",
                json={"accountId": new_owner_account_id},
            )

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
                resp = await self.request(
                    "GET",
                    "/rest/api/3/search/jql",
                    params={
                        "jql": jql,
                        "maxResults": 100,
                        "fields": "key",
                        "nextPageToken": next_page_token,
                    },
                )
            issues = resp.get("issues", [])
            keys.extend([i["key"] for i in issues])
            if next_page_token := resp.get("nextPageToken"):
                continue
            return keys

    async def bulk_update_user_field(
        self, issue_keys: List[str], field_name: str, new_account_id: str
    ) -> list[str]:
        """Use Jira Cloud bulk update endpoint to set user field in batches.
        Returns list of task IDs for tracking progress.
        """
        batch_size = 50

        async def send_batch(chunk: List[str], batch_index: int) -> dict[str, Any]:
            try:
                async with self._rate_limit(stagger_order=batch_index, delay=0.5):
                    return await self.request(
                        "POST",
                        "/rest/api/3/bulk/issues/fields",
                        json={
                            "selectedActions": [field_name],
                            "selectedIssueIdsOrKeys": chunk,
                            "editedFieldsInput": {
                                "singleSelectClearableUserPickerFields": [
                                    {
                                        "fieldId": field_name,
                                        "user": {"accountId": new_account_id},
                                    }
                                ]
                            },
                        },
                    )
            except Exception as exc:
                self._log_error(str(exc))
                raise

        responses = await asyncio.gather(
            *(
                send_batch(chunk, idx)
                for idx, chunk in enumerate(
                    issue_keys[i : i + batch_size]
                    for i in range(0, len(issue_keys), batch_size)
                )
            )
        )
        return [resp.get("taskId", "") for resp in responses if isinstance(resp, dict)]

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
            resp = await self.request("GET", f"/rest/api/3/bulk/queue/{task_id}")
        new_task = Task(**resp)

        if task is not None:
            task.status = new_task.status
            task.progressPercent = new_task.progressPercent
            task.totalIssueCount = new_task.totalIssueCount
            task.processedAccessibleIssues = new_task.processedAccessibleIssues
            return task
        return new_task
