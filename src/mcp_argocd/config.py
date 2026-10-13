"""Argo CD MCP server configuration."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

_FALSEY = ("false", "0", "no")
_TRUTHY = ("true", "1", "yes")

# Read-only switch values: stripped and case-insensitive. Only a clearly falsy
# value unlocks; a truthy value or any unrecognised non-empty value locks (fail safe),
# the latter with a WARNING so a deployer sees the typo did not silently unlock writes.
_RO_TRUTHY = frozenset({"1", "true", "yes", "on"})
_RO_FALSEY = frozenset({"", "0", "false", "no", "off"})

# Shared across the mcp-* servers so one deployer-set env var locks any of them
# read-only. The per-server alias (ARGOCD_READ_ONLY) stays; either being truthy wins.
_SHARED_READ_ONLY = "MCP_READ_ONLY"

_log = logging.getLogger(__name__)


def _ro_value(name: str) -> str | None:
    """A read-only source's value, stripped; ``None`` when the var is unset."""
    raw = os.getenv(name)
    return raw.strip() if raw is not None else None


def _locks(value: str) -> bool:
    """Whether a source value locks: anything not clearly falsy (truthy or unrecognised)."""
    return value.lower() not in _RO_FALSEY


def read_only_sources(alias: str) -> list[str]:
    """Env var names (shared first, then the per-server alias) whose value locks read-only."""
    return [
        name
        for name in (_SHARED_READ_ONLY, alias)
        if (value := _ro_value(name)) is not None and _locks(value)
    ]


def _read_only(alias: str) -> bool:
    """Resolve read-only as ``locks(MCP_READ_ONLY) or locks(<alias>)``.

    Any truthy or unrecognised-non-empty source locks the server; a clearly falsy value is
    never an override — it is the default. When locked, log one WARNING per source that did
    not contribute: a falsy value (it had no effect) or an unrecognised value (it locked by
    failing safe, not because it was understood).
    """
    locked = bool(read_only_sources(alias))
    if locked:
        for name in (_SHARED_READ_ONLY, alias):
            value = _ro_value(name)
            if value is None:
                continue
            low = value.lower()
            if low in _RO_FALSEY:
                _log.warning(
                    "read-only is locked on; ignoring %s=%s (a falsy value cannot unlock it)",
                    name,
                    value,
                )
            elif low not in _RO_TRUTHY:
                _log.warning(
                    "read-only: locking on unrecognised %s=%s (anything but a falsy value locks)",
                    name,
                    value,
                )
    return locked


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
    token: str = field(default="", repr=False)  # keep the bearer token out of repr/logs
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
        read_only = _read_only("ARGOCD_READ_ONLY")
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
