"""Argo CD API client using httpx."""

from __future__ import annotations

import asyncio
import json
import math
import random
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from .config import ArgoCDConfig
from .exceptions import (
    ArgoCDApiError,
    ArgoCDAuthError,
    ArgoCDConflictError,
    ArgoCDConnectionError,
    ArgoCDNotFoundError,
    ArgoCDRateLimitError,
    ArgoCDTimeoutError,
)

# Transport retry policy (R2). Idempotent GETs are retried on a transient
# upstream status; writes are never retried. httpx's AsyncHTTPTransport(retries=)
# already covers connection-level failures, so this layer only adds status
# retries. "Max 2 attempts" = the initial request plus at most one retry.
_RETRY_STATUSES = frozenset({502, 503, 504})
_MAX_ATTEMPTS = 2
_MAX_RETRY_AFTER = 5.0  # only honour a 429 Retry-After at most this long (seconds)
_jitter = random.SystemRandom()  # jitter source; SystemRandom sidesteps the S311 lint


def _retry_after_seconds(resp: httpx.Response) -> float | None:
    """Parse a ``Retry-After`` header to seconds-from-now, or None when absent.

    Both forms are handled: delta-seconds, and an HTTP-date (RFC 7231), which is
    converted to the delay from now. The result is clamped to >= 0 (a past date
    means "retry now") and non-finite values are rejected."""
    raw = resp.headers.get("Retry-After")
    if raw is None:
        return None
    raw = raw.strip()
    try:
        seconds = float(raw)
    except ValueError:
        try:
            when = parsedate_to_datetime(raw)
        except (TypeError, ValueError):
            return None
        if when is None:
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        seconds = (when - datetime.now(timezone.utc)).total_seconds()
    if not math.isfinite(seconds):
        return None
    return max(0.0, seconds)


def _retry_delay(resp: httpx.Response) -> float | None:
    """Seconds to wait before retrying this response, or None to not retry.

    502/503/504 get a jittered backoff; a 429 is retried only when it names a
    ``Retry-After`` of at most ``_MAX_RETRY_AFTER`` seconds."""
    if resp.status_code in _RETRY_STATUSES:
        return _jitter.uniform(0.5, 1.5)
    if resp.status_code == 429:
        after = _retry_after_seconds(resp)
        if after is not None and after <= _MAX_RETRY_AFTER:
            return after
    return None


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
            timeout=httpx.Timeout(self.config.timeout, connect=min(10, self.config.timeout)),
            verify=self.config.ssl_verify,
            # verify= on the transport too: httpx 0.28 ignores the client-level
            # verify once a transport is supplied, but the client-level one is
            # still used for proxy mounts, so both carry the same value.
            transport=httpx.AsyncHTTPTransport(retries=2, verify=self.config.ssl_verify),
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
        if sc == 429:
            raise ArgoCDRateLimitError(
                sc, message, grpc_code, body, retry_after=_retry_after_seconds(resp)
            )
        raise ArgoCDApiError(sc, message, grpc_code, body)

    @staticmethod
    def _parse_body(resp: httpx.Response) -> Any:
        if resp.status_code == 204 or not resp.content:
            return None
        if _looks_like_html(resp):
            raise ArgoCDApiError(resp.status_code, _HTML_HINT, None, resp.text[:500])
        try:
            return resp.json()
        except json.JSONDecodeError as e:
            raise ArgoCDApiError(
                resp.status_code, f"JSON parse error: {e}", None, resp.text[:500]
            ) from e

    def _unreachable(self, e: httpx.TransportError) -> ArgoCDConnectionError:
        # Strip any userinfo so a credential embedded in the URL never lands in an error.
        safe = httpx.URL(self.config.url).copy_with(username=None, password=None)
        msg = f"Cannot reach Argo CD at {safe}: {e}"
        return ArgoCDConnectionError(msg)

    async def _send(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """One send path with the R2 retry policy. Idempotent GETs are retried on
        a transient upstream status (502/503/504, or a 429 naming a short
        ``Retry-After``) with jitter; writes are never retried. Connection-level
        failures are retried beneath this by ``AsyncHTTPTransport(retries=2)``."""
        resp: httpx.Response
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                resp = await self._client.request(method, path, **kwargs)
            except httpx.ConnectTimeout as e:
                # A connect timeout means the host was never reached — treat it
                # as unreachable, not as a slow request (it is a TimeoutException
                # subclass, so it must be caught before the timeout branch).
                raise self._unreachable(e) from e
            except httpx.TimeoutException as e:
                msg = f"Request timed out: {e}"
                raise ArgoCDTimeoutError(msg) from e
            except httpx.TransportError as e:
                raise self._unreachable(e) from e
            if method == "GET" and attempt < _MAX_ATTEMPTS:
                delay = _retry_delay(resp)
                if delay is not None:
                    await asyncio.sleep(delay)
                    continue
            return resp
        return resp  # pragma: no cover — the loop always returns

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_data: Any = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        kwargs: dict[str, Any] = {"params": params}
        if json_data is not None:
            kwargs["json"] = json_data
        resp = await self._send(method, path, **kwargs)
        self._raise_for_status(resp)
        return self._parse_body(resp)

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return await self._request("GET", path, params=params)

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
        resp = await self._send("GET", self._version_url)
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
        except httpx.ConnectTimeout as e:
            # Connect timeout means the host was never reached (unreachable), not
            # a slow stream; caught before the TimeoutException branch.
            raise self._unreachable(e) from e
        except httpx.TimeoutException as e:
            msg = f"Log stream timed out: {e}"
            raise ArgoCDTimeoutError(msg) from e
        except httpx.TransportError as e:
            raise self._unreachable(e) from e
