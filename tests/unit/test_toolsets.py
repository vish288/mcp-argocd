"""MCP_TOOLSETS: coarse tool groups (workbench-backlog#160).

``MCP_TOOLSETS`` (or ``--toolsets``) selects one or more named groups of the raw domain
tags the tools already carry, switching ``tools/list`` to an allowlist of just those tools
so a client with a tool budget sees fewer. Resources and prompts stay listed regardless,
and ``MCP_READ_ONLY`` still applies after the selection and only ever removes tools. Unset
or ``all`` lists every tool. An unknown group name exits 2 with the valid list.

Each case boots a real ``Client(mcp)`` so the env → lifespan → ``_apply_toolsets`` wiring is
exercised, and restores the module-level server's visibility transforms afterwards.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from click.testing import CliRunner
from fastmcp import Client

from mcp_argocd import main
from mcp_argocd.servers import prompts, resources  # noqa: F401 — registers decorators
from mcp_argocd.servers.argocd import (
    TOOLSET_GROUPS,
    UnknownToolsetError,
    mcp,
    parse_toolsets,
)

# Non-domain tags every tool carries; the remainder is its one domain tag.
_FRAME_TAGS = {"argocd", "read", "write"}

# Per group: (tools listed, tools listed under read-only). Verified against the server.
_EXPECTED = {
    "apps": (22, 14),
    "appsets": (3, 3),
    "admin": (12, 11),
}
_ALL_TOOLS = 37
_RESOURCES = 7
_PROMPTS = 7


async def _boot(
    monkeypatch, toolsets: str | None, *, read_only: bool = False
) -> tuple[set[str], int, int]:
    """Boot ``mcp`` through a real client with the given MCP_TOOLSETS / MCP_READ_ONLY env and
    return (tool names, resource count, prompt count), restoring visibility afterwards."""
    monkeypatch.setenv("ARGOCD_URL", "https://x")
    monkeypatch.setenv("ARGOCD_TOKEN", "t")
    monkeypatch.delenv("ARGOCD_READ_ONLY", raising=False)
    monkeypatch.delenv("MCP_READ_ONLY", raising=False)
    monkeypatch.delenv("MCP_TOOLSETS", raising=False)
    if toolsets is not None:
        monkeypatch.setenv("MCP_TOOLSETS", toolsets)
    if read_only:
        monkeypatch.setenv("MCP_READ_ONLY", "true")
    tx = len(mcp._transforms)
    instructions = mcp.instructions
    try:
        async with Client(mcp) as client:
            tools = {t.name for t in await client.list_tools()}
            res = len(await client.list_resources())
            prm = len(await client.list_prompts())
        return tools, res, prm
    finally:
        del mcp._transforms[tx:]
        mcp.instructions = instructions


# ── the group map ──────────────────────────────────────────────────────


async def test_every_domain_tag_belongs_to_exactly_one_group():
    """Each tool's one domain tag is covered by exactly one group, and no group names a tag
    no tool carries — so the map and the decorators cannot drift apart."""
    all_tools = await mcp._list_tools()
    domain_tags = {t for tool in all_tools for t in (set(tool.tags) - _FRAME_TAGS)}
    for tool in all_tools:
        assert len(set(tool.tags) - _FRAME_TAGS) == 1, tool.name
    group_tags = [tag for tags in TOOLSET_GROUPS.values() for tag in tags]
    assert len(group_tags) == len(set(group_tags)), "a tag appears in two groups"
    assert set(group_tags) == domain_tags


# ── selection ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("group", sorted(_EXPECTED))
async def test_group_lists_expected_tools(monkeypatch, group):
    tools, res, prm = await _boot(monkeypatch, group)
    assert len(tools) == _EXPECTED[group][0]
    assert (res, prm) == (_RESOURCES, _PROMPTS)


@pytest.mark.parametrize("raw", [None, "", "  ", "all", "apps,all"])
async def test_unset_or_all_lists_everything(monkeypatch, raw):
    tools, res, prm = await _boot(monkeypatch, raw)
    assert len(tools) == _ALL_TOOLS
    assert (res, prm) == (_RESOURCES, _PROMPTS)


async def test_multiple_groups_union(monkeypatch):
    tools, _, _ = await _boot(monkeypatch, "apps,appsets")
    assert len(tools) == _EXPECTED["apps"][0] + _EXPECTED["appsets"][0]


# ── read-only still wins after the allowlist ──────────────────────────────


@pytest.mark.parametrize("group", sorted(_EXPECTED))
async def test_group_plus_read_only_drops_writes(monkeypatch, group):
    all_tools = await mcp._list_tools()
    write_names = {t.name for t in all_tools if "write" in t.tags}
    tools, _, _ = await _boot(monkeypatch, group, read_only=True)
    assert len(tools) == _EXPECTED[group][1]
    assert not (tools & write_names)


# ── parsing / validation ──────────────────────────────────────────────────


@pytest.mark.parametrize("raw", [None, "", "   ", "all", "ALL", " apps , all "])
def test_parse_none_means_no_filter(raw):
    assert parse_toolsets(raw) is None


def test_parse_trims_and_lowercases():
    assert parse_toolsets(" Apps , APPSETS ") == set(TOOLSET_GROUPS["apps"]) | set(
        TOOLSET_GROUPS["appsets"]
    )


def test_parse_unknown_raises_with_valid_list():
    with pytest.raises(UnknownToolsetError) as exc:
        parse_toolsets("apps,bogus")
    message = str(exc.value)
    assert "bogus" in message
    for name in [*TOOLSET_GROUPS, "all"]:
        assert name in message


def test_cli_unknown_toolset_exits_2():
    # main() writes MCP_TOOLSETS into os.environ; patch.dict restores it so the bad value
    # does not leak into later tests (CliRunner's env= would not).
    with patch.dict("os.environ", {"ARGOCD_URL": "https://x", "ARGOCD_TOKEN": "t"}, clear=False):
        result = CliRunner().invoke(main, ["--toolsets", "bogus"])
    assert result.exit_code == 2
    assert "bogus" in result.output
    assert "apps" in result.output
