from typing import Optional, List

import pydantic

from .base import BaseClient, AtlassianApiError


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
    permissions: Optional[List[SpacePermissionV1]] = None


class ConfluenceClient(BaseClient):
    async def acquire_admin(self) -> None:
        """Acquire admin access for the current session."""
        async with self._rate_limit():
            await self.request("POST", "/wiki/api/v2/admin-key")

    async def list_spaces(self) -> List[Space]:
        """List all Confluence spaces."""
        async with self._rate_limit():
            resp = await self.request(
                "GET",
                "/wiki/api/v2/spaces",
                params={"limit": 100},
            )

        results = resp.get("results", [])
        links = resp.get("_links", {})

        while "next" in links:
            async with self._rate_limit():
                resp = await self.request("GET", links["next"])

            results.extend(resp.get("results", []))
            links = resp.get("_links", {})

        return [Space(**space) for space in results]

    async def list_space_permissions(self, space: Space) -> List[SpacePermissionV1]:
        """List permissions for a specific Confluence space."""
        async with self._rate_limit():
            resp = await self.request(
                "GET",
                f"/wiki/api/v2/spaces/{space.id}/permissions",
                params={"limit": 100},
            )

        results = resp.get("results", [])
        links = resp.get("_links", {})

        while "next" in links:
            async with self._rate_limit():
                resp = await self.request("GET", links["next"])

            results.extend(resp.get("results", []))
            links = resp.get("_links", {})

        return [
            SpacePermissionV1._from_v2(SpacePermissionV2(**perm)) for perm in results
        ]

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
        except AtlassianApiError as e:
            # Ignore conflict errors (permission already exists)
            if e.status_code == 409 or (
                e.status_code == 400
                and "Permission already exists." in (e.response_text or "")
            ):
                self._log_error(
                    f"Permission {str(permission.operation)} for {str(permission.subject)} already exists in space '{space.key}', skipping."
                )
                return

    async def remove_space_permission(
        self, space: Space, permission: SpacePermissionV1
    ) -> None:
        """Remove a user permission from a Confluence space."""
        async with self._rate_limit():
            await self.request(
                "DELETE", f"/wiki/rest/api/space/{space.key}/permission/{permission.id}"
            )

    async def rename_space(self, space: Space, new_name: str) -> None:
        """Rename a Confluence space."""
        async with self._rate_limit():
            await self.request(
                "PUT",
                f"/wiki/rest/api/space/{space.key}",
                json={"type": space.type, "name": new_name},
            )
