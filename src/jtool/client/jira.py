from typing import Optional, Any
import asyncio
from enum import Enum

import pydantic

from .base import BaseClient, User, APIError, APIHTTPError, logger, handle_api_errors


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


class JiraAPIError(APIError):
    """Exception raised for Jira API errors."""

    ...


def jira_errors(exc: Exception) -> Exception:
    if isinstance(exc, APIHTTPError):
        if isinstance(exc.response_json, dict):
            if error_messages := exc.response_json.get("errorMessages"):
                if isinstance(error_messages, list) and error_messages:
                    return JiraAPIError("; ".join(error_messages))

            if isinstance(errors := exc.response_json.get("errors"), list) and errors:
                error_messages = [
                    err.get("message")
                    for err in errors
                    if isinstance(err, dict)  # fmt: keep
                ]
                return JiraAPIError("; ".join(error_messages))
    return exc


handle_jira_errors = handle_api_errors(jira_errors)


class JiraClient(BaseClient):
    """Client for interacting with the Jira Cloud API."""

    @handle_jira_errors
    async def get_self(self) -> User:
        """Get information about the authenticated user."""
        async with self._rate_limit():
            resp = await self.request("GET", "/rest/api/3/myself")
        return User.model_validate(resp)

    @handle_jira_errors
    async def resolve_user(self, identifier: str) -> User:
        """Resolve a user identifier (email or accountId) to a Jira Cloud accountId."""
        async with self._rate_limit():
            resp: dict[str, Any] | list[Any] = await self.request(
                "GET", "/rest/api/3/user/search", params={"query": identifier}
            )
        assert isinstance(resp, list)
        if len(resp) == 1:
            u = User.model_validate(resp[0])
            return u
        elif len(resp) > 1:
            for u in resp:
                u = User.model_validate(u)
                if u.accountId == identifier or u.emailAddress == identifier:
                    return u
        raise APIError(f"No exact match found for '{identifier}'")

    @handle_jira_errors
    async def get_filters_for_user(self, user: User) -> list[str]:
        """Get the IDs of filters owned by the given user."""
        async with self._rate_limit():
            resp = await self.request(
                "GET",
                "/rest/api/3/filter/search",
                params={
                    "accountId": user.accountId,
                    "overrideSharePermissions": True,
                },
            )
        assert isinstance(resp, dict)
        filters: list[str] = []
        while True:
            filters.extend([f["id"] for f in resp.get("values", [])])
            if next_page := resp.get("nextPage"):
                async with self._rate_limit():
                    resp = await self.request("GET", next_page)
                continue
            return filters

    @handle_jira_errors
    async def set_filter_owner(self, filter_id: str, new_owner_account_id: str) -> None:
        """Set the owner of a filter to a new user."""
        async with self._rate_limit():
            await self.request(
                "PUT",
                f"/rest/api/3/filter/{filter_id}/owner",
                json={"accountId": new_owner_account_id},
            )

    @handle_api_errors()
    async def search_issue_keys_for_user_field(
        self, field_name: str, user: User, project_key: Optional[str] = None
    ) -> list[str]:
        """Search for issue keys where the given user is set in the specified user field."""
        jql_parts: list[str] = [f"{field_name} = {user.accountId}"]
        if project_key:
            jql_parts.append(f"project = {project_key}")
        jql = " AND ".join(jql_parts)
        next_page_token: str = ""
        keys: list[str] = []
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
            assert isinstance(resp, dict)
            issues = resp.get("issues", [])
            assert isinstance(issues, list)
            keys.extend(
                key
                for issue in issues
                if isinstance(issue, dict)
                if isinstance(key := issue.get("key"), str)
            )
            if next_page_token := str(resp.get("nextPageToken", "")):
                continue
            return keys

    @handle_jira_errors
    async def bulk_update_user_field(
        self, issue_keys: list[str], field_name: str, new_account_id: str
    ) -> list[str]:
        """Use Jira Cloud bulk update endpoint to set user field in batches.
        Returns list of task IDs for tracking progress.
        """
        batch_size = 50

        @handle_jira_errors
        async def send_batch(
            chunk: list[str], batch_index: int
        ) -> dict[str, Any] | Any:
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
        results = []
        for resp in responses:
            if isinstance(resp, APIError):
                logger.error(str(resp))
            elif isinstance(resp, Exception):
                logger.error(f"Unexpected error: {resp}")
            elif isinstance(resp, dict):
                results.append(resp.get("taskId", ""))
        return results

    @handle_jira_errors
    async def get_task_status(
        self,
        task: Task | str,
        batch_index: int = 0,
    ) -> Task:
        """Get the status of a bulk operation task"""
        if isinstance(task, Task):
            task_id = task.taskId
            curr_task = task
        elif isinstance(task, str):
            task_id = task
            curr_task = None
        else:
            raise ValueError()

        async with self._rate_limit(stagger_order=batch_index):
            resp = await self.request("GET", f"/rest/api/3/bulk/queue/{task_id}")
        new_task = Task.model_validate(resp)

        if curr_task is not None:
            curr_task.status = new_task.status
            curr_task.progressPercent = new_task.progressPercent
            curr_task.totalIssueCount = new_task.totalIssueCount
            curr_task.processedAccessibleIssues = new_task.processedAccessibleIssues
            return curr_task
        return new_task
