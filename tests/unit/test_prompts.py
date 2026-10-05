"""Tests for MCP prompt registration and content."""

from __future__ import annotations

from pathlib import Path

import pytest

from mcp_argocd.servers._helpers import _load_file
from mcp_argocd.servers.prompts import (
    _PROMPTS_DIR,
    diagnose_sync_failure,
    fleet_status,
    inspect_applicationset,
    review_drift,
    rollback_application,
    safe_sync,
    triage_application,
)

EXPECTED_PROMPTS = {
    "triage_application": {
        "fn": triage_application,
        "file": "triage-application.md",
        "args": {"name": "guestbook"},
    },
    "diagnose_sync_failure": {
        "fn": diagnose_sync_failure,
        "file": "diagnose-sync-failure.md",
        "args": {"name": "guestbook"},
    },
    "review_drift": {"fn": review_drift, "file": "review-drift.md", "args": {"project": "default"}},
    "safe_sync": {
        "fn": safe_sync,
        "file": "safe-sync.md",
        "args": {"name": "guestbook", "prune": "true"},
    },
    "rollback_application": {
        "fn": rollback_application,
        "file": "rollback-application.md",
        "args": {"name": "guestbook", "history_id": "2"},
    },
    "fleet_status": {"fn": fleet_status, "file": "fleet-status.md", "args": {"project": "default"}},
    "inspect_applicationset": {
        "fn": inspect_applicationset,
        "file": "inspect-applicationset.md",
        "args": {"name": "guestbooks"},
    },
}

PROMPT_FILES = [info["file"] for info in EXPECTED_PROMPTS.values()]


def test_prompt_count():
    assert len(EXPECTED_PROMPTS) == 7


def test_all_files_exist_and_start_with_heading():
    for filename in PROMPT_FILES:
        path = Path(_PROMPTS_DIR) / filename
        assert path.is_file(), filename
        content = _load_file(_PROMPTS_DIR, filename)
        assert content.lstrip().startswith("#"), filename
        assert len(content) > 100, filename


def test_each_prompt_returns_two_messages():
    for name, info in EXPECTED_PROMPTS.items():
        result = info["fn"](**info["args"])
        assert isinstance(result, list) and len(result) == 2, name
        assert result[0].role == "user", name
        assert result[1].role == "assistant", name


def test_args_interpolated():
    result = triage_application(name="payments")
    assert "payments" in result[0].content.text
    assert "payments" in result[1].content.text


def test_namespace_name_split():
    result = triage_application(name="team-a/guestbook")
    text = result[0].content.text
    assert "guestbook" in text
    assert "team-a" in text


def test_rollback_prompt_mentions_auto_sync_check():
    text = rollback_application(name="guestbook", history_id="2")[0].content.text
    assert "auto-sync" in text.lower() or "auto_sync" in text.lower()


def test_curly_braces_in_args_do_not_crash():
    result = safe_sync(name="guestbook", prune='{"nested": "x"}')
    assert '{"nested": "x"}' in result[0].content.text


def test_prompt_functions_have_metadata():
    for name, info in EXPECTED_PROMPTS.items():
        assert hasattr(info["fn"], "__fastmcp__"), name


@pytest.mark.asyncio
async def test_prompts_listed_by_server():
    from mcp_argocd.servers.argocd import mcp

    names = {p.name for p in await mcp.list_prompts()}
    assert set(EXPECTED_PROMPTS) <= names
