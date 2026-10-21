"""Tests for the Argo CD API client."""

from __future__ import annotations

import asyncio
import ssl
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from unittest.mock import patch

import httpx
import pytest
import respx

from mcp_argocd import client as _client_mod
from mcp_argocd.client import ArgoCDClient
from mcp_argocd.config import ArgoCDConfig
from mcp_argocd.exceptions import (
    ArgoCDApiError,
    ArgoCDAuthError,
    ArgoCDConflictError,
    ArgoCDConnectionError,
    ArgoCDNotFoundError,
    ArgoCDRateLimitError,
    ArgoCDTimeoutError,
)


@pytest.fixture
async def prefixed_client():
    """Client whose base URL carries a path prefix (reverse-proxy install)."""
    ac = ArgoCDClient(ArgoCDConfig(url="https://example.com/cd-core", token="test-token"))
    yield ac
    await ac.close()


async def test_base_url_keeps_path_prefix(prefixed_client):
    with respx.mock(base_url="https://example.com") as router:
        route = router.get("/cd-core/api/v1/applications").mock(
            return_value=httpx.Response(200, json={"items": []})
        )
        await prefixed_client.get("/applications")
        assert route.called
        assert route.calls.last.request.url.path == "/cd-core/api/v1/applications"


async def test_version_path_outside_v1(prefixed_client):
    with respx.mock(base_url="https://example.com") as router:
        route = router.get("/cd-core/api/version").mock(
            return_value=httpx.Response(200, json={"Version": "v3.5.3"})
        )
        result = await prefixed_client.get_version()
        assert route.called
        assert result["Version"] == "v3.5.3"


async def test_authorization_header(client, mock_api):
    route = mock_api.get("/api/v1/applications").mock(
        return_value=httpx.Response(200, json={"items": []})
    )
    await client.get("/applications")
    assert route.calls.last.request.headers["authorization"] == "Bearer test-token"


async def test_auth_error_401(client, mock_api):
    mock_api.get("/api/v1/applications").mock(
        return_value=httpx.Response(401, json={"error": "token expired", "code": 16})
    )
    with pytest.raises(ArgoCDAuthError) as exc:
        await client.get("/applications")
    assert exc.value.status_code == 401


async def test_permission_denied_is_auth_error(client, mock_api):
    mock_api.get("/api/v1/applications/x").mock(
        return_value=httpx.Response(
            403,
            json={
                "error": "permission denied: applications, sync, default/app",
                "code": 7,
                "message": "permission denied",
            },
        )
    )
    with pytest.raises(ArgoCDAuthError) as exc:
        await client.get("/applications/x")
    assert exc.value.grpc_code == 7


async def test_not_found(client, mock_api):
    mock_api.get("/api/v1/applications/missing").mock(
        return_value=httpx.Response(404, json={"message": "app not found", "code": 5})
    )
    with pytest.raises(ArgoCDNotFoundError):
        await client.get("/applications/missing")


async def test_conflict_from_grpc_code(client, mock_api):
    mock_api.post("/api/v1/applications/x/sync").mock(
        return_value=httpx.Response(
            400,
            json={"message": "another operation is already in progress", "code": 9},
        )
    )
    with pytest.raises(ArgoCDConflictError) as exc:
        await client.post("/applications/x/sync")
    assert exc.value.grpc_code == 9


async def test_conflict_from_409_grpc_code_10(client, mock_api):
    # 409 with grpc code 10 (Aborted) is a conflict just like a 400 with code 9.
    mock_api.post("/api/v1/applications/x/sync").mock(
        return_value=httpx.Response(409, json={"message": "aborted", "code": 10})
    )
    with pytest.raises(ArgoCDConflictError) as exc:
        await client.post("/applications/x/sync")
    assert exc.value.status_code == 409
    assert exc.value.grpc_code == 10


async def test_plain_400_is_api_error_not_conflict(client, mock_api):
    mock_api.post("/api/v1/applications/x/sync").mock(
        return_value=httpx.Response(400, json={"message": "bad request", "code": 3})
    )
    with pytest.raises(ArgoCDApiError) as exc:
        await client.post("/applications/x/sync")
    assert not isinstance(exc.value, ArgoCDConflictError)


async def test_server_error(client, mock_api):
    mock_api.get("/api/v1/applications").mock(return_value=httpx.Response(500, text="boom"))
    with pytest.raises(ArgoCDApiError) as exc:
        await client.get("/applications")
    assert exc.value.status_code == 500


async def test_html_guard_on_success(client, mock_api):
    mock_api.get("/api/v1/applications").mock(
        return_value=httpx.Response(
            200, text="<html><body>Login</body></html>", headers={"content-type": "text/html"}
        )
    )
    with pytest.raises(ArgoCDApiError, match="HTML"):
        await client.get("/applications")


async def test_html_guard_on_error(client, mock_api):
    mock_api.get("/api/v1/applications").mock(
        return_value=httpx.Response(
            502, text="<html>Bad Gateway</html>", headers={"content-type": "text/html"}
        )
    )
    with pytest.raises(ArgoCDApiError, match="HTML"):
        await client.get("/applications")


async def test_empty_response_is_none(client, mock_api):
    mock_api.delete("/api/v1/applications/x").mock(return_value=httpx.Response(204))
    assert await client.delete("/applications/x") is None


async def test_timeout_maps_to_timeout_error(client, mock_api):
    mock_api.get("/api/v1/applications").mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(ArgoCDTimeoutError):
        await client.get("/applications")


async def test_stream_lines_stops_on_last(client, mock_api):
    ndjson = (
        '{"result":{"content":"line 1","podName":"p","timeStampStr":"t1","last":false}}\n'
        '{"result":{"content":"line 2","podName":"p","timeStampStr":"t2","last":false}}\n'
        '{"result":{"content":"","podName":"p","timeStampStr":"t3","last":true}}\n'
        '{"result":{"content":"should not be read","podName":"p","last":false}}\n'
    )
    mock_api.get("/api/v1/applications/x/logs").mock(return_value=httpx.Response(200, text=ndjson))
    seen = []
    async for obj in client.stream_lines("/applications/x/logs"):
        seen.append(obj["result"])
        if obj["result"]["last"]:
            break
    assert [s["content"] for s in seen] == ["line 1", "line 2", ""]


async def test_stream_lines_raises_on_error(client, mock_api):
    mock_api.get("/api/v1/applications/x/logs").mock(
        return_value=httpx.Response(403, json={"message": "logs, get denied", "code": 7})
    )
    with pytest.raises(ArgoCDAuthError):
        async for _ in client.stream_lines("/applications/x/logs"):
            pass


async def test_stream_lines_skips_blank_lines(client, mock_api):
    mock_api.get("/api/v1/applications/x/logs").mock(
        return_value=httpx.Response(
            200, text='{"result":{"content":"a"}}\n\n{"result":{"content":"b"}}\n'
        )
    )
    seen = [obj["result"]["content"] async for obj in client.stream_lines("/applications/x/logs")]
    assert seen == ["a", "b"]


async def test_stream_lines_timeout(client, mock_api):
    mock_api.get("/api/v1/applications/x/logs").mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(ArgoCDTimeoutError):
        async for _ in client.stream_lines("/applications/x/logs"):
            pass


async def test_get_version_timeout(client, mock_api):
    # ReadTimeout (not ConnectTimeout, which now maps to unreachable) is the
    # "server is slow" case get_version should surface as a timeout.
    mock_api.get("/api/version").mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(ArgoCDTimeoutError):
        await client.get_version()


async def test_connect_error_maps_to_connection_error(client, mock_api):
    mock_api.get("/api/v1/applications").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(ArgoCDConnectionError, match="Cannot reach Argo CD"):
        await client.get("/applications")


async def test_get_version_connect_error(client, mock_api):
    mock_api.get("/api/version").mock(side_effect=httpx.ConnectError("no route"))
    with pytest.raises(ArgoCDConnectionError):
        await client.get_version()


async def test_stream_lines_connect_error(client, mock_api):
    mock_api.get("/api/v1/applications/x/logs").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(ArgoCDConnectionError):
        async for _ in client.stream_lines("/applications/x/logs"):
            pass


async def test_stream_lines_connect_timeout_is_unreachable(client, mock_api):
    # A connect timeout on the log stream maps to unreachable, not a timeout.
    mock_api.get("/api/v1/applications/x/logs").mock(side_effect=httpx.ConnectTimeout("no route"))
    with pytest.raises(ArgoCDConnectionError):
        async for _ in client.stream_lines("/applications/x/logs"):
            pass


async def test_read_error_maps_to_connection_error(client, mock_api):
    # ReadError (and the other transport errors: ProxyError, RemoteProtocolError,
    # UnsupportedProtocol) must surface as a connection error, not an unexpected crash.
    mock_api.get("/api/v1/applications").mock(side_effect=httpx.ReadError("peer reset"))
    with pytest.raises(ArgoCDConnectionError, match="Cannot reach Argo CD"):
        await client.get("/applications")


async def test_unsupported_protocol_maps_to_connection_error(client, mock_api):
    mock_api.get("/api/v1/applications").mock(side_effect=httpx.UnsupportedProtocol("bad scheme"))
    with pytest.raises(ArgoCDConnectionError):
        await client.get("/applications")


async def test_connection_error_strips_url_credentials():
    # A credential embedded in ARGOCD_URL must never leak into the error message.
    ac = ArgoCDClient(ArgoCDConfig(url="https://user:secret@host.example.com", token="t"))
    with respx.mock(base_url="https://host.example.com") as router:
        router.get("/api/v1/applications").mock(side_effect=httpx.ConnectError("refused"))
        with pytest.raises(ArgoCDConnectionError) as excinfo:
            await ac.get("/applications")
    await ac.close()
    assert "secret" not in str(excinfo.value)


async def test_client_passes_verify_and_timeout():
    # The httpx client must receive a per-phase timeout with the connect phase
    # capped (R2) and the TLS-verify flag; dropping either (verify=, timeout=) is
    # a silent security/behaviour regression. A connection-retrying transport is
    # also wired in.
    with patch("mcp_argocd.client.httpx.AsyncClient") as mock_cls:
        ArgoCDClient(
            ArgoCDConfig(url="https://a.example.com", token="t", timeout=50, ssl_verify=False)
        )
    _, kwargs = mock_cls.call_args
    timeout = kwargs["timeout"]
    assert isinstance(timeout, httpx.Timeout)
    assert timeout.connect == 10  # min(10, 50)
    assert timeout.read == 50
    assert kwargs["verify"] is False
    assert isinstance(kwargs["transport"], httpx.AsyncHTTPTransport)


async def test_json_parse_error_on_success(client, mock_api):
    mock_api.get("/api/v1/applications").mock(
        return_value=httpx.Response(
            200, content=b"not json{", headers={"content-type": "application/json"}
        )
    )
    with pytest.raises(ArgoCDApiError, match="JSON parse error"):
        await client.get("/applications")


async def test_non_dict_error_body_falls_back_to_text(client, mock_api):
    mock_api.get("/api/v1/applications").mock(return_value=httpx.Response(400, json=["oops"]))
    with pytest.raises(ArgoCDApiError) as exc:
        await client.get("/applications")
    assert "oops" in exc.value.body


# ── R2 transport: GET-only retries, Retry-After, timeout vs. unreachable ──────
# (The timeout-vs-unreachable split already existed; mcp-argocd is the reference
# the other servers ported it from — see the ReadTimeout/ConnectError tests
# above. These guards are the new GET-retry policy and the 429 Retry-After
# surfacing.)


@pytest.fixture
def _no_jitter(monkeypatch):
    """Make the jittered backoff instant so retry tests do not sleep."""
    monkeypatch.setattr(_client_mod._jitter, "uniform", lambda _a, _b: 0.0)


async def test_get_retries_on_503_then_succeeds(client, mock_api, _no_jitter):
    route = mock_api.get("/api/v1/applications").mock(
        side_effect=[
            httpx.Response(503, headers={"Retry-After": "1"}, json={"error": "unavailable"}),
            httpx.Response(200, json={"items": []}),
        ]
    )
    result = await client.get("/applications")
    assert result == {"items": []}
    assert route.call_count == 2


async def test_get_retries_on_429_with_short_retry_after(client, mock_api):
    route = mock_api.get("/api/v1/applications").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "0"}, json={"error": "slow down"}),
            httpx.Response(200, json={"items": []}),
        ]
    )
    result = await client.get("/applications")
    assert result == {"items": []}
    assert route.call_count == 2


async def test_post_is_never_retried(client, mock_api):
    route = mock_api.post("/api/v1/applications").mock(
        side_effect=[
            httpx.Response(503, json={"error": "unavailable"}),
            httpx.Response(200, json={}),
        ]
    )
    with pytest.raises(ArgoCDApiError) as exc:
        await client.post("/applications", json_data={})
    assert exc.value.status_code == 503
    assert route.call_count == 1


async def test_429_long_retry_after_is_not_retried_and_surfaces_retry_after(client, mock_api):
    route = mock_api.get("/api/v1/applications").mock(
        return_value=httpx.Response(429, headers={"Retry-After": "30"}, json={"error": "slow down"})
    )
    with pytest.raises(ArgoCDRateLimitError) as exc:
        await client.get("/applications")
    assert exc.value.retry_after == 30.0
    assert exc.value.status_code == 429
    assert route.call_count == 1


# ── R2 fix round: TLS on the transport, retry bounds, Retry-After parsing ─────


def _resp(status: int, retry_after: str | None = None) -> httpx.Response:
    headers = {"Retry-After": retry_after} if retry_after is not None else {}
    return httpx.Response(status, headers=headers)


def test_transport_carries_verify_false():
    # httpx 0.28 ignores client-level verify once a transport is supplied, so the
    # transport must carry it or TLS verification silently turns back on.
    ac = ArgoCDClient(ArgoCDConfig(url="https://a.example.com", token="t", ssl_verify=False))
    ctx = ac._client._transport._pool._ssl_context
    assert ctx.verify_mode == ssl.CERT_NONE


def test_transport_carries_verify_true():
    ac = ArgoCDClient(ArgoCDConfig(url="https://a.example.com", token="t", ssl_verify=True))
    ctx = ac._client._transport._pool._ssl_context
    assert ctx.verify_mode == ssl.CERT_REQUIRED


async def test_connect_timeout_is_unreachable(client, mock_api):
    # ConnectTimeout is a TimeoutException subclass but means the host was never
    # reached, so it must map to the connection (unreachable) error.
    mock_api.get("/api/v1/applications").mock(side_effect=httpx.ConnectTimeout("no route"))
    with pytest.raises(ArgoCDConnectionError):
        await client.get("/applications")


async def test_get_gives_up_after_two_attempts(client, mock_api, _no_jitter):
    route = mock_api.get("/api/v1/applications").mock(
        return_value=httpx.Response(503, json={"error": "unavailable"})
    )
    with pytest.raises(ArgoCDApiError) as exc:
        await client.get("/applications")
    assert exc.value.status_code == 503
    assert route.call_count == 2


async def test_cancellation_propagates_without_retry(client, monkeypatch):
    # The retry loop must not swallow cancellation; it propagates on the first
    # attempt. (respx cannot carry a BaseException side_effect, so patch the
    # httpx client directly and count the calls.)
    calls = 0

    async def _boom(*_a, **_k):
        nonlocal calls
        calls += 1
        raise asyncio.CancelledError

    monkeypatch.setattr(client._client, "request", _boom)
    with pytest.raises(asyncio.CancelledError):
        await client.get("/applications")
    assert calls == 1


def test_retry_after_delta_seconds():
    assert _client_mod._retry_after_seconds(_resp(429, "5")) == 5.0


def test_retry_after_http_date():
    when = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=100))
    secs = _client_mod._retry_after_seconds(_resp(429, when))
    assert secs is not None
    assert 80 <= secs <= 100


def test_retry_after_past_date_clamped_to_zero():
    assert _client_mod._retry_after_seconds(_resp(429, "Wed, 21 Oct 2015 07:28:00 GMT")) == 0.0


def test_retry_after_negative_clamped_to_zero():
    assert _client_mod._retry_after_seconds(_resp(429, "-5")) == 0.0


def test_retry_after_non_finite_rejected():
    assert _client_mod._retry_after_seconds(_resp(429, "nan")) is None
    assert _client_mod._retry_after_seconds(_resp(429, "inf")) is None


def test_retry_after_absent():
    assert _client_mod._retry_after_seconds(_resp(429)) is None
