"""Locked read-only: the deployer-set MCP_READ_ONLY switch.

A truthy MCP_READ_ONLY or the per-server ARGOCD_READ_ONLY alias (or --read-only,
which writes the alias) locks the server read-only for the process. The lock hides
every write tool and write-workflow prompt from tools/list and prompts/list, so a
client cannot call or re-enable them; the ``_check_write`` guard stays as defence
in depth. A clearly falsy value never unlocks; an unrecognised value fails safe to
locked. This file imports ``prompts`` (via conftest) so its prompt assertions are not
order-dependent on another module registering them first.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner
from fastmcp import Client

from mcp_argocd import main
from mcp_argocd.config import ArgoCDConfig
from mcp_argocd.exceptions import ArgoCDWriteDisabledError
from mcp_argocd.servers.argocd import _check_write, _err, _lock_read_only, mcp

from ._read_only import write_decorated_names
from .test_tool_contract import WRITE_ROWS


def _clear_read_only(monkeypatch) -> None:
    monkeypatch.delenv("MCP_READ_ONLY", raising=False)
    monkeypatch.delenv("ARGOCD_READ_ONLY", raising=False)


@pytest.fixture
def lock_mcp():
    """Lock the shared ``mcp`` in-process, yielding the ``_lock_read_only`` caller.

    Restores visibility (truncate the appended transforms) and instructions afterwards so
    the module-level ``mcp`` is clean for other tests — the lock keys its idempotency off
    the restored instructions sentence, so there is no separate flag to reset.
    """
    tx = len(mcp._transforms)
    instructions = mcp.instructions

    def lock(**kwargs) -> object:
        _lock_read_only(mcp, **kwargs)
        return mcp

    try:
        yield lock
    finally:
        del mcp._transforms[tx:]
        mcp.instructions = instructions


# ── config: OR semantics, strip, unrecognised → lock (OD-8) ───────────


def test_mcp_read_only_env_locks(monkeypatch):
    _clear_read_only(monkeypatch)
    monkeypatch.setenv("MCP_READ_ONLY", "true")
    assert ArgoCDConfig.from_env().read_only is True


def test_alias_false_cannot_unlock(monkeypatch, caplog):
    _clear_read_only(monkeypatch)
    monkeypatch.setenv("MCP_READ_ONLY", "true")
    monkeypatch.setenv("ARGOCD_READ_ONLY", "false")
    with caplog.at_level(logging.WARNING, logger="mcp_argocd.config"):
        config = ArgoCDConfig.from_env()
    assert config.read_only is True
    assert any("ARGOCD_READ_ONLY" in r.message for r in caplog.records)


def test_mcp_false_cannot_unlock_alias(monkeypatch, caplog):
    _clear_read_only(monkeypatch)
    monkeypatch.setenv("ARGOCD_READ_ONLY", "true")
    monkeypatch.setenv("MCP_READ_ONLY", "false")
    with caplog.at_level(logging.WARNING, logger="mcp_argocd.config"):
        config = ArgoCDConfig.from_env()
    assert config.read_only is True
    assert any("MCP_READ_ONLY" in r.message for r in caplog.records)


def test_no_falsy_warning_when_unlocked(monkeypatch, caplog):
    _clear_read_only(monkeypatch)
    monkeypatch.setenv("MCP_READ_ONLY", "false")
    with caplog.at_level(logging.WARNING, logger="mcp_argocd.config"):
        config = ArgoCDConfig.from_env()
    assert config.read_only is False
    assert caplog.records == []


# (value, expected read_only) — stripped and case-insensitive (OD-8).
_RO_VALUES = [
    ("true", True),
    ("1", True),
    ("yes", True),
    ("on", True),
    ("TRUE", True),
    ("  true  ", True),  # stripped
    ("false", False),
    ("0", False),
    ("no", False),
    ("off", False),
    ("", False),  # explicitly empty is the default
    ("  ", False),  # whitespace strips to empty
    ("maybe", True),  # unrecognised non-empty fails safe to locked
    ("2", True),
    ("enabled", True),
]


@pytest.mark.parametrize(
    ("value", "expected"), _RO_VALUES, ids=[v[0] or "empty" for v in _RO_VALUES]
)
def test_read_only_value_table(monkeypatch, value, expected):
    _clear_read_only(monkeypatch)
    monkeypatch.setenv("MCP_READ_ONLY", value)
    assert ArgoCDConfig.from_env().read_only is expected


def test_unrecognised_value_locks_with_warning(monkeypatch, caplog):
    _clear_read_only(monkeypatch)
    monkeypatch.setenv("MCP_READ_ONLY", "maybe")
    with caplog.at_level(logging.WARNING, logger="mcp_argocd.config"):
        config = ArgoCDConfig.from_env()
    assert config.read_only is True
    assert any(
        "unrecognised" in r.message and "MCP_READ_ONLY" in r.message and "maybe" in r.message
        for r in caplog.records
    )


def test_no_read_only_flag_rejected():
    """`--no-read-only` does not exist (plain is_flag); click rejects it, exit 2."""
    result = CliRunner().invoke(main, ["--no-read-only"])
    assert result.exit_code == 2
    assert "no such option" in result.output.lower()


# ── lock: real lifespan boot ──────────────────────────────────────────


async def test_real_lifespan_boot_locks(monkeypatch):
    """The real lifespan (not the mocked tool_client) locks when MCP_READ_ONLY=true.

    Booting through a real Client runs ``lifespan`` → ``_lock_read_only``: every write tool
    is absent from the listing and the instructions gain the locked sentence. Snapshot and
    restore ``mcp._transforms`` + ``instructions`` so the boot does not leak the lock into
    later tests that share the module-level ``mcp``.
    """
    monkeypatch.setenv("ARGOCD_URL", "https://argocd.example.com")
    monkeypatch.setenv("ARGOCD_TOKEN", "test-token")
    monkeypatch.delenv("ARGOCD_READ_ONLY", raising=False)
    monkeypatch.setenv("MCP_READ_ONLY", "true")
    tx = len(mcp._transforms)
    instructions = mcp.instructions
    try:
        async with Client(mcp) as client:
            visible = {t.name for t in await client.list_tools()}
            assert not (visible & write_decorated_names())
            assert "locked read-only" in (mcp.instructions or "")
    finally:
        del mcp._transforms[tx:]
        mcp.instructions = instructions


# ── lock: hide write tools/prompts ────────────────────────────────────


async def test_locked_lists_no_write_tools(readonly_client):
    client, _router = readonly_client
    tools = await client.list_tools()
    assert all(t.annotations is None or t.annotations.read_only_hint is not False for t in tools)
    assert not ({t.name for t in tools} & write_decorated_names())


async def test_hidden_set_equals_write_tag_set_equals_write_decorator_set(readonly_client):
    # `mcp._list_tools()` is the raw registry: the underscore method skips visibility, while
    # server-side `mcp.list_tools()` and Clients apply it. So `_list_tools()` gives the
    # universe and the tags; the locked client gives what survives the hide.
    all_tools = await mcp._list_tools()
    tag_set = {t.name for t in all_tools if "write" in t.tags}
    decorator_set = write_decorated_names()
    assert tag_set == decorator_set

    client, _router = readonly_client
    visible = {t.name for t in await client.list_tools()}
    hidden = {t.name for t in all_tools} - visible
    assert hidden == tag_set


async def test_locked_hides_write_workflow_prompts(readonly_client):
    client, _router = readonly_client
    names = {p.name for p in await client.list_prompts()}
    assert "safe_sync" not in names
    assert "rollback_application" not in names
    assert "triage_application" in names  # read-only workflow stays


async def test_call_hidden_write_tool_is_unknown_tool(readonly_client):
    client, _router = readonly_client
    result = await client.call_tool(
        "argocd_sync_application", {"name": "guestbook"}, raise_on_error=False
    )
    assert result.is_error is True
    assert "unknown tool" in result.content[0].text.lower()


# ── lock: decorator guard (defence in depth) ──────────────────────────


@pytest.mark.parametrize("row", WRITE_ROWS, ids=[r[0] for r in WRITE_ROWS])
async def test_decorator_guard_refuses_every_write_tool(row):
    """Defence in depth: even reaching a hidden write tool's ``fn`` directly (a cached
    client list, a future untagged tool) refuses before any mutation. Call the registered
    ``tool.fn`` — the ``@tool_result(write=True)`` wrapper — with a stub ctx carrying a
    read-only config and assert the refusal envelope and its deployer-owned hint.
    """
    name, args, *_ = row
    tool = {t.name: t for t in await mcp._list_tools()}[name]
    ctx = SimpleNamespace(
        lifespan_context={"config": ArgoCDConfig(url="u", token="t", read_only=True)}
    )

    result = await tool.fn(ctx, **args)

    assert result.is_error is True
    body = result.content[0].text
    assert "read-only" in body
    assert "deployer" in body.lower()


def test_check_write_raises_and_hint_names_read_only():
    """The guard's two halves directly: ``_check_write`` raises under a read-only config and
    ``_err`` renders the deployer-owned hint. (The parametrized test proves the wired path.)
    """
    ctx = SimpleNamespace(
        lifespan_context={"config": ArgoCDConfig(url="u", token="t", read_only=True)}
    )
    with pytest.raises(ArgoCDWriteDisabledError):
        _check_write(ctx)
    assert "read-only" in _err(ArgoCDWriteDisabledError())["hint"]


# ── lock: instructions, startup log, and the src invariant ────────────


@pytest.mark.parametrize("source", ["MCP_READ_ONLY", "ARGOCD_READ_ONLY", "--read-only"])
def test_locked_instructions_and_log(lock_mcp, caplog, source):
    """The startup line names each truthy source verbatim — the shared env vars and the
    ``--read-only`` flag — so a deployer reading the log sees exactly what engaged the lock.
    """
    with caplog.at_level(logging.INFO, logger="mcp_argocd.servers.argocd"):
        server = lock_mcp(sources=[source])
    assert "locked read-only" in (server.instructions or "")
    assert any("LOCKED" in r.message and source in r.message for r in caplog.records)


def test_src_never_re_enables_or_mutates_visibility():
    """``src/`` must not re-enable components, touch the transform list, or flip the config
    flag off after config is final: none of the shared-pattern regexes (``.enable(``,
    ``enable_components(``, ``disable_components(``, ``reset_visibility(``, ``add_transform(``,
    ``._transforms``, ``read_only = False``). The lock is one ``disable`` at boot and nothing
    undoes it.
    """
    banned = (
        r"\.enable\(",
        r"enable_components\(",
        r"disable_components\(",
        r"reset_visibility\(",
        r"add_transform\(",
        r"\._transforms",
        r"read_only\s*=\s*False",
    )
    src_dir = Path(__file__).resolve().parents[2] / "src"
    for path in src_dir.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for pattern in banned:
            assert not re.search(pattern, text), f"{pattern} found in {path}"
