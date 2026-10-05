"""Argo CD MCP server configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass

_FALSEY = ("false", "0", "no")
_TRUTHY = ("true", "1", "yes")


def _server_url() -> str:
    """Resolve the base URL from ARGOCD_URL, falling back to the CLI's ARGOCD_SERVER.

    ARGOCD_SERVER is a bare host:port (the argocd CLI's name); prepend https://
    when it has no scheme so the client base URL is well-formed.
    """
    url = os.getenv("ARGOCD_URL")
    if url:
        return url.rstrip("/")
    server = os.getenv("ARGOCD_SERVER", "")
    if server and "://" not in server:
        server = f"https://{server}"
    return server.rstrip("/")


@dataclass
class ArgoCDConfig:
    """Configuration for the Argo CD MCP server, loaded from environment variables."""

    url: str = ""
    token: str = ""
    read_only: bool = False
    timeout: int = 30
    ssl_verify: bool = True
    app_namespace: str | None = None

    @classmethod
    def from_env(cls) -> ArgoCDConfig:
        token = (
            os.getenv("ARGOCD_TOKEN")
            or os.getenv("ARGOCD_AUTH_TOKEN")
            or os.getenv("ARGOCD_API_TOKEN", "")
        )
        read_only = os.getenv("ARGOCD_READ_ONLY", "false").lower() in _TRUTHY
        timeout = int(os.getenv("ARGOCD_TIMEOUT", "30"))
        ssl_verify = os.getenv("ARGOCD_SSL_VERIFY", "true").lower() not in _FALSEY
        if os.getenv("ARGOCD_INSECURE", "").lower() in _TRUTHY:
            ssl_verify = False

        return cls(
            url=_server_url(),
            token=token,
            read_only=read_only,
            timeout=timeout,
            ssl_verify=ssl_verify,
            app_namespace=os.getenv("ARGOCD_APP_NAMESPACE") or None,
        )

    @property
    def api_url(self) -> str:
        return f"{self.url}/api/v1"

    def validate(self) -> None:
        if not self.url:
            msg = "ARGOCD_URL environment variable is required"
            raise ValueError(msg)
        if not self.token:
            msg = (
                "Argo CD token is required. Set one of: ARGOCD_TOKEN, "
                "ARGOCD_AUTH_TOKEN, ARGOCD_API_TOKEN"
            )
            raise ValueError(msg)
