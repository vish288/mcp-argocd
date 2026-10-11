"""CLI entry point tests."""

from __future__ import annotations

import os
from unittest.mock import patch

from click.testing import CliRunner

from mcp_argocd import main

_ENV = {"ARGOCD_URL": "https://argocd.example.com", "ARGOCD_TOKEN": "test-token"}


def test_sse_deprecation_warning():
    runner = CliRunner()
    with (
        patch("mcp_argocd.asyncio.run") as mock_run,
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
        patch("mcp_argocd.asyncio.run"),
        patch.dict("os.environ", _ENV, clear=False),
    ):
        os.environ.pop("ARGOCD_SSL_VERIFY", None)
        result = runner.invoke(main, ["--insecure"])
        assert result.exit_code == 0
        assert os.environ["ARGOCD_SSL_VERIFY"] == "false"


def test_read_only_sets_env():
    runner = CliRunner()
    with (
        patch("mcp_argocd.asyncio.run"),
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
        patch("mcp_argocd.asyncio.run") as mock_run,
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
        patch("mcp_argocd.asyncio.run"),
        patch.dict("os.environ", _ENV, clear=True),
    ):
        result = runner.invoke(main, [])
        assert result.exit_code == 0
        assert "deprecated" not in result.stderr
