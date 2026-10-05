"""Shared helper functions for server modules."""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Any

# ════════════════════════════════════════════════════════════════════
# Static file loading
# ════════════════════════════════════════════════════════════════════


@functools.cache
def _load_file(base_dir: str, filename: str) -> str:
    """Load a file from the given directory with path traversal protection.

    Results are cached — static files do not change at runtime.
    """
    if "/" in filename or "\\" in filename or ".." in filename:
        msg = f"Invalid filename: {filename}"
        raise ValueError(msg)
    base = Path(base_dir)
    path = base / filename
    if not path.resolve().is_relative_to(base.resolve()):
        msg = f"Invalid filename: {filename}"
        raise ValueError(msg)
    return path.read_text(encoding="utf-8")


# ════════════════════════════════════════════════════════════════════
# Secret scrubbing
# ════════════════════════════════════════════════════════════════════

# Credential fields that must never leave the server, plus the two container
# keys whose whole contents are sensitive (`config` on clusters, `jwtTokens` on
# project roles). One source of truth; `_scrub` drops every one recursively and
# it is applied to repository, cluster, and project payloads, including
# `full=True` paths.
SECRET_KEYS = (
    "password",
    "sshPrivateKey",
    "tlsClientCertData",
    "tlsClientCertKey",
    "githubAppPrivateKey",
    "bearerToken",
    "gcpServiceAccountKey",
    "azureServicePrincipalClientSecret",
    "config",
    "jwtTokens",
)


def _scrub(obj: Any) -> Any:
    """Recursively drop every ``SECRET_KEYS`` entry from dicts and lists."""
    if isinstance(obj, dict):
        return {k: _scrub(v) for k, v in obj.items() if k not in SECRET_KEYS}
    if isinstance(obj, list):
        return [_scrub(v) for v in obj]
    return obj


# ════════════════════════════════════════════════════════════════════
# Client-side paging (Argo CD list endpoints take no paging parameters)
# ════════════════════════════════════════════════════════════════════


def _page(items: list[Any], limit: int, offset: int) -> tuple[list[Any], int, bool, int | None]:
    """Slice *items* to one page, returning (page, total, has_more, next_offset)."""
    total = len(items)
    page = items[offset : offset + limit]
    next_offset = offset + limit
    has_more = next_offset < total
    return page, total, has_more, (next_offset if has_more else None)


# ════════════════════════════════════════════════════════════════════
# Argo CD name / resource parsing
# ════════════════════════════════════════════════════════════════════


def _split_app_name(name: str, app_namespace: str | None = None) -> tuple[str, str | None]:
    """Split ``namespace/name`` into (name, app_namespace).

    An explicit *app_namespace* wins; otherwise a ``namespace/name`` value is
    split and the leading segment becomes the ``appNamespace``.
    """
    if app_namespace:
        return name, app_namespace
    if "/" in name:
        ns, _, bare = name.partition("/")
        return bare, ns
    return name, None


def _parse_resource_selector(selector: str) -> dict[str, str | None]:
    """Parse ``[group:]kind:name[/namespace]`` into its parts.

    ``apps:Deployment:web/default`` -> group ``apps``, kind ``Deployment``,
    name ``web``, namespace ``default``. A missing group is ``""``; a missing
    namespace is ``None``.
    """
    rest, _, namespace = selector.partition("/")
    parts = rest.split(":")
    if len(parts) == 3:
        group, kind, res_name = parts
    elif len(parts) == 2:
        group, (kind, res_name) = "", parts
    else:
        msg = f"Invalid resource selector: {selector!r}. Expected [group:]kind:name[/namespace]"
        raise ValueError(msg)
    return {"group": group, "kind": kind, "name": res_name, "namespace": namespace or None}


def _truncate(text: str | None, n: int) -> tuple[str | None, bool]:
    """Return (text, truncated) with *text* cut to *n* chars when longer."""
    if text is None:
        return None, False
    if len(text) <= n:
        return text, False
    return text[:n], True


def _terminal(phase: str | None) -> bool:
    """True when an operation phase is terminal (no further polling needed)."""
    return phase in {"Succeeded", "Failed", "Error"}
