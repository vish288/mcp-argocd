"""Boot the server through its real lifespan and check what it registers.

The decorator count is read from ``src/`` so a tool that silently drops out of the
registry fails here rather than in a client.
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path

from fastmcp import Client

from mcp_argocd.servers.argocd import mcp

SRC = Path(__file__).resolve().parents[2] / "src"

# Grows with the build: 14 (T4) -> 22 (T5) -> 37 (T6). Resources/prompts land in T7.
EXPECTED_TOOLS = 37
EXPECTED_RESOURCES = 7
EXPECTED_PROMPTS = 7

_NAME_RE = re.compile(r"^argocd_[a-z]+(_[a-z]+)+$")


def _decorators(kind: str) -> int:
    pattern = re.compile(rf"^@mcp\.{kind}\b", re.M)
    return sum(len(pattern.findall(p.read_text())) for p in SRC.rglob("*.py"))


def _set_env(monkeypatch) -> None:
    monkeypatch.setenv("ARGOCD_URL", "https://argocd.example.com")
    monkeypatch.setenv("ARGOCD_TOKEN", "test-token")
    monkeypatch.delenv("ARGOCD_READ_ONLY", raising=False)
    for module in ("mcp_argocd.servers.resources", "mcp_argocd.servers.prompts"):
        importlib.import_module(module)


async def test_real_lifespan_registers_everything(monkeypatch):
    _set_env(monkeypatch)
    async with Client(mcp) as client:
        tools = await client.list_tools()
        assert len(tools) == _decorators("tool") == EXPECTED_TOOLS
        resources = await client.list_resources()
        assert len(resources) == _decorators("resource") == EXPECTED_RESOURCES
        assert len(await client.list_prompts()) == _decorators("prompt") == EXPECTED_PROMPTS
        for resource in resources:
            (content,) = await client.read_resource(resource.uri)
            assert content.text.lstrip().startswith("#"), resource.uri


async def test_tool_naming_and_annotations(monkeypatch):
    _set_env(monkeypatch)
    for tool in await mcp._list_tools():
        assert _NAME_RE.match(tool.name), tool.name
        ann = tool.annotations
        assert ann is not None and ann.read_only_hint is not None, tool.name
        if ann.read_only_hint:
            assert "read" in tool.tags, tool.name
        else:
            assert "write" in tool.tags, tool.name
            assert ann.destructive_hint is not None, tool.name


async def test_2026_07_28_support(monkeypatch):
    _set_env(monkeypatch)
    async with Client(mcp, mode="2026-07-28") as client:
        assert len(await client.list_tools()) == EXPECTED_TOOLS
