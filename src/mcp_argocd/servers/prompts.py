"""MCP prompts for Argo CD — multi-tool workflow templates."""

from __future__ import annotations

from pathlib import Path
from string import Template

from fastmcp.prompts import Message

from ._helpers import _load_file, _split_app_name
from .argocd import mcp

_PROMPTS_DIR = str(Path(__file__).resolve().parent.parent / "resources" / "prompts")


def _render(filename: str, **kwargs: str) -> str:
    """Load a prompt template and substitute variables safely.

    Uses string.Template ($var) so parameter values containing braces cannot raise.
    """
    return Template(_load_file(_PROMPTS_DIR, filename)).safe_substitute(kwargs)


def _messages(text: str, ack: str) -> list[Message]:
    return [Message(role="user", content=text), Message(role="assistant", content=ack)]


@mcp.prompt(tags={"argocd", "triage"})
def triage_application(name: str, app_namespace: str = "") -> list[Message]:
    """Diagnose why an application is Degraded or OutOfSync: conditions, tree, events, logs.

    Accepts namespace/name for app_namespace extraction.
    """
    bare, ns = _split_app_name(name, app_namespace or None)
    text = _render("triage-application.md", name=bare, app_namespace=ns or "")
    return _messages(
        text,
        f"I'll triage application {bare}. Let me start with its conditions and last operation.",
    )


@mcp.prompt(tags={"argocd", "sync"})
def diagnose_sync_failure(name: str) -> list[Message]:
    """Find out why the last sync failed: failed resources, events, manifests, classification."""
    bare, _ = _split_app_name(name)
    text = _render("diagnose-sync-failure.md", name=bare)
    return _messages(
        text,
        f"I'll diagnose the sync failure on {bare}. Let me read the last operation's failures.",
    )


@mcp.prompt(tags={"argocd", "drift"})
def review_drift(project: str = "", name: str = "") -> list[Message]:
    """Review OutOfSync apps and their diffs; recommend sync, a Git change, or an ignore rule."""
    text = _render("review-drift.md", project=project, name=name)
    scope = f" in project {project}" if project else ""
    return _messages(
        text,
        f"I'll review drift{scope}. Let me list the OutOfSync applications first.",
    )


@mcp.prompt(tags={"argocd", "sync"})
def safe_sync(name: str, prune: str = "false") -> list[Message]:
    """Sync an application safely: check sync windows, dry-run, review, then sync and wait."""
    bare, _ = _split_app_name(name)
    text = _render("safe-sync.md", name=bare, prune=prune)
    return _messages(
        text,
        f"I'll sync {bare} safely. Let me check sync windows and run a dry-run first.",
    )


@mcp.prompt(tags={"argocd", "rollback"})
def rollback_application(name: str, history_id: str = "") -> list[Message]:
    """Roll back an app: pick history, confirm the commit, check auto-sync, then verify."""
    bare, _ = _split_app_name(name)
    text = _render("rollback-application.md", name=bare, history_id=history_id)
    return _messages(
        text,
        f"I'll roll back {bare}. First I'll read its history and check whether auto-sync is on, "
        "which would block the rollback.",
    )


@mcp.prompt(tags={"argocd", "fleet"})
def fleet_status(project: str = "", destination: str = "") -> list[Message]:
    """Report fleet health: clusters, connection state, and unhealthy or unsynced apps."""
    text = _render("fleet-status.md", project=project, destination=destination)
    return _messages(
        text,
        "I'll report fleet status. Let me list the clusters and their connection state first.",
    )


@mcp.prompt(tags={"argocd", "applicationset"})
def inspect_applicationset(name: str, appset_namespace: str = "") -> list[Message]:
    """Inspect an ApplicationSet: its generated apps, a dry-run generate, and a diff vs existing."""
    text = _render("inspect-applicationset.md", name=name, appset_namespace=appset_namespace)
    return _messages(
        text,
        f"I'll inspect ApplicationSet {name}. Let me read its generated app statuses first.",
    )
