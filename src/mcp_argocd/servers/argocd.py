"""Argo CD MCP server — all tool registrations."""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from importlib.metadata import version
from typing import Annotated, Any, Literal
from urllib.parse import quote

from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError
from pydantic import Field

from ..client import ArgoCDClient
from ..config import ArgoCDConfig
from ..exceptions import (
    ArgoCDApiError,
    ArgoCDError,
    ArgoCDTimeoutError,
    ArgoCDWriteDisabledError,
)
from ._helpers import (
    _page,
    _parse_resource_selector,
    _scrub,
    _split_app_name,
    _terminal,
    _truncate,
)

LAST_APPLIED = "kubectl.kubernetes.io/last-applied-configuration"

SyncStatus = Literal["Synced", "OutOfSync", "Unknown"]
HealthStatus = Literal["Healthy", "Progressing", "Degraded", "Suspended", "Missing", "Unknown"]
OperationPhase = Literal["Running", "Terminating", "Failed", "Error", "Succeeded"]

READ = {"readOnlyHint": True, "idempotentHint": True, "openWorldHint": True}
WRITE = {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": True}
WRITE_IDEMPOTENT = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": True,
}
DESTRUCTIVE = {"destructiveHint": True, "readOnlyHint": False, "openWorldHint": True}
DESTRUCTIVE_IDEMPOTENT = {
    "destructiveHint": True,
    "readOnlyHint": False,
    "idempotentHint": True,
    "openWorldHint": True,
}

_log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(server: FastMCP) -> AsyncIterator[dict[str, Any]]:
    config = ArgoCDConfig.from_env()
    config.validate()
    pkg_version = version("mcp-argocd")
    _log.info("mcp-argocd %s starting", pkg_version)
    _log.info("Argo CD: %s (read-only: %s)", config.url, config.read_only)
    client = ArgoCDClient(config)
    try:
        yield {"client": client, "config": config}
    finally:
        await client.close()


mcp = FastMCP(
    name="Argo CD MCP Server",
    instructions=(
        "Provides tools for Argo CD — application status, sync, rollback, drift,"
        " logs, ApplicationSets, clusters, repositories, and projects."
    ),
    lifespan=lifespan,
)


def _get_client(ctx: Context) -> ArgoCDClient:
    return ctx.lifespan_context["client"]


def _get_config(ctx: Context) -> ArgoCDConfig:
    return ctx.lifespan_context["config"]


def _check_write(ctx: Context) -> None:
    if _get_config(ctx).read_only:
        raise ArgoCDWriteDisabledError


# ════════════════════════════════════════════════════════════════════
# JSON envelopes
# ════════════════════════════════════════════════════════════════════


def _dump(data: Any) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False)


def _ok(data: Any) -> str:
    return _dump(data)


def _ok_scrubbed(data: Any) -> str:
    """Success envelope with every credential field removed (repos, clusters, projects)."""
    return _dump(_scrub(data))


def _paginated(items: list[Any], total: int, has_more: bool, next_offset: int | None) -> str:
    """Wrap a client-side-paged list. Argo CD list endpoints take no paging
    parameters, so the server fetches once, filters, sorts, and slices."""
    return _dump(
        {
            "items": items,
            "count": len(items),
            "total": total,
            "has_more": has_more,
            "next_offset": next_offset,
        }
    )


_PERMISSION_RE = re.compile(r"permission denied:\s*([^,\"]+),\s*([^,\"]+),\s*([^\",]+)")


def _permission_triple(text: str) -> tuple[str, str, str] | None:
    m = _PERMISSION_RE.search(text)
    if m:
        return m.group(1).strip(), m.group(2).strip(), m.group(3).strip()
    return None


def _api_hint(error: ArgoCDApiError) -> str | None:
    sc = error.status_code
    msg = error.message or ""
    low = msg.lower()
    search = f"{msg} {error.body or ''}"
    if "html" in low:
        return (
            "Unexpected HTML response — ARGOCD_URL may point at a login page or the wrong path "
            "prefix. Expected /api/version to return JSON."
        )
    if sc == 401:
        return (
            "Token rejected or expired. Generate a new one: `argocd account generate-token "
            "--account <name> --expires-in 24h`, then set ARGOCD_TOKEN. Session tokens from "
            "/api/v1/session expire after 24h by default."
        )
    if sc == 403:
        triple = _permission_triple(search)
        if triple:
            resource, action, obj = triple
            obj = obj.split(", sub:")[0].strip()
            return (
                f"Account lacks `{resource}, {action}` on `{obj}`. Check with "
                f"argocd_can_i(resource='{resource}', action='{action}'). Note: Argo CD also "
                "returns 403 for apps that do not exist when `project` is omitted — pass "
                "`project` to get a real 404."
            )
        return (
            "Permission denied. Check with argocd_can_i. Argo CD also returns 403 for apps that "
            "do not exist when `project` is omitted — pass `project` to get a real 404."
        )
    if sc == 404:
        return (
            "Not found. Pass `project` to distinguish missing from forbidden. For apps outside "
            "the control-plane namespace, use `namespace/name` or `app_namespace`."
        )
    if "another operation is already in progress" in low:
        return (
            "An operation is running. Inspect with argocd_get_operation, wait with "
            "argocd_wait_for_operation, or stop it with argocd_terminate_operation."
        )
    if "sync window" in low or "cansync" in low:
        return (
            "Sync blocked by a sync window. See argocd_get_sync_windows; manual sync needs "
            "`manualSync: true` on the window."
        )
    if sc == 400 and ("auto-sync" in low or "automated" in low or "autosync" in low):
        return (
            "Rollback is refused while auto-sync is enabled. Disable it with "
            'argocd_patch_application(patch=\'{"spec":{"syncPolicy":{"automated":null}}}\') '
            "and retry."
        )
    if sc == 429:
        return "Rate limited. Wait before retrying."
    if sc >= 500:
        return "Argo CD server error. Check argocd-server logs and the repo-server for this app."
    return None


def _err(error: Exception) -> str:
    detail: dict[str, Any] = {"error": str(error)}
    if isinstance(error, ArgoCDWriteDisabledError):
        detail["hint"] = "Server is in read-only mode. Set ARGOCD_READ_ONLY=false to enable writes."
    elif isinstance(error, ArgoCDTimeoutError):
        detail["hint"] = (
            "Operation still running. Call argocd_get_operation to check, or raise "
            "timeout_seconds (max 600)."
        )
    elif isinstance(error, ArgoCDApiError):
        detail["status_code"] = error.status_code
        detail["body"] = error.body
        hint = _api_hint(error)
        if hint:
            detail["hint"] = hint
    return _dump(detail)


_Tool = Callable[..., Awaitable[str]]


def tool_result(fn: _Tool | None = None, *, write: bool = False) -> Any:
    """Apply under ``@mcp.tool``. Expected failures (``ArgoCDError``) become the
    JSON envelope; anything else is a bug and is raised as a ``ToolError`` so it
    surfaces as ``isError: true`` with a logged traceback.

    ``write=True`` runs the read-only guard first.
    """

    def wrap(f: _Tool) -> _Tool:
        @functools.wraps(f)
        async def inner(ctx: Context, *args: Any, **kwargs: Any) -> str:
            try:
                if write:
                    _check_write(ctx)
                return await f(ctx, *args, **kwargs)
            except ArgoCDError as e:
                return _err(e)
            except Exception as e:
                _log.exception("%s failed", f.__name__)
                msg = f"{type(e).__name__}: {e}"
                raise ToolError(msg) from e

        return inner

    return wrap(fn) if fn is not None else wrap


# ════════════════════════════════════════════════════════════════════
# Slim helpers — pure functions, each unit-tested on a fixture
# ════════════════════════════════════════════════════════════════════


def _slim(d: dict, keys: tuple) -> dict:
    return {k: d[k] for k in keys if k in d}


def _params(**kw: Any) -> dict[str, Any]:
    """Request params from tool arguments, dropping unset (None) ones."""
    return {k: v for k, v in kw.items() if v is not None}


def _load_json(value: Any) -> Any:
    """Parse a JSON string; pass through anything already decoded."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _strip_managed_fields(obj: Any) -> Any:
    """Drop ``metadata.managedFields`` (noise that dwarfs the useful payload)."""
    if isinstance(obj, dict):
        meta = obj.get("metadata")
        if isinstance(meta, dict):
            meta.pop("managedFields", None)
    return obj


def _resolve(ctx: Context, name: str, app_namespace: str | None) -> tuple[str, str | None]:
    """Split ``namespace/name`` and apply the ARGOCD_APP_NAMESPACE default."""
    return _split_app_name(name, app_namespace or _get_config(ctx).app_namespace)


# ── slim helpers ──────────────────────────────────────────────────


def _slim_source(s: dict) -> dict:
    return _slim(s, ("repoURL", "path", "chart", "targetRevision"))


def _slim_sources(spec: dict) -> dict:
    if spec.get("sources"):
        return {"sources": [_slim_source(s) for s in spec["sources"]]}
    if spec.get("source"):
        return {"source": _slim_source(spec["source"])}
    return {}


def _slim_dest(d: dict) -> dict:
    return _slim(d, ("server", "name", "namespace"))


def _slim_app(app: dict) -> dict:
    """One list row: identity, sync/health, source, destination, policy flags."""
    meta = app.get("metadata", {})
    spec = app.get("spec", {})
    status = app.get("status", {})
    sync = status.get("sync", {})
    health = status.get("health", {})
    op = status.get("operationState", {})
    automated = (spec.get("syncPolicy") or {}).get("automated")
    out = {
        "name": meta.get("name"),
        "namespace": meta.get("namespace"),
        "project": spec.get("project"),
        "sync_status": sync.get("status"),
        "health_status": health.get("status"),
        "health_message": (health.get("message") or "")[:200],
        "operation_phase": op.get("phase"),
        "revision": (sync.get("revision") or "")[:12] or None,
        "destination": _slim_dest(spec.get("destination", {})),
        "auto_sync": automated is not None,
        "self_heal": bool(automated and automated.get("selfHeal")),
        "prune": bool(automated and automated.get("prune")),
        "reconciled_at": status.get("reconciledAt"),
        "conditions": len(status.get("conditions", []) or []),
    }
    out.update(_slim_sources(spec))
    return out


def _slim_annotations(meta: dict) -> dict:
    ann = dict(meta.get("annotations") or {})
    ann.pop(LAST_APPLIED, None)
    return ann


def _slim_resource_status(r: dict) -> dict:
    out = _slim(r, ("group", "kind", "name", "namespace", "status", "requiresPruning", "syncWave"))
    if "health" in r:
        out["health"] = _slim(r.get("health", {}), ("status", "message"))
    return out


def _filter_resources(resources: list, mode: str) -> list:
    if mode == "all":
        return resources
    if mode == "out_of_sync":
        return [r for r in resources if r.get("status") != "Synced"]
    if mode == "unhealthy":
        return [
            r
            for r in resources
            if (r.get("health") or {}).get("status") not in ("Healthy", None, "")
        ]
    return []


def _slim_app_detail(app: dict, resources_mode: str = "none") -> dict:
    meta = app.get("metadata", {})
    spec = app.get("spec", {})
    status = app.get("status", {})
    op = status.get("operationState", {})
    resources = status.get("resources", []) or []
    counts = {
        "total": len(resources),
        "out_of_sync": sum(1 for r in resources if r.get("status") == "OutOfSync"),
        "unhealthy": sum(
            1
            for r in resources
            if (r.get("health") or {}).get("status") not in ("Healthy", None, "")
        ),
    }
    slim_spec = {
        "project": spec.get("project"),
        "destination": _slim_dest(spec.get("destination", {})),
        "syncPolicy": spec.get("syncPolicy"),
        "revisionHistoryLimit": spec.get("revisionHistoryLimit"),
    }
    slim_spec.update(_slim_sources(spec))
    out = {
        "metadata": {
            "name": meta.get("name"),
            "namespace": meta.get("namespace"),
            "labels": meta.get("labels", {}),
            "annotations": _slim_annotations(meta),
            "creationTimestamp": meta.get("creationTimestamp"),
        },
        "spec": slim_spec,
        "status": {
            "sync": _slim(status.get("sync", {}), ("status", "revision", "revisions")),
            "health": _slim(status.get("health", {}), ("status", "message")),
            "conditions": [
                _slim(c, ("type", "message", "lastTransitionTime"))
                for c in status.get("conditions", []) or []
            ],
            "operation": _slim(op, ("phase", "message", "startedAt", "finishedAt", "retryCount")),
            "summary": status.get("summary", {}),
            "reconciledAt": status.get("reconciledAt"),
            "history_count": len(status.get("history", []) or []),
            "resource_counts": counts,
        },
    }
    if resources_mode != "none":
        out["status"]["resources"] = [
            _slim_resource_status(r) for r in _filter_resources(resources, resources_mode)
        ]
    return out


def _slim_node(node: dict, include_info: bool = False) -> dict:
    out = _slim(node, ("group", "kind", "name", "namespace", "images", "createdAt"))
    if "health" in node:
        out["health"] = _slim(node.get("health", {}), ("status", "message"))
    parents = node.get("parentRefs") or []
    if parents:
        out["parent"] = _slim(parents[0], ("kind", "name"))
    if include_info and node.get("info"):
        out["info"] = node["info"]
    return out


def _slim_diff(item: dict, max_diff_chars: int = 4000, include_states: bool = False) -> dict:
    out = _slim(item, ("group", "kind", "name", "namespace", "modified", "hook"))
    diff, truncated = _truncate(item.get("diff"), max_diff_chars)
    out["diff"] = diff
    if truncated:
        out["diff_truncated"] = True
    if include_states:
        for key in ("targetState", "normalizedLiveState", "predictedLiveState"):
            raw = item.get(key)
            if raw:
                out[key] = _strip_managed_fields(_load_json(raw))
    return out


def _slim_event(ev: dict) -> dict:
    obj = ev.get("involvedObject", {})
    return {
        "type": ev.get("type"),
        "reason": ev.get("reason"),
        "message": (ev.get("message") or "")[:500],
        "count": ev.get("count"),
        "first_timestamp": ev.get("firstTimestamp"),
        "last_timestamp": ev.get("lastTimestamp"),
        "object": _slim(obj, ("kind", "name", "namespace")),
    }


def _slim_history(h: dict) -> dict:
    out = {
        "id": h.get("id"),
        "deployed_at": h.get("deployedAt"),
        "deploy_started_at": h.get("deployStartedAt"),
        "initiated_by": _slim(h.get("initiatedBy", {}), ("username", "automated")),
    }
    if "revisions" in h:
        out["revisions"] = h["revisions"]
    else:
        out["revision"] = h.get("revision")
    if h.get("sources"):
        out["sources"] = [_slim_source(s) for s in h["sources"]]
    elif h.get("source"):
        out["source"] = _slim_source(h["source"])
    return out


def _slim_operation(status: dict, include_resources: bool = True) -> dict:
    op = status.get("operationState")
    if not op:
        return {"phase": None}
    operation = op.get("operation", {})
    sync = operation.get("sync", {})
    out: dict[str, Any] = {
        "phase": op.get("phase"),
        "message": op.get("message"),
        "started_at": op.get("startedAt"),
        "finished_at": op.get("finishedAt"),
        "retry_count": op.get("retryCount"),
        "initiated_by": _slim(operation.get("initiatedBy", {}), ("username", "automated")),
        "sync": {
            "revision": sync.get("revision"),
            "prune": sync.get("prune"),
            "dryRun": sync.get("dryRun"),
            "syncOptions": sync.get("syncOptions"),
        },
    }
    if include_resources and sync.get("resources") is not None:
        out["sync"]["resources"] = sync.get("resources")
    result = op.get("syncResult")
    if result:
        res_items = result.get("resources", []) or []
        counts: dict[str, int] = {}
        for r in res_items:
            st = r.get("status", "Unknown")
            counts[st] = counts.get(st, 0) + 1
        failed = [
            {
                **_slim(r, ("group", "kind", "name", "namespace", "status")),
                "message": (r.get("message") or "")[:300],
            }
            for r in res_items
            if r.get("status") in ("SyncFailed", "Error")
        ]
        out["result"] = {"revision": result.get("revision"), "counts": counts, "failed": failed}
    return out


def _slim_window(w: dict) -> dict:
    return _slim(
        w,
        ("kind", "schedule", "duration", "applications", "namespaces", "clusters", "manualSync"),
    )


async def _poll_operation(
    client: ArgoCDClient,
    bare: str,
    params: dict,
    timeout_seconds: int,
    poll_interval: int = 3,
) -> dict:
    """Poll an application until its operation is terminal, gone, or timed out."""
    start = time.monotonic()
    while True:
        data = await client.get(f"/applications/{bare}", params)
        status = data.get("status", {})
        op = _slim_operation(status, include_resources=True)
        waited = time.monotonic() - start
        phase = op.get("phase")
        done = phase is None or _terminal(phase)
        if done or waited >= timeout_seconds:
            op["waited_seconds"] = round(waited, 1)
            op["timed_out"] = not done
            op["health_status"] = status.get("health", {}).get("status")
            op["sync_status"] = status.get("sync", {}).get("status")
            return op
        await asyncio.sleep(poll_interval)


def _resource_selector_body(selector: str) -> dict:
    return {k: v for k, v in _parse_resource_selector(selector).items() if v is not None}


# ════════════════════════════════════════════════════════════════════
# Annotated parameter aliases
# ════════════════════════════════════════════════════════════════════

Name = Annotated[
    str,
    Field(
        description="Application name; accepts namespace/name for apps-in-any-namespace",
        min_length=1,
    ),
]
AppNamespace = Annotated[
    str | None, Field(description="Override appNamespace; defaults to ARGOCD_APP_NAMESPACE")
]
Project = Annotated[
    str | None,
    Field(description="Project; with it a missing app is 404 and a wrong-project app is 403"),
]
Limit = Annotated[int, Field(description="Max results to return (1-200)", ge=1, le=200)]
Offset = Annotated[int, Field(description="Result offset for paging", ge=0)]
Full = Annotated[bool, Field(description="Return the raw API payload instead of the slim one")]
ResourceName = Annotated[str, Field(description="Target resource name", min_length=1)]
Kind = Annotated[str, Field(description="Resource kind, e.g. Deployment", min_length=1)]


# ════════════════════════════════════════════════════════════════════
# 2.1 Applications — read (14)
# ════════════════════════════════════════════════════════════════════


@mcp.tool(tags={"argocd", "applications", "read"}, annotations=READ)
@tool_result
async def argocd_list_applications(
    ctx: Context,
    projects: Annotated[list[str] | None, Field(description="Filter by project names")] = None,
    selector: Annotated[str | None, Field(description="Label selector, e.g. team=platform")] = None,
    repo: Annotated[str | None, Field(description="Filter by source repo URL")] = None,
    app_namespace: AppNamespace = None,
    name_prefix: Annotated[str | None, Field(description="Client-side name prefix filter")] = None,
    sync_status: Annotated[SyncStatus | None, Field(description="Filter by sync status")] = None,
    health_status: Annotated[HealthStatus | None, Field(description="Filter by health")] = None,
    destination: Annotated[
        str | None, Field(description="Match destination.server or destination.name")
    ] = None,
    dest_namespace: Annotated[str | None, Field(description="Match destination namespace")] = None,
    operation_phase: Annotated[
        OperationPhase | None, Field(description="Filter by current operation phase")
    ] = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> str:
    """List applications with slim rows (name, project, sync/health, source, destination, policy).

    Filters sync_status, health_status, destination, and name_prefix client-side; the Argo CD
    list endpoint has no paging, so results are sorted by name and sliced here.
    """
    params = _params(
        projects=projects,
        selector=selector,
        repo=repo,
        appNamespace=app_namespace or _get_config(ctx).app_namespace,
    )
    data = await _get_client(ctx).get("/applications", params)
    items = data.get("items") or []

    def keep(app: dict) -> bool:
        meta = app.get("metadata", {})
        status = app.get("status", {})
        spec = app.get("spec", {})
        dest = spec.get("destination", {})
        if name_prefix and not (meta.get("name") or "").startswith(name_prefix):
            return False
        if sync_status and status.get("sync", {}).get("status") != sync_status:
            return False
        if health_status and status.get("health", {}).get("status") != health_status:
            return False
        if operation_phase and status.get("operationState", {}).get("phase") != operation_phase:
            return False
        if destination and destination not in (dest.get("server"), dest.get("name")):
            return False
        if dest_namespace and dest.get("namespace") != dest_namespace:
            return False
        return True

    items = sorted(
        (a for a in items if keep(a)), key=lambda a: a.get("metadata", {}).get("name", "")
    )
    page, total, has_more, next_offset = _page(items, limit, offset)
    return _paginated([_slim_app(a) for a in page], total, has_more, next_offset)


@mcp.tool(tags={"argocd", "applications", "read"}, annotations=READ)
@tool_result
async def argocd_get_application(
    ctx: Context,
    name: Name,
    app_namespace: AppNamespace = None,
    project: Project = None,
    refresh: Annotated[
        Literal["none", "normal", "hard"],
        Field(description="normal reconciles against cache; hard re-fetches from the repo server"),
    ] = "none",
    resources: Annotated[
        Literal["none", "out_of_sync", "unhealthy", "all"],
        Field(description="Include status.resources filtered by this mode"),
    ] = "none",
    full: Full = False,
) -> str:
    """Get one application: spec, sync/health, conditions, operation, summary, resource counts.

    refresh=hard forces reconciliation and re-fetches target manifests from the repo server.
    """
    bare, ns = _resolve(ctx, name, app_namespace)
    params = _params(
        appNamespace=ns, project=project, refresh=None if refresh == "none" else refresh
    )
    data = await _get_client(ctx).get(f"/applications/{bare}", params)
    return _ok(data if full else _slim_app_detail(data, resources))


@mcp.tool(tags={"argocd", "applications", "read"}, annotations=READ)
@tool_result
async def argocd_get_resource_tree(
    ctx: Context,
    name: Name,
    app_namespace: AppNamespace = None,
    project: Project = None,
    kind: Annotated[str | None, Field(description="Client-side kind filter")] = None,
    health_status: Annotated[
        HealthStatus | None, Field(description="Client-side health filter")
    ] = None,
    include_orphaned: Annotated[bool, Field(description="Include orphaned nodes")] = False,
    include_info: Annotated[bool, Field(description="Include each node's info[] entries")] = False,
    limit: Limit = 200,
    offset: Offset = 0,
) -> str:
    """Get the live resource tree as slim nodes (kind, name, health, images, parent).

    Drops networkingInfo, uid, and resourceVersion. Filters by kind and health client-side.
    """
    bare, ns = _resolve(ctx, name, app_namespace)
    data = await _get_client(ctx).get(
        f"/applications/{bare}/resource-tree", _params(appNamespace=ns, project=project)
    )
    nodes = list(data.get("nodes") or [])
    if include_orphaned:
        nodes += data.get("orphanedNodes") or []
    if kind:
        nodes = [n for n in nodes if n.get("kind") == kind]
    if health_status:
        nodes = [n for n in nodes if (n.get("health") or {}).get("status") == health_status]
    nodes.sort(key=lambda n: (n.get("kind", ""), n.get("name", "")))
    page, total, has_more, next_offset = _page(nodes, limit, offset)
    return _paginated([_slim_node(n, include_info) for n in page], total, has_more, next_offset)


@mcp.tool(tags={"argocd", "applications", "read"}, annotations=READ)
@tool_result
async def argocd_get_managed_resources(
    ctx: Context,
    name: Name,
    app_namespace: AppNamespace = None,
    project: Project = None,
    kind: Annotated[str | None, Field(description="Filter by kind")] = None,
    resource_name: Annotated[str | None, Field(description="Filter by resource name")] = None,
    namespace: Annotated[str | None, Field(description="Filter by namespace")] = None,
    group: Annotated[str | None, Field(description="Filter by API group")] = None,
    version: Annotated[str | None, Field(description="Filter by API version")] = None,
    modified_only: Annotated[
        bool, Field(description="Only resources with a live-vs-desired diff")
    ] = True,
    include_states: Annotated[
        bool, Field(description="Include parsed target/live/predicted states")
    ] = False,
    max_diff_chars: Annotated[
        int, Field(description="Truncate each diff to this many chars", ge=0)
    ] = 4000,
) -> str:
    """Get managed resources with their live-vs-desired diffs (truncated by default).

    include_states adds the parsed target, normalized-live, and predicted-live manifests with
    managedFields stripped.
    """
    bare, ns = _resolve(ctx, name, app_namespace)
    params = _params(
        appNamespace=ns,
        project=project,
        kind=kind,
        name=resource_name,
        namespace=namespace,
        group=group,
        version=version,
    )
    data = await _get_client(ctx).get(f"/applications/{bare}/managed-resources", params)
    items = data.get("items") or []
    if modified_only:
        items = [i for i in items if i.get("modified")]
    slim = [_slim_diff(i, max_diff_chars, include_states) for i in items]
    return _ok({"items": slim, "count": len(slim)})


@mcp.tool(tags={"argocd", "applications", "read"}, annotations=READ)
@tool_result
async def argocd_get_resource(
    ctx: Context,
    name: Name,
    kind: Kind,
    resource_name: ResourceName,
    namespace: Annotated[str | None, Field(description="Resource namespace")] = None,
    group: Annotated[str, Field(description="API group ('' for core)")] = "",
    version: Annotated[str, Field(description="API version")] = "v1",
    app_namespace: AppNamespace = None,
    project: Project = None,
    full: Full = False,
) -> str:
    """Get a single managed resource's live manifest, with managedFields and the last-applied
    annotation removed."""
    bare, ns = _resolve(ctx, name, app_namespace)
    params = _params(
        appNamespace=ns,
        project=project,
        name=resource_name,
        namespace=namespace,
        group=group,
        version=version,
        kind=kind,
    )
    data = await _get_client(ctx).get(f"/applications/{bare}/resource", params)
    if full:
        return _ok(data)
    manifest = _load_json(data.get("manifest")) if data.get("manifest") else data
    manifest = _strip_managed_fields(manifest)
    if isinstance(manifest, dict):
        ann = (manifest.get("metadata") or {}).get("annotations")
        if isinstance(ann, dict):
            ann.pop(LAST_APPLIED, None)
    return _ok({"manifest": manifest})


@mcp.tool(tags={"argocd", "applications", "read"}, annotations=READ)
@tool_result
async def argocd_get_manifests(
    ctx: Context,
    name: Name,
    revision: Annotated[str | None, Field(description="Revision to render")] = None,
    app_namespace: AppNamespace = None,
    project: Project = None,
    kind: Annotated[str | None, Field(description="Client-side kind filter")] = None,
    resource_name: Annotated[str | None, Field(description="Client-side name filter")] = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> str:
    """Get the rendered desired manifests for a revision, parsed into objects and paged."""
    bare, ns = _resolve(ctx, name, app_namespace)
    params = _params(appNamespace=ns, project=project, revision=revision)
    data = await _get_client(ctx).get(f"/applications/{bare}/manifests", params)
    manifests = [_load_json(m) for m in (data.get("manifests") or [])]
    if kind:
        manifests = [m for m in manifests if isinstance(m, dict) and m.get("kind") == kind]
    if resource_name:
        manifests = [
            m
            for m in manifests
            if isinstance(m, dict) and (m.get("metadata") or {}).get("name") == resource_name
        ]
    page, total, has_more, next_offset = _page(manifests, limit, offset)
    return _ok(
        {
            "revision": data.get("revision"),
            "source_type": data.get("sourceType"),
            "namespace": data.get("namespace"),
            "server": data.get("server"),
            "manifests": page,
            "count": len(page),
            "total": total,
            "has_more": has_more,
            "next_offset": next_offset,
        }
    )


@mcp.tool(tags={"argocd", "applications", "read"}, annotations=READ)
@tool_result
async def argocd_get_application_events(
    ctx: Context,
    name: Name,
    app_namespace: AppNamespace = None,
    project: Project = None,
    resource_name: Annotated[str | None, Field(description="Filter to one resource")] = None,
    resource_namespace: Annotated[str | None, Field(description="Resource namespace")] = None,
    resource_uid: Annotated[str | None, Field(description="Resource UID")] = None,
    warnings_only: Annotated[bool, Field(description="Only Warning events")] = False,
    limit: Limit = 50,
    offset: Offset = 0,
) -> str:
    """Get Kubernetes events for an application, newest first, with messages capped at 500 chars."""
    bare, ns = _resolve(ctx, name, app_namespace)
    params = _params(
        appNamespace=ns,
        project=project,
        resourceName=resource_name,
        resourceNamespace=resource_namespace,
        resourceUID=resource_uid,
    )
    data = await _get_client(ctx).get(f"/applications/{bare}/events", params)
    items = data.get("items") or []
    if warnings_only:
        items = [e for e in items if e.get("type") == "Warning"]
    items.sort(key=lambda e: e.get("lastTimestamp") or "", reverse=True)
    page, total, has_more, next_offset = _page(items, limit, offset)
    return _paginated([_slim_event(e) for e in page], total, has_more, next_offset)


@mcp.tool(tags={"argocd", "applications", "read"}, annotations=READ)
@tool_result
async def argocd_get_pod_logs(
    ctx: Context,
    name: Name,
    pod_name: Annotated[
        str | None, Field(description="Pod name; or pass kind + resource_name")
    ] = None,
    kind: Annotated[str | None, Field(description="Owner kind, e.g. Deployment")] = None,
    group: Annotated[str | None, Field(description="Owner API group")] = None,
    resource_name: Annotated[str | None, Field(description="Owner resource name")] = None,
    namespace: Annotated[str | None, Field(description="Pod namespace")] = None,
    container: Annotated[str | None, Field(description="Container name")] = None,
    tail_lines: Annotated[
        int, Field(description="Lines from the tail (0-1000)", ge=0, le=1000)
    ] = 200,
    since_seconds: Annotated[
        int | None, Field(description="Only logs newer than N seconds", ge=0)
    ] = None,
    previous: Annotated[
        bool, Field(description="Logs from the previous container instance")
    ] = False,
    filter: Annotated[str | None, Field(description="Only lines containing this string")] = None,
    match_case: Annotated[bool, Field(description="Case-sensitive filter")] = False,
    app_namespace: AppNamespace = None,
    project: Project = None,
) -> str:
    """Get container logs (never follows). Returns lines with pod and timestamp, the pods seen, and
    a shown_lines count; stops at the stream's last marker."""
    bare, ns = _resolve(ctx, name, app_namespace)
    params = _params(
        appNamespace=ns,
        project=project,
        podName=pod_name,
        kind=kind,
        group=group,
        resourceName=resource_name,
        namespace=namespace,
        container=container,
        tailLines=tail_lines,
        sinceSeconds=since_seconds,
        previous=previous or None,
        filter=filter,
        matchCase=match_case or None,
    )
    lines: list[dict] = []
    pods: set[str] = set()
    async for obj in _get_client(ctx).stream_lines(f"/applications/{bare}/logs", params):
        r = obj.get("result", {})
        if r.get("last"):
            break
        lines.append(
            {
                "pod": r.get("podName"),
                "timestamp": r.get("timeStampStr"),
                "content": r.get("content"),
            }
        )
        if r.get("podName"):
            pods.add(r["podName"])
    return _ok(
        {
            "lines": lines,
            "shown_lines": len(lines),
            "pods": sorted(pods),
            "truncated": len(lines) >= tail_lines > 0,
        }
    )


@mcp.tool(tags={"argocd", "applications", "read"}, annotations=READ)
@tool_result
async def argocd_get_application_history(
    ctx: Context,
    name: Name,
    app_namespace: AppNamespace = None,
    project: Project = None,
    limit: Limit = 20,
    offset: Offset = 0,
) -> str:
    """Get deployment history (newest first) with revision, who deployed it, and the source."""
    bare, ns = _resolve(ctx, name, app_namespace)
    data = await _get_client(ctx).get(
        f"/applications/{bare}", _params(appNamespace=ns, project=project)
    )
    status = data.get("status", {})
    history = sorted(status.get("history", []) or [], key=lambda h: h.get("id", 0), reverse=True)
    page, total, has_more, next_offset = _page(history, limit, offset)
    return _ok(
        {
            "current_revision": status.get("sync", {}).get("revision"),
            "items": [_slim_history(h) for h in page],
            "count": len(page),
            "total": total,
            "has_more": has_more,
            "next_offset": next_offset,
        }
    )


@mcp.tool(tags={"argocd", "applications", "read"}, annotations=READ)
@tool_result
async def argocd_get_revision_metadata(
    ctx: Context,
    name: Name,
    revision: Annotated[str, Field(description="Git revision or chart version", min_length=1)],
    source_index: Annotated[
        int | None, Field(description="Source index for multi-source apps")
    ] = None,
    version_id: Annotated[int | None, Field(description="History version id")] = None,
    app_namespace: AppNamespace = None,
    project: Project = None,
) -> str:
    """Get commit metadata for a revision: author, date, message, tags, signature info."""
    bare, ns = _resolve(ctx, name, app_namespace)
    params = _params(
        appNamespace=ns, project=project, sourceIndex=source_index, versionId=version_id
    )
    data = await _get_client(ctx).get(f"/applications/{bare}/revisions/{revision}/metadata", params)
    return _ok(
        {
            "author": data.get("author"),
            "date": data.get("date"),
            "message": data.get("message"),
            "tags": data.get("tags"),
            "signature_info": data.get("signatureInfo"),
            "source_integrity": (data.get("sourceIntegrityResult") or {}).get("status")
            if isinstance(data.get("sourceIntegrityResult"), dict)
            else data.get("sourceIntegrityResult"),
        }
    )


@mcp.tool(tags={"argocd", "applications", "read"}, annotations=READ)
@tool_result
async def argocd_get_operation(
    ctx: Context,
    name: Name,
    app_namespace: AppNamespace = None,
    project: Project = None,
    include_resources: Annotated[
        bool, Field(description="Include the requested resource subset")
    ] = True,
) -> str:
    """Get the current or last sync operation: phase, who started it, and per-status result counts.

    Returns {"phase": null} when no operation has run.
    """
    bare, ns = _resolve(ctx, name, app_namespace)
    data = await _get_client(ctx).get(
        f"/applications/{bare}", _params(appNamespace=ns, project=project)
    )
    return _ok(_slim_operation(data.get("status", {}), include_resources))


@mcp.tool(tags={"argocd", "applications", "read"}, annotations=READ)
@tool_result
async def argocd_wait_for_operation(
    ctx: Context,
    name: Name,
    app_namespace: AppNamespace = None,
    project: Project = None,
    timeout_seconds: Annotated[
        int, Field(description="Give up after N seconds (10-600)", ge=10, le=600)
    ] = 120,
    poll_interval_seconds: Annotated[
        int, Field(description="Seconds between polls (1-30)", ge=1, le=30)
    ] = 3,
) -> str:
    """Poll an application until its operation reaches a terminal phase, disappears, or times out.

    Never errors on timeout; returns timed_out=true with the latest operation, sync, and health.
    """
    bare, ns = _resolve(ctx, name, app_namespace)
    params = _params(appNamespace=ns, project=project)
    op = await _poll_operation(
        _get_client(ctx), bare, params, timeout_seconds, poll_interval_seconds
    )
    return _ok(op)


@mcp.tool(tags={"argocd", "applications", "read"}, annotations=READ)
@tool_result
async def argocd_get_sync_windows(
    ctx: Context,
    name: Name,
    app_namespace: AppNamespace = None,
    project: Project = None,
) -> str:
    """Get sync windows for an application: whether it can sync now, plus active and assigned
    windows."""
    bare, ns = _resolve(ctx, name, app_namespace)
    data = await _get_client(ctx).get(
        f"/applications/{bare}/syncwindows", _params(appNamespace=ns, project=project)
    )
    return _ok(
        {
            "can_sync": data.get("canSync"),
            "active_windows": data.get("activeWindows") or [],
            "assigned_windows": [_slim_window(w) for w in data.get("assignedWindows") or []],
        }
    )


@mcp.tool(tags={"argocd", "applications", "read"}, annotations=READ)
@tool_result
async def argocd_list_resource_actions(
    ctx: Context,
    name: Name,
    kind: Kind,
    resource_name: ResourceName,
    namespace: Annotated[str | None, Field(description="Resource namespace")] = None,
    group: Annotated[str, Field(description="API group ('' for core)")] = "",
    version: Annotated[str, Field(description="API version")] = "v1",
    app_namespace: AppNamespace = None,
    project: Project = None,
) -> str:
    """List the custom resource actions available on a resource (e.g. restart, pause)."""
    bare, ns = _resolve(ctx, name, app_namespace)
    params = _params(
        appNamespace=ns,
        project=project,
        name=resource_name,
        namespace=namespace,
        group=group,
        version=version,
        kind=kind,
    )
    data = await _get_client(ctx).get(f"/applications/{bare}/resource/actions", params)
    actions = [
        {
            "name": a.get("name"),
            "displayName": a.get("displayName"),
            "disabled": a.get("disabled"),
            "params": a.get("params"),
        }
        for a in (data.get("actions") or [])
    ]
    return _ok({"actions": actions})


# ════════════════════════════════════════════════════════════════════
# 2.2 Applications — write (8)
# ════════════════════════════════════════════════════════════════════


@mcp.tool(tags={"argocd", "applications", "write"}, annotations=DESTRUCTIVE)
@tool_result(write=True)
async def argocd_sync_application(
    ctx: Context,
    name: Name,
    app_namespace: AppNamespace = None,
    project: Project = None,
    revision: Annotated[
        str | None, Field(description="Sync to this revision (needs override RBAC)")
    ] = None,
    prune: Annotated[bool, Field(description="Delete resources no longer in Git")] = False,
    dry_run: Annotated[bool, Field(description="Preview only; change nothing")] = False,
    force: Annotated[bool, Field(description="Force apply (recreate on conflict)")] = False,
    apply_out_of_sync_only: Annotated[
        bool, Field(description="Apply only out-of-sync resources")
    ] = False,
    resources: Annotated[
        list[str] | None, Field(description="Scope to [group:]kind:name[/namespace] selectors")
    ] = None,
    sync_options: Annotated[
        list[str] | None, Field(description="Free-form sync options, e.g. ServerSideApply=true")
    ] = None,
    retry_limit: Annotated[int | None, Field(description="Retry attempts on failure", ge=0)] = None,
    wait: Annotated[bool, Field(description="Wait for the operation to finish")] = False,
    timeout_seconds: Annotated[
        int, Field(description="Wait timeout in seconds", ge=10, le=600)
    ] = 120,
) -> str:
    """Sync an application. destructive because prune and force can delete or recreate resources.

    Returns the started operation; with wait=True, the final one. revision override needs the
    `applications, override` RBAC permission.
    """
    bare, ns = _resolve(ctx, name, app_namespace)
    body: dict[str, Any] = {"name": bare}
    if ns:
        body["appNamespace"] = ns
    if project:
        body["project"] = project
    if revision:
        body["revision"] = revision
    if prune:
        body["prune"] = True
    if dry_run:
        body["dryRun"] = True
    if force:
        body["strategy"] = {"apply": {"force": True}}
    opts = list(sync_options or [])
    if apply_out_of_sync_only:
        opts.append("ApplyOutOfSyncOnly=true")
    if opts:
        body["syncOptions"] = {"items": opts}
    if resources:
        body["resources"] = [_resource_selector_body(r) for r in resources]
    if retry_limit is not None:
        body["retryStrategy"] = {"limit": retry_limit}

    client = _get_client(ctx)
    resp = await client.post(f"/applications/{bare}/sync", body)
    if wait:
        return _ok(
            await _poll_operation(
                client, bare, _params(appNamespace=ns, project=project), timeout_seconds
            )
        )
    return _ok(_slim_operation((resp or {}).get("status", {})))


@mcp.tool(tags={"argocd", "applications", "write"}, annotations=DESTRUCTIVE)
@tool_result(write=True)
async def argocd_rollback_application(
    ctx: Context,
    name: Name,
    history_id: Annotated[int, Field(description="status.history id to roll back to", ge=0)],
    prune: Annotated[bool, Field(description="Prune resources during rollback")] = False,
    dry_run: Annotated[bool, Field(description="Preview only")] = False,
    app_namespace: AppNamespace = None,
    project: Project = None,
    wait: Annotated[bool, Field(description="Wait for the operation to finish")] = False,
    timeout_seconds: Annotated[
        int, Field(description="Wait timeout in seconds", ge=10, le=600)
    ] = 120,
) -> str:
    """Roll back to a prior deployment history entry. destructive.

    Argo CD refuses a rollback while auto-sync is on, so this pre-reads the app and returns an
    actionable error first. A rollback re-applies an old revision, so fix Git afterward.
    """
    bare, ns = _resolve(ctx, name, app_namespace)
    client = _get_client(ctx)
    params = _params(appNamespace=ns, project=project)
    app = await client.get(f"/applications/{bare}", params)
    if ((app.get("spec", {}).get("syncPolicy") or {}).get("automated")) is not None:
        msg = "Rollback refused: automated sync is enabled on this application"
        raise ArgoCDApiError(400, msg)
    revision = next(
        (
            h.get("revision")
            for h in app.get("status", {}).get("history", []) or []
            if h.get("id") == history_id
        ),
        None,
    )
    body: dict[str, Any] = {"name": bare, "id": history_id}
    if ns:
        body["appNamespace"] = ns
    if project:
        body["project"] = project
    if prune:
        body["prune"] = True
    if dry_run:
        body["dryRun"] = True
    resp = await client.post(f"/applications/{bare}/rollback", body)
    op = (
        await _poll_operation(client, bare, params, timeout_seconds)
        if wait
        else _slim_operation((resp or {}).get("status", {}))
    )
    op["rolled_back_to"] = {"id": history_id, "revision": revision}
    return _ok(op)


@mcp.tool(tags={"argocd", "applications", "write"}, annotations=DESTRUCTIVE_IDEMPOTENT)
@tool_result(write=True)
async def argocd_terminate_operation(
    ctx: Context,
    name: Name,
    app_namespace: AppNamespace = None,
    project: Project = None,
) -> str:
    """Terminate the running sync operation. Use to unstick a sync hanging in Running."""
    bare, ns = _resolve(ctx, name, app_namespace)
    client = _get_client(ctx)
    params = _params(appNamespace=ns, project=project)
    app = await client.get(f"/applications/{bare}", params)
    previous = _slim_operation(app.get("status", {})).get("phase")
    await client.delete(f"/applications/{bare}/operation", params)
    return _ok({"status": "terminating", "previous_phase": previous})


@mcp.tool(tags={"argocd", "applications", "write"}, annotations=WRITE)
@tool_result(write=True)
async def argocd_create_application(
    ctx: Context,
    name: Annotated[str, Field(description="New application name", min_length=1)],
    project: Annotated[str, Field(description="Project to create it in", min_length=1)],
    repo_url: Annotated[str, Field(description="Source repository URL", min_length=1)],
    path: Annotated[str | None, Field(description="Path within the repo (or use chart)")] = None,
    chart: Annotated[str | None, Field(description="Helm chart name (or use path)")] = None,
    target_revision: Annotated[str, Field(description="Branch, tag, or chart version")] = "HEAD",
    dest_server: Annotated[str | None, Field(description="Destination cluster API URL")] = None,
    dest_name: Annotated[str | None, Field(description="Destination cluster name")] = None,
    dest_namespace: Annotated[str | None, Field(description="Destination namespace")] = None,
    app_namespace: AppNamespace = None,
    helm_value_files: Annotated[
        list[str] | None, Field(description="Helm value file paths")
    ] = None,
    helm_parameters: Annotated[list[str] | None, Field(description="Helm params as k=v")] = None,
    helm_release_name: Annotated[str | None, Field(description="Helm release name")] = None,
    kustomize_images: Annotated[
        list[str] | None, Field(description="Kustomize image overrides")
    ] = None,
    labels: Annotated[dict[str, str] | None, Field(description="Application labels")] = None,
    auto_sync: Annotated[bool, Field(description="Enable automated sync")] = False,
    self_heal: Annotated[bool, Field(description="Self-heal on drift (needs auto_sync)")] = False,
    prune: Annotated[bool, Field(description="Prune on auto-sync (needs auto_sync)")] = False,
    sync_options: Annotated[list[str] | None, Field(description="Sync options list")] = None,
    create_namespace: Annotated[bool, Field(description="Add CreateNamespace=true")] = False,
    upsert: Annotated[bool, Field(description="Update if it already exists")] = False,
    validate: Annotated[bool, Field(description="Validate the manifests")] = True,
) -> str:
    """Create an application from flattened parameters. Use patch for anything this does not cover.

    Returns the created application, slim. Idempotent only with upsert=True.
    """
    source: dict[str, Any] = {"repoURL": repo_url, "targetRevision": target_revision}
    if path:
        source["path"] = path
    if chart:
        source["chart"] = chart
    helm: dict[str, Any] = {}
    if helm_value_files:
        helm["valueFiles"] = helm_value_files
    if helm_parameters:
        helm["parameters"] = [
            {"name": k, "value": v} for k, v in (p.split("=", 1) for p in helm_parameters)
        ]
    if helm_release_name:
        helm["releaseName"] = helm_release_name
    if helm:
        source["helm"] = helm
    if kustomize_images:
        source["kustomize"] = {"images": kustomize_images}

    dest: dict[str, Any] = {}
    if dest_server:
        dest["server"] = dest_server
    if dest_name:
        dest["name"] = dest_name
    if dest_namespace:
        dest["namespace"] = dest_namespace

    spec: dict[str, Any] = {"project": project, "source": source, "destination": dest}
    policy: dict[str, Any] = {}
    if auto_sync:
        policy["automated"] = {"prune": prune, "selfHeal": self_heal}
    opts = list(sync_options or [])
    if create_namespace:
        opts.append("CreateNamespace=true")
    if opts:
        policy["syncOptions"] = opts
    if policy:
        spec["syncPolicy"] = policy

    meta: dict[str, Any] = {"name": name}
    if app_namespace:
        meta["namespace"] = app_namespace
    if labels:
        meta["labels"] = labels

    params = _params(upsert=upsert or None, validate=None if validate else False)
    resp = await _get_client(ctx).post("/applications", {"metadata": meta, "spec": spec}, params)
    return _ok(_slim_app(resp or {}))


@mcp.tool(tags={"argocd", "applications", "write"}, annotations=WRITE_IDEMPOTENT)
@tool_result(write=True)
async def argocd_patch_application(
    ctx: Context,
    name: Name,
    patch: Annotated[str, Field(description="JSON patch body", min_length=1)],
    patch_type: Annotated[
        Literal["merge", "json"], Field(description="merge (RFC 7386) or json (RFC 6902)")
    ] = "merge",
    app_namespace: AppNamespace = None,
    project: Project = None,
) -> str:
    """Patch an application — the one tool for every update.

    Examples: change targetRevision with patch='{"spec":{"source":{"targetRevision":"v2"}}}';
    disable auto-sync with patch='{"spec":{"syncPolicy":{"automated":null}}}'.
    """
    bare, ns = _resolve(ctx, name, app_namespace)
    body: dict[str, Any] = {"name": bare, "patch": patch, "patchType": patch_type}
    if ns:
        body["appNamespace"] = ns
    if project:
        body["project"] = project
    resp = await _get_client(ctx).patch(f"/applications/{bare}", body)
    return _ok(_slim_app(resp or {}))


@mcp.tool(tags={"argocd", "applications", "write"}, annotations=DESTRUCTIVE)
@tool_result(write=True)
async def argocd_delete_application(
    ctx: Context,
    name: Name,
    cascade: Annotated[bool, Field(description="Also delete the app's resources")] = True,
    propagation_policy: Annotated[
        Literal["foreground", "background", "orphan"],
        Field(description="Kubernetes deletion propagation policy"),
    ] = "foreground",
    app_namespace: AppNamespace = None,
    project: Project = None,
) -> str:
    """Delete an application. destructive. cascade=False removes only the Application object."""
    bare, ns = _resolve(ctx, name, app_namespace)
    params = _params(
        cascade=cascade,
        propagationPolicy=propagation_policy,
        appNamespace=ns,
        project=project,
    )
    await _get_client(ctx).delete(f"/applications/{bare}", params)
    return _ok({"status": "deleted", "name": name, "cascade": cascade})


@mcp.tool(tags={"argocd", "applications", "write"}, annotations=DESTRUCTIVE)
@tool_result(write=True)
async def argocd_run_resource_action(
    ctx: Context,
    name: Name,
    action: Annotated[str, Field(description="Action name, e.g. restart", min_length=1)],
    kind: Kind,
    resource_name: ResourceName,
    namespace: Annotated[str | None, Field(description="Resource namespace")] = None,
    group: Annotated[str, Field(description="API group ('' for core)")] = "",
    version: Annotated[str, Field(description="API version")] = "v1",
    parameters: Annotated[dict[str, str] | None, Field(description="Action parameters")] = None,
    app_namespace: AppNamespace = None,
    project: Project = None,
) -> str:
    """Run a custom resource action (restart a Deployment, pause a Rollout). destructive."""
    bare, ns = _resolve(ctx, name, app_namespace)
    params = _params(
        appNamespace=ns,
        project=project,
        namespace=namespace,
        resourceName=resource_name,
        version=version,
        group=group,
        kind=kind,
        action=action,
    )
    body = [{"name": k, "value": v} for k, v in (parameters or {}).items()] or None
    await _get_client(ctx).post(f"/applications/{bare}/resource/actions/v2", body, params=params)
    return _ok(
        {
            "status": "ran",
            "action": action,
            "resource": {
                "group": group,
                "kind": kind,
                "name": resource_name,
                "namespace": namespace,
            },
        }
    )


@mcp.tool(tags={"argocd", "applications", "write"}, annotations=DESTRUCTIVE)
@tool_result(write=True)
async def argocd_delete_resource(
    ctx: Context,
    name: Name,
    kind: Kind,
    resource_name: ResourceName,
    namespace: Annotated[str | None, Field(description="Resource namespace")] = None,
    group: Annotated[str, Field(description="API group ('' for core)")] = "",
    version: Annotated[str, Field(description="API version")] = "v1",
    force: Annotated[bool, Field(description="Force delete")] = False,
    orphan: Annotated[bool, Field(description="Orphan dependents")] = False,
    app_namespace: AppNamespace = None,
    project: Project = None,
) -> str:
    """Delete a single managed resource so the controller recreates it. destructive."""
    bare, ns = _resolve(ctx, name, app_namespace)
    params = _params(
        appNamespace=ns,
        project=project,
        name=resource_name,
        namespace=namespace,
        version=version,
        group=group,
        kind=kind,
        force=force or None,
        orphan=orphan or None,
    )
    await _get_client(ctx).delete(f"/applications/{bare}/resource", params)
    return _ok(
        {
            "status": "deleted",
            "resource": {
                "group": group,
                "kind": kind,
                "name": resource_name,
                "namespace": namespace,
            },
        }
    )


# ════════════════════════════════════════════════════════════════════
# Slim helpers for sets, projects, clusters, repositories
# ════════════════════════════════════════════════════════════════════

GENERATOR_KINDS = (
    "git",
    "list",
    "clusters",
    "matrix",
    "merge",
    "scmProvider",
    "pullRequest",
    "plugin",
    "clusterDecisionResource",
    "oci",
)
CRED_FIELDS = (
    "password",
    "sshPrivateKey",
    "tlsClientCertData",
    "tlsClientCertKey",
    "githubAppPrivateKey",
    "bearerToken",
    "gcpServiceAccountKey",
    "azureServicePrincipalClientSecret",
)


def _slim_appset(a: dict) -> dict:
    meta = a.get("metadata", {})
    spec = a.get("spec", {})
    status = a.get("status", {})
    gens = spec.get("generators") or []
    kinds = sorted({k for g in gens for k in g if k in GENERATOR_KINDS})
    resources = status.get("resources") or []
    health = None
    if resources:
        health = (
            "Healthy"
            if all((r.get("health") or {}).get("status") == "Healthy" for r in resources)
            else "Degraded"
        )
    return {
        "name": meta.get("name"),
        "namespace": meta.get("namespace"),
        "project": (spec.get("template", {}).get("spec", {}) or {}).get("project"),
        "generators": kinds,
        "go_template": spec.get("goTemplate"),
        "strategy": (spec.get("strategy") or {}).get("type"),
        "health_status": health,
        "resources_count": len(resources),
        "conditions": [
            {
                "type": c.get("type"),
                "status": c.get("status"),
                "reason": c.get("reason"),
                "message": (c.get("message") or "")[:200],
            }
            for c in status.get("conditions", []) or []
        ],
    }


def _slim_appset_detail(
    a: dict, include_applications: bool = True, include_template: bool = False
) -> dict:
    meta = a.get("metadata", {})
    spec = a.get("spec", {})
    status = a.get("status", {})
    slim_spec: dict[str, Any] = {
        "generators": spec.get("generators"),
        "goTemplate": spec.get("goTemplate"),
        "strategy": spec.get("strategy"),
        "syncPolicy": spec.get("syncPolicy"),
    }
    if include_template:
        slim_spec["template"] = spec.get("template")
    slim_status: dict[str, Any] = {
        "conditions": [
            _slim(c, ("type", "status", "reason", "message"))
            for c in status.get("conditions", []) or []
        ]
    }
    if include_applications:
        slim_status["applicationStatus"] = [
            _slim(s, ("application", "status", "step", "message"))
            for s in status.get("applicationStatus", []) or []
        ]
        slim_status["resources"] = [
            {
                **_slim(r, ("name", "namespace", "status")),
                "health": _slim(r.get("health", {}), ("status", "message")),
            }
            for r in status.get("resources", []) or []
        ]
    return {
        "metadata": _slim(meta, ("name", "namespace", "labels", "annotations")),
        "spec": slim_spec,
        "status": slim_status,
    }


def _slim_generated_app(app: dict) -> dict:
    meta = app.get("metadata", {})
    spec = app.get("spec", {})
    out = {
        "name": meta.get("name"),
        "namespace": meta.get("namespace"),
        "project": spec.get("project"),
        "destination": _slim_dest(spec.get("destination", {})),
    }
    out.update(_slim_sources(spec))
    return out


def _slim_project_list(p: dict) -> dict:
    meta = p.get("metadata", {})
    spec = p.get("spec", {})
    repos = spec.get("sourceRepos") or []
    return {
        "name": meta.get("name"),
        "description": spec.get("description"),
        "source_repos": {"count": len(repos), "first": repos[:5]},
        "destinations": len(spec.get("destinations") or []),
        "roles": [r.get("name") for r in spec.get("roles") or []],
        "sync_windows": len(spec.get("syncWindows") or []),
        "orphaned_resources_warn": (spec.get("orphanedResources") or {}).get("warn"),
    }


def _slim_project_detail(p: dict, include_roles: bool = True) -> dict:
    spec = p.get("spec", {})
    out = _slim(
        spec,
        (
            "description",
            "sourceRepos",
            "sourceNamespaces",
            "destinations",
            "clusterResourceWhitelist",
            "clusterResourceBlacklist",
            "namespaceResourceWhitelist",
            "namespaceResourceBlacklist",
            "syncWindows",
            "orphanedResources",
        ),
    )
    if include_roles:
        out["roles"] = [
            {
                "name": r.get("name"),
                "description": r.get("description"),
                "policies": r.get("policies"),
                "groups": r.get("groups"),
                "token_count": len(r.get("jwtTokens") or []),
            }
            for r in spec.get("roles") or []
        ]
    return {"spec": out}


def _slim_cluster(c: dict) -> dict:
    info = c.get("info", {})
    cache = info.get("cacheInfo", {})
    return {
        "name": c.get("name"),
        "server": c.get("server"),
        "project": c.get("project"),
        "labels": c.get("labels"),
        "namespaces": c.get("namespaces"),
        "connection": _slim(c.get("connectionState", {}), ("status", "message", "attemptedAt")),
        "server_version": info.get("serverVersion"),
        "applications_count": info.get("applicationsCount"),
        "cache": _slim(cache, ("resourcesCount", "apisCount", "lastCacheSyncTime")),
        "shard": c.get("shard"),
    }


def _slim_cluster_detail(c: dict) -> dict:
    out = _slim_cluster(c)
    out["annotations"] = c.get("annotations")
    out["clusterResources"] = c.get("clusterResources")
    return out


def _slim_repo(r: dict) -> dict:
    return {
        "repo": r.get("repo"),
        "name": r.get("name"),
        "type": r.get("type"),
        "project": r.get("project"),
        "connection": _slim(r.get("connectionState", {}), ("status", "message", "attemptedAt")),
        "insecure": r.get("insecure"),
        "enable_lfs": r.get("enableLfs"),
        "enable_oci": r.get("enableOCI"),
        "inherited_creds": r.get("inheritedCreds"),
        "has_credentials": any(r.get(k) for k in CRED_FIELDS),
    }


def _cluster_path(cluster: str) -> tuple[str, dict]:
    """(url-encoded id.value, query) for a cluster addressed by name or server URL."""
    value = quote(cluster, safe="")
    id_type = None if cluster.startswith("http") else "name"
    return value, _params(**{"id.type": id_type})


# ════════════════════════════════════════════════════════════════════
# 2.3 ApplicationSets (3)
# ════════════════════════════════════════════════════════════════════


@mcp.tool(tags={"argocd", "applicationsets", "read"}, annotations=READ)
@tool_result
async def argocd_list_applicationsets(
    ctx: Context,
    projects: Annotated[list[str] | None, Field(description="Filter by project names")] = None,
    selector: Annotated[str | None, Field(description="Label selector")] = None,
    appset_namespace: Annotated[str | None, Field(description="ApplicationSet namespace")] = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> str:
    """List ApplicationSets with slim rows (generators present, strategy, health, conditions)."""
    params = _params(projects=projects, selector=selector, appsetNamespace=appset_namespace)
    data = await _get_client(ctx).get("/applicationsets", params)
    items = sorted(data.get("items") or [], key=lambda a: a.get("metadata", {}).get("name", ""))
    page, total, has_more, next_offset = _page(items, limit, offset)
    return _paginated([_slim_appset(a) for a in page], total, has_more, next_offset)


@mcp.tool(tags={"argocd", "applicationsets", "read"}, annotations=READ)
@tool_result
async def argocd_get_applicationset(
    ctx: Context,
    name: Annotated[str, Field(description="ApplicationSet name", min_length=1)],
    appset_namespace: Annotated[str | None, Field(description="ApplicationSet namespace")] = None,
    include_applications: Annotated[
        bool, Field(description="Include generated app statuses")
    ] = True,
    include_template: Annotated[bool, Field(description="Include the full spec.template")] = False,
    full: Full = False,
) -> str:
    """Get one ApplicationSet: generators, strategy, and the status of the apps it generates."""
    params = _params(appsetNamespace=appset_namespace)
    data = await _get_client(ctx).get(f"/applicationsets/{name}", params)
    return _ok(data if full else _slim_appset_detail(data, include_applications, include_template))


@mcp.tool(tags={"argocd", "applicationsets", "read"}, annotations=READ)
@tool_result
async def argocd_generate_applicationset(
    ctx: Context,
    applicationset: Annotated[str, Field(description="Full ApplicationSet manifest as JSON")],
    appset_namespace: Annotated[str | None, Field(description="ApplicationSet namespace")] = None,
) -> str:
    """Dry-run the generators: preview the applications an ApplicationSet would produce. Creates
    nothing. The manifest must be JSON."""
    try:
        manifest = json.loads(applicationset)
    except json.JSONDecodeError as e:
        msg = f"applicationset must be valid JSON: {e}"
        raise ArgoCDApiError(400, msg) from e
    body: dict[str, Any] = {"applicationSet": manifest}
    if appset_namespace:
        body["appsetNamespace"] = appset_namespace
    data = await _get_client(ctx).post("/applicationsets/generate", body)
    apps = [_slim_generated_app(a) for a in (data or {}).get("applications") or []]
    return _ok({"applications": apps, "count": len(apps)})


# ════════════════════════════════════════════════════════════════════
# 2.4 Projects (2)
# ════════════════════════════════════════════════════════════════════


@mcp.tool(tags={"argocd", "projects", "read"}, annotations=READ)
@tool_result
async def argocd_list_projects(
    ctx: Context,
    name: Annotated[str | None, Field(description="Filter to one project name")] = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> str:
    """List projects with slim rows (repo/destination counts, role names, sync-window count)."""
    data = await _get_client(ctx).get("/projects", _params(name=name))
    items = sorted(data.get("items") or [], key=lambda p: p.get("metadata", {}).get("name", ""))
    page, total, has_more, next_offset = _page(items, limit, offset)
    return _ok_scrubbed(
        {
            "items": [_slim_project_list(p) for p in page],
            "count": len(page),
            "total": total,
            "has_more": has_more,
            "next_offset": next_offset,
        }
    )


@mcp.tool(tags={"argocd", "projects", "read"}, annotations=READ)
@tool_result
async def argocd_get_project(
    ctx: Context,
    name: Annotated[str, Field(description="Project name", min_length=1)],
    include_roles: Annotated[
        bool, Field(description="Include role definitions (never jwtTokens)")
    ] = True,
    full: Full = False,
) -> str:
    """Get a project: allowed repos, destinations, resource whitelists/blacklists, and roles.

    Role token values (jwtTokens) are never returned; only a token count.
    """
    data = await _get_client(ctx).get(f"/projects/{name}")
    return _ok_scrubbed(data if full else _slim_project_detail(data, include_roles))


# ════════════════════════════════════════════════════════════════════
# 2.5 Clusters (3)
# ════════════════════════════════════════════════════════════════════


@mcp.tool(tags={"argocd", "clusters", "read"}, annotations=READ)
@tool_result
async def argocd_list_clusters(
    ctx: Context,
    name: Annotated[str | None, Field(description="Filter by cluster name")] = None,
    server: Annotated[str | None, Field(description="Filter by server URL")] = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> str:
    """List clusters with slim rows (connection state, server version, app and cache counts).

    The cluster credential config is never returned.
    """
    data = await _get_client(ctx).get("/clusters")
    items = data.get("items") or []
    if name:
        items = [c for c in items if c.get("name") == name]
    if server:
        items = [c for c in items if c.get("server") == server]
    items = sorted(items, key=lambda c: c.get("name") or c.get("server") or "")
    page, total, has_more, next_offset = _page(items, limit, offset)
    return _ok_scrubbed(
        {
            "items": [_slim_cluster(c) for c in page],
            "count": len(page),
            "total": total,
            "has_more": has_more,
            "next_offset": next_offset,
        }
    )


@mcp.tool(tags={"argocd", "clusters", "read"}, annotations=READ)
@tool_result
async def argocd_get_cluster(
    ctx: Context,
    cluster: Annotated[str, Field(description="Cluster name, or server URL (starts with http)")],
    full: Full = False,
) -> str:
    """Get one cluster by name or server URL. The credential config is always removed, even with
    full=True."""
    value, query = _cluster_path(cluster)
    data = await _get_client(ctx).get(f"/clusters/{value}", query)
    return _ok_scrubbed(data if full else _slim_cluster_detail(data))


@mcp.tool(tags={"argocd", "clusters", "write"}, annotations=WRITE_IDEMPOTENT)
@tool_result(write=True)
async def argocd_invalidate_cluster_cache(
    ctx: Context,
    cluster: Annotated[str, Field(description="Cluster name, or server URL (starts with http)")],
) -> str:
    """Invalidate a cluster's cached resources — the standard fix for phantom OutOfSync or Unknown
    health. Removes nothing real."""
    value, query = _cluster_path(cluster)
    data = await _get_client(ctx).post(f"/clusters/{value}/invalidate-cache", params=query)
    cache = ((data or {}).get("info") or {}).get("cacheInfo")
    return _ok({"status": "invalidated", "cluster": cluster, "cache": cache})


# ════════════════════════════════════════════════════════════════════
# 2.6 Repositories (3)
# ════════════════════════════════════════════════════════════════════


@mcp.tool(tags={"argocd", "repositories", "read"}, annotations=READ)
@tool_result
async def argocd_list_repositories(
    ctx: Context,
    repo: Annotated[str | None, Field(description="Filter by repo URL")] = None,
    app_project: Annotated[str | None, Field(description="Filter by project")] = None,
    force_refresh: Annotated[bool, Field(description="Re-check connection state")] = False,
    limit: Limit = 50,
    offset: Offset = 0,
) -> str:
    """List repositories with slim rows and connection state. Credentials are never returned;
    has_credentials reports whether any are configured."""
    params = _params(appProject=app_project, forceRefresh=force_refresh or None)
    data = await _get_client(ctx).get("/repositories", params)
    items = data.get("items") or []
    if repo:
        items = [r for r in items if r.get("repo") == repo]
    items = sorted(items, key=lambda r: r.get("repo") or "")
    page, total, has_more, next_offset = _page(items, limit, offset)
    return _ok_scrubbed(
        {
            "items": [_slim_repo(r) for r in page],
            "count": len(page),
            "total": total,
            "has_more": has_more,
            "next_offset": next_offset,
        }
    )


@mcp.tool(tags={"argocd", "repositories", "read"}, annotations=READ)
@tool_result
async def argocd_get_repository_refs(
    ctx: Context,
    repo: Annotated[str, Field(description="Repository URL", min_length=1)],
    app_project: Annotated[str | None, Field(description="Project scope")] = None,
    force_refresh: Annotated[bool, Field(description="Bypass the cache")] = False,
    limit: Annotated[
        int, Field(description="Max branches/tags per list (1-500)", ge=1, le=500)
    ] = 100,
    offset: Offset = 0,
) -> str:
    """Get a repository's branches and tags, each paged separately with its own counts."""
    params = _params(appProject=app_project, forceRefresh=force_refresh or None)
    data = await _get_client(ctx).get(f"/repositories/{quote(repo, safe='')}/refs", params)

    def paged(values: list) -> dict:
        page, total, has_more, next_offset = _page(values, limit, offset)
        return {
            "items": page,
            "count": len(page),
            "total": total,
            "has_more": has_more,
            "next_offset": next_offset,
        }

    return _ok(
        {"branches": paged(data.get("branches") or []), "tags": paged(data.get("tags") or [])}
    )


@mcp.tool(tags={"argocd", "repositories", "read"}, annotations=READ)
@tool_result
async def argocd_list_repository_apps(
    ctx: Context,
    repo: Annotated[str, Field(description="Repository URL", min_length=1)],
    revision: Annotated[str | None, Field(description="Revision to inspect")] = None,
    app_project: Annotated[str | None, Field(description="Project scope")] = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> str:
    """List the application paths (and their config type) discoverable in a repository."""
    params = _params(revision=revision, appProject=app_project)
    data = await _get_client(ctx).get(f"/repositories/{quote(repo, safe='')}/apps", params)
    items = [_slim(i, ("path", "type")) for i in data.get("items") or []]
    page, total, has_more, next_offset = _page(items, limit, offset)
    return _paginated(page, total, has_more, next_offset)


# ════════════════════════════════════════════════════════════════════
# 2.7 Server and account (4)
# ════════════════════════════════════════════════════════════════════


@mcp.tool(tags={"argocd", "server", "read"}, annotations=READ)
@tool_result
async def argocd_get_version(ctx: Context) -> str:
    """Get the Argo CD server version and its bundled tool versions. The connectivity probe."""
    data = await _get_client(ctx).get_version()
    return _ok(
        {
            "version": data.get("Version"),
            "git_tag": data.get("GitTag"),
            "git_commit": data.get("GitCommit"),
            "build_date": data.get("BuildDate"),
            "kustomize_version": data.get("KustomizeVersion"),
            "helm_version": data.get("HelmVersion"),
            "kubectl_version": data.get("KubectlVersion"),
            "go_version": data.get("GoVersion"),
            "platform": data.get("Platform"),
        }
    )


@mcp.tool(tags={"argocd", "server", "read"}, annotations=READ)
@tool_result
async def argocd_get_userinfo(ctx: Context) -> str:
    """Get the authenticated identity: username, groups, whether logged in, and the token issuer."""
    data = await _get_client(ctx).get("/session/userinfo")
    return _ok(
        {
            "username": data.get("username"),
            "groups": data.get("groups"),
            "logged_in": data.get("loggedIn"),
            "issuer": data.get("iss"),
        }
    )


@mcp.tool(tags={"argocd", "server", "read"}, annotations=READ)
@tool_result
async def argocd_can_i(
    ctx: Context,
    resource: Annotated[
        Literal[
            "applications",
            "applicationsets",
            "clusters",
            "projects",
            "repositories",
            "accounts",
            "certificates",
            "gpgkeys",
            "logs",
            "exec",
            "extensions",
        ],
        Field(description="RBAC resource"),
    ],
    action: Annotated[
        Literal[
            "get", "create", "update", "delete", "sync", "rollback", "action", "override", "invoke"
        ],
        Field(description="RBAC action"),
    ],
    subresource: Annotated[str, Field(description="Sub-resource, e.g. */* or proj/app")] = "*/*",
) -> str:
    """Check whether the current account may perform resource/action on a subresource.

    Returns {allowed: bool, ...}; the API answers the string yes/no.
    """
    data = await _get_client(ctx).get(f"/account/can-i/{resource}/{action}/{subresource}")
    value = data.get("value") if isinstance(data, dict) else data
    return _ok(
        {
            "allowed": value == "yes",
            "resource": resource,
            "action": action,
            "subresource": subresource,
        }
    )


@mcp.tool(tags={"argocd", "server", "read"}, annotations=READ)
@tool_result
async def argocd_get_settings(ctx: Context, full: Full = False) -> str:
    """Get server settings: URL, enabled features, tracking method, and plugin names.

    Drops OIDC/Dex config and UI banner fields in the slim view.
    """
    data = await _get_client(ctx).get("/settings")
    if full:
        return _ok_scrubbed(data)
    overrides = data.get("resourceOverrides") or {}
    return _ok(
        {
            "url": data.get("url"),
            "apps_in_any_namespace_enabled": data.get("appsInAnyNamespaceEnabled"),
            "exec_enabled": data.get("execEnabled"),
            "tracking_method": data.get("trackingMethod"),
            "kustomize_versions": data.get("kustomizeVersions"),
            "plugins": [p.get("name") for p in data.get("plugins") or []],
            "impersonation_enabled": data.get("impersonationEnabled"),
            "hydrator_enabled": data.get("hydratorEnabled"),
            "status_badge_enabled": data.get("statusBadgeEnabled"),
            "sync_with_replace_allowed": data.get("syncWithReplaceAllowed"),
            "resource_overrides": sorted(overrides.keys()),
        }
    )
