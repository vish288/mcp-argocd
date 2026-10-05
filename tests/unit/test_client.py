"""Tests for the Argo CD API client."""

from __future__ import annotations

import httpx
import pytest
import respx

from mcp_argocd.client import ArgoCDClient
from mcp_argocd.config import ArgoCDConfig
from mcp_argocd.exceptions import (
    ArgoCDApiError,
    ArgoCDAuthError,
    ArgoCDConflictError,
    ArgoCDNotFoundError,
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
    mock_api.get("/api/version").mock(side_effect=httpx.ConnectTimeout("slow"))
    with pytest.raises(ArgoCDTimeoutError):
        await client.get_version()


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
