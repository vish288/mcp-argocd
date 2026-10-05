"""Argo CD API client using httpx."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from .config import ArgoCDConfig
from .exceptions import (
    ArgoCDApiError,
    ArgoCDAuthError,
    ArgoCDConflictError,
    ArgoCDNotFoundError,
    ArgoCDTimeoutError,
)

_HTML_HINT = (
    "Unexpected HTML response — ARGOCD_URL may point at a login page or the wrong path prefix"
)


def _looks_like_html(resp: httpx.Response) -> bool:
    if "text/html" in resp.headers.get("content-type", ""):
        return True
    return resp.text.lstrip().startswith("<")


def _extract_error(resp: httpx.Response) -> tuple[str, int | None]:
    """Pull (message, grpc_code) from a grpc-gateway ``runtimeError`` body.

    Falls back to ``error`` then the raw text (<= 500 chars). HTML bodies get the
    path-prefix hint rather than a dump of the login page.
    """
    if _looks_like_html(resp):
        return _HTML_HINT, None
    try:
        data = resp.json()
    except (json.JSONDecodeError, ValueError):
        return (resp.text[:500] or resp.reason_phrase), None
    if isinstance(data, dict):
        message = data.get("message") or data.get("error") or resp.text[:500]
        code = data.get("code")
        return message, (code if isinstance(code, int) else None)
    return resp.text[:500], None


class ArgoCDClient:
    """Async HTTP client for the Argo CD REST API under ``/api/v1``.

    The base URL keeps any path prefix in ``ARGOCD_URL`` (e.g. a reverse proxy at
    ``https://example.com/cd-core``), so requests hit ``/cd-core/api/v1/...``.
    ``/api/version`` is the one endpoint outside ``/api/v1``; it is requested with
    an absolute URL that also preserves the prefix.
    """

    def __init__(self, config: ArgoCDConfig | None = None) -> None:
        self.config = config or ArgoCDConfig.from_env()
        self.config.validate()
        self._version_url = f"{self.config.url}/api/version"
        self._client = httpx.AsyncClient(
            base_url=self.config.api_url,
            headers={
                "Authorization": f"Bearer {self.config.token}",
                "Content-Type": "application/json",
            },
            timeout=self.config.timeout,
            verify=self.config.ssl_verify,
        )

    async def close(self) -> None:
        await self._client.aclose()

    # ── core request path ─────────────────────────────────────────

    @staticmethod
    def _raise_for_status(resp: httpx.Response) -> None:
        if resp.is_success:
            return
        message, grpc_code = _extract_error(resp)
        body = resp.text[:500]
        sc = resp.status_code
        if sc in (401, 403):
            raise ArgoCDAuthError(sc, message, grpc_code, body)
        if sc == 404:
            raise ArgoCDNotFoundError(sc, message, grpc_code, body)
        if sc in (400, 409) and grpc_code in (9, 10):
            raise ArgoCDConflictError(sc, message, grpc_code, body)
        raise ArgoCDApiError(sc, message, grpc_code, body)

    @staticmethod
    def _parse_body(resp: httpx.Response, *, raw: bool = False) -> Any:
        if resp.status_code == 204 or not resp.content:
            return None
        if _looks_like_html(resp):
            raise ArgoCDApiError(resp.status_code, _HTML_HINT, None, resp.text[:500])
        if raw:
            return resp.text
        try:
            return resp.json()
        except json.JSONDecodeError as e:
            raise ArgoCDApiError(
                resp.status_code, f"JSON parse error: {e}", None, resp.text[:500]
            ) from e

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_data: Any = None,
        params: dict[str, Any] | None = None,
        raw: bool = False,
    ) -> Any:
        kwargs: dict[str, Any] = {"params": params}
        if json_data is not None:
            kwargs["json"] = json_data
        try:
            resp = await self._client.request(method, path, **kwargs)
        except httpx.TimeoutException as e:
            msg = f"Request timed out: {e}"
            raise ArgoCDTimeoutError(msg) from e
        self._raise_for_status(resp)
        return self._parse_body(resp, raw=raw)

    async def get(
        self, path: str, params: dict[str, Any] | None = None, *, raw: bool = False
    ) -> Any:
        return await self._request("GET", path, params=params, raw=raw)

    async def post(
        self, path: str, json_data: Any = None, params: dict[str, Any] | None = None
    ) -> Any:
        return await self._request("POST", path, json_data=json_data, params=params)

    async def patch(
        self, path: str, json_data: Any = None, params: dict[str, Any] | None = None
    ) -> Any:
        return await self._request("PATCH", path, json_data=json_data, params=params)

    async def delete(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return await self._request("DELETE", path, params=params)

    async def get_version(self) -> Any:
        """GET /api/version — the one endpoint outside /api/v1."""
        try:
            resp = await self._client.get(self._version_url)
        except httpx.TimeoutException as e:
            msg = f"Request timed out: {e}"
            raise ArgoCDTimeoutError(msg) from e
        self._raise_for_status(resp)
        return self._parse_body(resp)

    async def stream_lines(
        self, path: str, params: dict[str, Any] | None = None
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield parsed objects from an NDJSON stream (the log endpoint).

        Each Argo CD log line is ``{"result": {...}}``; the caller stops when a
        line carries ``last: true`` by breaking out of the iteration, which
        closes the stream.
        """
        try:
            async with self._client.stream("GET", path, params=params or {}) as resp:
                if not resp.is_success:
                    await resp.aread()
                    self._raise_for_status(resp)
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    yield json.loads(line)
        except httpx.TimeoutException as e:
            msg = f"Log stream timed out: {e}"
            raise ArgoCDTimeoutError(msg) from e
