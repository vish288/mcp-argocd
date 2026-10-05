"""Tests for MCP resource registration and content."""

from __future__ import annotations

from pathlib import Path

import pytest

from mcp_argocd.servers.resources import (
    _RESOURCES_DIR,
    _load,
    api_token_setup_guide,
    applicationset_rules,
    gitops_change_flow_rules,
    rbac_guide,
    rollback_rules,
    status_triage_guide,
    sync_safety_rules,
)

EXPECTED_RESOURCES = {
    "resource://rules/sync-safety": {"fn": sync_safety_rules, "file": "sync-safety.md"},
    "resource://rules/rollback": {"fn": rollback_rules, "file": "rollback.md"},
    "resource://rules/gitops-change-flow": {
        "fn": gitops_change_flow_rules,
        "file": "gitops-change-flow.md",
    },
    "resource://rules/applicationsets": {"fn": applicationset_rules, "file": "applicationsets.md"},
    "resource://guides/status-triage": {"fn": status_triage_guide, "file": "status-triage.md"},
    "resource://guides/rbac": {"fn": rbac_guide, "file": "rbac.md"},
    "resource://guides/api-token-setup": {
        "fn": api_token_setup_guide,
        "file": "api-token-setup.md",
    },
}

RESOURCE_FILES = [info["file"] for info in EXPECTED_RESOURCES.values()]


def test_resource_count():
    assert len(EXPECTED_RESOURCES) == 7


def test_all_files_exist():
    for filename in RESOURCE_FILES:
        assert (Path(_RESOURCES_DIR) / filename).is_file(), filename


def test_content_starts_with_heading_and_is_substantial():
    for filename in RESOURCE_FILES:
        content = _load(filename)
        assert content.lstrip().startswith("#"), filename
        assert len(content) > 100, filename
        assert '"""' not in content, filename


def test_each_resource_returns_its_file():
    for info in EXPECTED_RESOURCES.values():
        assert info["fn"]() == _load(info["file"])


def test_resource_functions_have_metadata():
    for uri, info in EXPECTED_RESOURCES.items():
        assert hasattr(info["fn"], "__fastmcp__"), uri


def test_rejects_traversal():
    with pytest.raises(ValueError, match="Invalid filename"):
        _load("../../../etc/passwd")
