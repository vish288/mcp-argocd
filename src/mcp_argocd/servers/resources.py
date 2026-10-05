"""MCP resources for Argo CD — curated rules and guides for GitOps operations."""

from __future__ import annotations

from pathlib import Path

from ._helpers import _load_file
from .argocd import mcp

_RESOURCES_DIR = str(Path(__file__).resolve().parent.parent / "resources")


def _load(filename: str) -> str:
    """Load a resource markdown file from the resources directory."""
    return _load_file(_RESOURCES_DIR, filename)


# ════════════════════════════════════════════════════════════════════
# Rules
# ════════════════════════════════════════════════════════════════════


@mcp.resource(
    "resource://rules/sync-safety",
    name="Sync Safety Rules",
    description="Dry-run first, prune/force, sync options, sync windows, revision override",
    mime_type="text/markdown",
    tags={"rule", "argocd", "sync"},
)
def sync_safety_rules() -> str:
    """Rules for syncing an application without causing an outage."""
    return _load("sync-safety.md")


@mcp.resource(
    "resource://rules/rollback",
    name="Rollback Rules",
    description="History limits, auto-sync blocking rollback, RBAC, fixing Git after a rollback",
    mime_type="text/markdown",
    tags={"rule", "argocd", "rollback"},
)
def rollback_rules() -> str:
    """Rules for rolling an application back to a prior deployment."""
    return _load("rollback.md")


@mcp.resource(
    "resource://rules/gitops-change-flow",
    name="GitOps Change Flow",
    description="Git as source of truth, selfHeal, justified live fixes, never delete to fix drift",
    mime_type="text/markdown",
    tags={"rule", "argocd", "gitops"},
)
def gitops_change_flow_rules() -> str:
    """How to make changes the GitOps way."""
    return _load("gitops-change-flow.md")


@mcp.resource(
    "resource://rules/applicationsets",
    name="ApplicationSet Rules",
    description="Set owns generated apps; preservedFields, strategy, generate preview, finalizer",
    mime_type="text/markdown",
    tags={"rule", "argocd", "applicationset"},
)
def applicationset_rules() -> str:
    """Rules for working with ApplicationSets and their generated applications."""
    return _load("applicationsets.md")


# ════════════════════════════════════════════════════════════════════
# Guides
# ════════════════════════════════════════════════════════════════════


@mcp.resource(
    "resource://guides/status-triage",
    name="Status Triage Guide",
    description="Sync x health matrix, condition types, and how to investigate an unhealthy app",
    mime_type="text/markdown",
    tags={"guide", "argocd", "triage"},
)
def status_triage_guide() -> str:
    """A guide for diagnosing unhealthy or out-of-sync applications."""
    return _load("status-triage.md")


@mcp.resource(
    "resource://guides/rbac",
    name="RBAC and Permission Errors",
    description="Resource/action model, fine-grained sub-resources, logs RBAC, reading 403s",
    mime_type="text/markdown",
    tags={"guide", "argocd", "rbac"},
)
def rbac_guide() -> str:
    """A guide to Argo CD RBAC and reading permission-denied errors."""
    return _load("rbac.md")


@mcp.resource(
    "resource://guides/api-token-setup",
    name="API Token Setup",
    description="Local apiKey accounts, generate-token, project role tokens, read-only policy",
    mime_type="text/markdown",
    tags={"guide", "argocd", "auth"},
)
def api_token_setup_guide() -> str:
    """How to create a token for headless Argo CD access."""
    return _load("api-token-setup.md")
