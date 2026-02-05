from typing import Optional, Any, Self, Callable, TypeVar, ParamSpec, Awaitable
import logging
import asyncio
from contextlib import asynccontextmanager
from functools import wraps

import pydantic
import httpx

logger = logging.getLogger("jtool.client")


class User(pydantic.BaseModel):
    accountId: str
    emailAddress: Optional[str]
    displayName: Optional[str]


class APIError(Exception):
    """Generic API error."""

    pass


class APIHTTPError(APIError):
    """Exception raised for API errors with detailed request and response information."""

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


P = ParamSpec("P")
T = TypeVar("T")
Func = TypeVar("Func", bound=Callable[P, Awaitable[T]])


def handle_api_errors(
    extra_handler: Optional[Callable[[Exception], Exception]] = None,
) -> Callable[[Func], Func]:
    """Decorator to wrap API calls and raise APIError."""

    def _mask_headers(headers: Any) -> dict[str, Any]:
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

    def _decorator(
        func: Func,
    ) -> Callable[..., Awaitable[T]]:
        @wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            try:
                return await func(*args, **kwargs)
            except httpx.HTTPStatusError as exc:
                req = exc.request
                resp = exc.response
                assert isinstance(resp, httpx.Response)
                new_exc = APIHTTPError(
                    status_code=resp.status_code,
                    reason=resp.reason_phrase or "",
                    method=req.method,
                    url=str(req.url),
                    request_headers=_mask_headers(req.headers),
                    request_body=(
                        req.content.decode("utf-8", "replace") if req.content else None
                    ),
                    response_headers=_mask_headers(resp.headers),
                    response_json=(
                        resp.json()
                        if any(
                            ct.find("application/json") != -1
                            for ct in resp.headers.get("content-type", "").split(",")
                        )
                        else None
                    ),
                    response_text=resp.text,
                )
            except Exception as exc:
                new_exc = exc

            if extra_handler:
                raise extra_handler(new_exc)
            raise new_exc

        return wrapper

    return _decorator


class BaseClient:
    """Base client for making API requests with rate limiting and error handling."""

    _semaphore: asyncio.Semaphore
    _client: httpx.AsyncClient

    def __init__(
        self,
        base_url: str,
        concurrency: int,
        auth: tuple[str, str],
    ) -> None:
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
    ) -> dict[str, Any] | list[Any]:
        """Make a generic API request."""
        resp = await self._client.request(method, url, params=params, json=json)
        resp.raise_for_status()

        return (
            resp_json
            if any(
                ct.find("application/json") != -1
                for ct in resp.headers.get("content-type", "").split(",")
            )
            and isinstance(resp_json := resp.json(), (dict, list))
            else {}
        )
