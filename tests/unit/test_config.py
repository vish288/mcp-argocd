"""Tests for Argo CD configuration."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from mcp_argocd.config import ArgoCDConfig

_CLEARED = (
    "ARGOCD_URL",
    "ARGOCD_SERVER",
    "ARGOCD_TOKEN",
    "ARGOCD_AUTH_TOKEN",
    "ARGOCD_API_TOKEN",
    "ARGOCD_READ_ONLY",
    "ARGOCD_TIMEOUT",
    "ARGOCD_SSL_VERIFY",
    "ARGOCD_INSECURE",
    "ARGOCD_APP_NAMESPACE",
)


def _env(**overrides: str):
    base = dict.fromkeys(_CLEARED, "")
    env = {k: v for k, v in {**base, **overrides}.items() if v != ""}
    return patch.dict(os.environ, env, clear=True)


def test_config_from_env():
    with _env(ARGOCD_URL="https://argocd.example.com", ARGOCD_TOKEN="tok"):
        config = ArgoCDConfig.from_env()
    assert config.url == "https://argocd.example.com"
    assert config.token == "tok"
    assert config.read_only is False
    assert config.timeout == 30
    assert config.ssl_verify is True


def test_token_aliases():
    with _env(ARGOCD_URL="https://a.example.com", ARGOCD_AUTH_TOKEN="auth"):
        assert ArgoCDConfig.from_env().token == "auth"
    with _env(ARGOCD_URL="https://a.example.com", ARGOCD_API_TOKEN="api"):
        assert ArgoCDConfig.from_env().token == "api"


def test_token_priority():
    with _env(
        ARGOCD_URL="https://a.example.com",
        ARGOCD_TOKEN="winner",
        ARGOCD_AUTH_TOKEN="loser1",
        ARGOCD_API_TOKEN="loser2",
    ):
        assert ArgoCDConfig.from_env().token == "winner"


def test_server_without_scheme_gets_https():
    with _env(ARGOCD_SERVER="argocd.example.com:8080", ARGOCD_TOKEN="x"):
        assert ArgoCDConfig.from_env().url == "https://argocd.example.com:8080"


def test_server_with_scheme_kept():
    with _env(ARGOCD_SERVER="http://argocd.local:8080", ARGOCD_TOKEN="x"):
        assert ArgoCDConfig.from_env().url == "http://argocd.local:8080"


def test_url_wins_over_server():
    with _env(
        ARGOCD_URL="https://primary.example.com",
        ARGOCD_SERVER="fallback.example.com",
        ARGOCD_TOKEN="x",
    ):
        assert ArgoCDConfig.from_env().url == "https://primary.example.com"


def test_url_strips_trailing_slash():
    with _env(ARGOCD_URL="https://argocd.example.com/", ARGOCD_TOKEN="x"):
        assert ArgoCDConfig.from_env().url == "https://argocd.example.com"


def test_path_prefix_preserved():
    with _env(ARGOCD_URL="https://example.com/cd-core", ARGOCD_TOKEN="x"):
        config = ArgoCDConfig.from_env()
    assert config.url == "https://example.com/cd-core"
    assert config.api_url == "https://example.com/cd-core/api/v1"


def test_read_only():
    with _env(ARGOCD_URL="https://a.example.com", ARGOCD_TOKEN="x", ARGOCD_READ_ONLY="yes"):
        assert ArgoCDConfig.from_env().read_only is True


def test_ssl_verify_disabled():
    with _env(ARGOCD_URL="https://a.example.com", ARGOCD_TOKEN="x", ARGOCD_SSL_VERIFY="false"):
        assert ArgoCDConfig.from_env().ssl_verify is False


def test_insecure_alias_disables_ssl():
    with _env(ARGOCD_URL="https://a.example.com", ARGOCD_TOKEN="x", ARGOCD_INSECURE="true"):
        assert ArgoCDConfig.from_env().ssl_verify is False


def test_app_namespace():
    with _env(ARGOCD_URL="https://a.example.com", ARGOCD_TOKEN="x", ARGOCD_APP_NAMESPACE="team-a"):
        assert ArgoCDConfig.from_env().app_namespace == "team-a"


def test_api_url():
    config = ArgoCDConfig(url="https://argocd.example.com", token="x")
    assert config.api_url == "https://argocd.example.com/api/v1"


def test_validate_missing_url():
    with pytest.raises(ValueError, match="ARGOCD_URL"):
        ArgoCDConfig(url="", token="x").validate()


def test_validate_missing_token():
    with pytest.raises(ValueError, match="ARGOCD_TOKEN"):
        ArgoCDConfig(url="https://a.example.com", token="").validate()
