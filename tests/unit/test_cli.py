"""CLI entry point tests."""

from __future__ import annotations

import os
from collections.abc import Iterator
from unittest.mock import patch

import httpx
import pytest
from click.testing import CliRunner

from mcp_argocd import _BearerTokenVerifier, main
from mcp_argocd.servers.argocd import mcp as server_mcp

_ENV = {"ARGOCD_URL": "https://argocd.example.com", "ARGOCD_TOKEN": "test-token"}
_TOKEN32 = "0123456789abcdef0123456789abcdef"


@pytest.fixture(autouse=True)
def _reset_auth() -> Iterator[None]:
    """main() mutates the module-level server's auth; keep tests isolated."""
    yield
    server_mcp.auth = None


def _close_coro(coro):
    """Stand in for ``asyncio.run``: close the ``mcp.run_async`` coroutine so it is
    never left un-awaited (otherwise GC emits ``RuntimeWarning: coroutine ... never awaited``).
    """
    coro.close()


def _patch_run():
    return patch("mcp_argocd.asyncio.run", side_effect=_close_coro)


def test_sse_deprecation_warning():
    runner = CliRunner()
    with (
        _patch_run() as mock_run,
        patch.dict("os.environ", _ENV),
    ):
        result = runner.invoke(main, ["--transport", "sse"])
        assert result.exit_code == 0
        assert (
            "Warning: --transport sse uses the HTTP+SSE transport, deprecated in MCP 2026-07-28"
            in result.output
        )
        assert "Use --transport streamable-http" in result.output
        mock_run.assert_called_once()


def test_insecure_sets_ssl_verify_false():
    runner = CliRunner()
    with (
        _patch_run(),
        patch.dict("os.environ", _ENV, clear=False),
    ):
        os.environ.pop("ARGOCD_SSL_VERIFY", None)
        result = runner.invoke(main, ["--insecure"])
        assert result.exit_code == 0
        assert os.environ["ARGOCD_SSL_VERIFY"] == "false"


def test_read_only_sets_env():
    runner = CliRunner()
    with (
        _patch_run(),
        patch.dict("os.environ", _ENV, clear=False),
    ):
        os.environ.pop("ARGOCD_READ_ONLY", None)
        result = runner.invoke(main, ["--read-only"])
        assert result.exit_code == 0
        assert os.environ["ARGOCD_READ_ONLY"] == "true"


def test_token_flag_hidden_from_help():
    result = CliRunner().invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "--argocd-token" not in result.output
    assert "--argocd-url" in result.output


def test_token_flag_still_works_and_warns():
    runner = CliRunner()
    env = {"ARGOCD_URL": "https://argocd.example.com"}
    with (
        _patch_run() as mock_run,
        patch.dict("os.environ", env, clear=True),
    ):
        result = runner.invoke(main, ["--argocd-token", "argv-tok"])
        assert result.exit_code == 0
        mock_run.assert_called_once()
        assert os.environ["ARGOCD_TOKEN"] == "argv-tok"
        assert "--argocd-token is deprecated" in result.stderr
        assert "ARGOCD_TOKEN" in result.stderr
        assert "deprecated" not in result.stdout


def test_env_token_does_not_warn():
    runner = CliRunner()
    with (
        _patch_run(),
        patch.dict("os.environ", _ENV, clear=True),
    ):
        result = runner.invoke(main, [])
        assert result.exit_code == 0
        assert "deprecated" not in result.stderr


class TestHttpBearerToken:
    """An HTTP transport on a non-loopback host needs a bearer token."""

    def test_non_loopback_without_token_exits(self):
        runner = CliRunner()
        env = dict(_ENV)
        with _patch_run() as mock_run, patch.dict("os.environ", env, clear=True):
            result = runner.invoke(main, ["--transport", "streamable-http", "--host", "0.0.0.0"])
        assert result.exit_code == 2
        assert "ARGOCD_HTTP_BEARER_TOKEN" in result.output
        mock_run.assert_not_called()
        assert server_mcp.auth is None

    def test_token_sets_static_auth(self):
        runner = CliRunner()
        env = {**_ENV, "ARGOCD_HTTP_BEARER_TOKEN": _TOKEN32}
        with _patch_run() as mock_run, patch.dict("os.environ", env, clear=True):
            result = runner.invoke(main, ["--transport", "streamable-http", "--host", "0.0.0.0"])
        assert result.exit_code == 0
        assert isinstance(server_mcp.auth, _BearerTokenVerifier)
        mock_run.assert_called_once()

    def test_loopback_without_token_no_auth(self):
        runner = CliRunner()
        env = dict(_ENV)
        with _patch_run() as mock_run, patch.dict("os.environ", env, clear=True):
            result = runner.invoke(main, ["--transport", "streamable-http", "--host", "127.0.0.1"])
        assert result.exit_code == 0
        assert server_mcp.auth is None
        mock_run.assert_called_once()

    def test_short_token_exits(self):
        runner = CliRunner()
        env = {**_ENV, "ARGOCD_HTTP_BEARER_TOKEN": "deadbeef"}  # 8 chars
        with _patch_run() as mock_run, patch.dict("os.environ", env, clear=True):
            result = runner.invoke(main, ["--transport", "streamable-http", "--host", "0.0.0.0"])
        assert result.exit_code == 2
        assert "at least 16" in result.output
        mock_run.assert_not_called()

    def test_stdio_ignores_token(self):
        runner = CliRunner()
        env = {**_ENV, "ARGOCD_HTTP_BEARER_TOKEN": _TOKEN32}
        with _patch_run() as mock_run, patch.dict("os.environ", env, clear=True):
            result = runner.invoke(main, [])
        assert result.exit_code == 0
        assert server_mcp.auth is None
        mock_run.assert_called_once()


async def test_bearer_middleware_streamable_http(monkeypatch: pytest.MonkeyPatch) -> None:
    """Wire check: with auth set, the streamable-http ASGI app enforces the token."""
    monkeypatch.setenv("ARGOCD_URL", "https://argocd.example.com")
    monkeypatch.setenv("ARGOCD_TOKEN", "tok")
    # These boot the real lifespan (app.router.lifespan_context), so strip the
    # read-only env vars: MCP_READ_ONLY=true would otherwise lock the shared
    # server and hide write tools for every later test.
    monkeypatch.delenv("MCP_READ_ONLY", raising=False)
    monkeypatch.delenv("ARGOCD_READ_ONLY", raising=False)
    server_mcp.auth = _BearerTokenVerifier(_TOKEN32)
    try:
        app = server_mcp.http_app()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            async with app.router.lifespan_context(app):
                no_header = await client.post("/mcp", json={})
                wrong_token = await client.post(
                    "/mcp", json={}, headers={"Authorization": "Bearer wrong-token-value"}
                )
                # Non-ASCII must be rejected, not crash: compare_digest raises
                # TypeError on a non-ASCII str, which would 500 with a traceback.
                # HTTP headers are latin-1, so send raw bytes.
                non_ascii = await client.post(
                    "/mcp", json={}, headers={"Authorization": "Bearer café".encode("latin-1")}
                )
                with_header = await client.post(
                    "/mcp", json={}, headers={"Authorization": f"Bearer {_TOKEN32}"}
                )
        assert no_header.status_code == 401
        assert wrong_token.status_code == 401
        assert non_ascii.status_code == 401
        # The middleware ran and let the authenticated request through; any
        # non-401 status proves that.
        assert with_header.status_code != 401
    finally:
        server_mcp.auth = None
