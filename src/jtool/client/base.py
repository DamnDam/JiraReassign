from typing import Optional, Any, Self
import asyncio
from contextlib import asynccontextmanager

import pydantic
import httpx

from ..term import DualConsole


class User(pydantic.BaseModel):
    accountId: str
    emailAddress: Optional[str]
    displayName: Optional[str]


class AtlassianApiError(Exception):
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


class BaseClient:
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

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
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

    def _build_error(self, exc: Exception) -> Exception:
        if not isinstance(exc, httpx.HTTPStatusError):
            return exc

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
        if any(
            ct.startswith("application/json")
            for ct in resp.headers.get("content-type", "").split(",")
        ):
            try:
                resp_json = resp.json()
            except Exception:
                try:
                    resp_text = resp.text
                except Exception:
                    resp_text = None
            else:
                resp_text = resp.text
        else:
            try:
                resp_text = resp.text
            except Exception:
                resp_text = None

        return AtlassianApiError(
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

    async def request(
        self,
        method: str,
        url: str,
        *,
        params: Optional[dict[str, Any]] = None,
        json: Optional[Any] = None,
    ) -> dict[str, Any]:
        """Make a generic API request."""
        resp = await self._client.request(method, url, params=params, json=json)
        try:
            resp.raise_for_status()
        except Exception as exc:
            raise self._build_error(exc) from exc

        return (
            resp_json
            if any(
                ct.startswith("application/json")
                for ct in resp.headers.get("content-type", "").split(",")
            )
            and isinstance(resp_json := resp.json(), dict)
            else {}
        )
