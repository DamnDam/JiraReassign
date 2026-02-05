from typing import Optional

import pydantic

from .base import BaseClient, APIError, APIHTTPError, logger, handle_api_errors


class SpacePermOperationV1(pydantic.BaseModel):
    key: str
    target: str


class SpacePermOperationV2(pydantic.BaseModel):
    key: str
    targetType: str


class SpacePermSubjectV1(pydantic.BaseModel):
    type: str
    identifier: str


class SpacePermSubjectV2(pydantic.BaseModel):
    type: str
    id: str


class SpacePermissionV1(pydantic.BaseModel):
    id: Optional[str] = pydantic.Field(default=None, exclude_if=lambda v: v is None)
    subject: SpacePermSubjectV1
    operation: SpacePermOperationV1

    @classmethod
    def _from_v2(cls, perm_v2: "SpacePermissionV2") -> "SpacePermissionV1":
        subject = SpacePermSubjectV1(
            type=perm_v2.principal.type,
            identifier=perm_v2.principal.id,
        )
        operation = SpacePermOperationV1(
            key=perm_v2.operation.key,
            target=perm_v2.operation.targetType,
        )
        return cls(
            id=perm_v2.id,
            subject=subject,
            operation=operation,
        )


class SpacePermissionV2(pydantic.BaseModel):
    id: Optional[str] = pydantic.Field(default=None, exclude_if=lambda v: v is None)
    principal: SpacePermSubjectV2
    operation: SpacePermOperationV2


class Space(pydantic.BaseModel):
    id: str
    key: str
    name: str
    type: str
    permissions: Optional[list[SpacePermissionV1]] = None


class ConfluenceAPIError(APIError):
    """Exception raised for Confluence API errors."""

    ...


def confluence_errors(exc: Exception) -> Exception:
    if isinstance(exc, APIHTTPError):
        if isinstance(exc.response_json, dict):
            if isinstance(data := exc.response_json.get("data"), dict):
                if isinstance(errors := data.get("errors"), list) and errors:
                    err_msg = "; ".join(
                        msg.get("translation", "-")
                        for err in errors
                        if isinstance(err, dict)
                        if isinstance(msg := err.get("message", {}), dict)
                    )
                    return ConfluenceAPIError(err_msg)
            if isinstance(errors := exc.response_json.get("errors"), list) and errors:
                err_msg = "; ".join(
                    f"{err.get('title', '-')} - {err.get('detail', None)}"
                    for err in errors
                    if isinstance(err, dict)
                )
                return ConfluenceAPIError(err_msg)
    return exc


handle_confluence_errors = handle_api_errors(confluence_errors)


class ConfluenceClient(BaseClient):
    """Client for interacting with the Confluence API."""

    @handle_confluence_errors
    async def acquire_admin(self) -> None:
        """Acquire admin access for the current session."""
        async with self._rate_limit():
            await self.request("POST", "/wiki/api/v2/admin-key")

    @handle_confluence_errors
    async def list_spaces(self) -> list[Space]:
        """List all Confluence spaces."""
        async with self._rate_limit():
            resp = await self.request(
                "GET",
                "/wiki/api/v2/spaces",
                params={"limit": 100},
            )

        results = []
        while True:
            assert isinstance(resp, dict)
            results.extend(resp.get("results", []))
            links = resp.get("_links", {})
            assert isinstance(links, dict)
            next_page = links.get("next", "")
            if not next_page:
                break
            async with self._rate_limit():
                resp = await self.request("GET", next_page)

        return [Space.model_validate(space) for space in results]

    @handle_confluence_errors
    async def list_space_permissions(self, space: Space) -> list[SpacePermissionV1]:
        """List permissions for a specific Confluence space."""
        async with self._rate_limit():
            resp = await self.request(
                "GET",
                f"/wiki/api/v2/spaces/{space.id}/permissions",
                params={"limit": 100},
            )

        results = []
        while True:
            assert isinstance(resp, dict)
            results.extend(resp.get("results", []))
            links = resp.get("_links", {})
            assert isinstance(links, dict)
            next_page = links.get("next", "")
            if not next_page:
                break
            async with self._rate_limit():
                resp = await self.request("GET", next_page)

        return [
            SpacePermissionV1._from_v2(SpacePermissionV2.model_validate(perm))
            for perm in results
        ]

    @handle_confluence_errors
    async def add_space_permission(
        self, space: Space, permission: SpacePermissionV1
    ) -> None:
        """Add a user permission to a Confluence space."""
        try:
            async with self._rate_limit():
                await self.request(
                    "POST",
                    f"/wiki/rest/api/space/{space.key}/permission",
                    json=permission.model_dump(),
                )
        except APIHTTPError as e:
            # Ignore conflict errors (permission already exists)
            if e.status_code == 409 or (
                e.status_code == 400
                and "Permission already exists." in (e.response_text or "")
            ):
                logger.warning(
                    f"Permission {str(permission.operation)} for {str(permission.subject)} already exists in space '{space.key}', skipping."
                )
                return
            raise

    @handle_confluence_errors
    async def remove_space_permission(
        self, space: Space, permission: SpacePermissionV1
    ) -> None:
        """Remove a user permission from a Confluence space."""
        async with self._rate_limit():
            await self.request(
                "DELETE", f"/wiki/rest/api/space/{space.key}/permission/{permission.id}"
            )

    @handle_confluence_errors
    async def rename_space(self, space: Space, new_name: str) -> None:
        """Rename a Confluence space."""
        async with self._rate_limit():
            await self.request(
                "PUT",
                f"/wiki/rest/api/space/{space.key}",
                json={"type": space.type, "name": new_name},
            )
