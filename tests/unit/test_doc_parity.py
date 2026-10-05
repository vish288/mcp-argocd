"""Docs must stay in lockstep with the registered tools, resources, and prompts.

Parses the counts and tool names out of README.md, AGENTS.md, llms-full.txt, and server.json and
checks them against what the server actually registers.
"""

from __future__ import annotations

import importlib
import json
import re
from pathlib import Path

import pytest

from mcp_argocd.servers.argocd import mcp

ROOT = Path(__file__).resolve().parents[2]
README = (ROOT / "README.md").read_text()
AGENTS = (ROOT / "AGENTS.md").read_text()
LLMS_FULL = (ROOT / "llms-full.txt").read_text()
SERVER = json.loads((ROOT / "server.json").read_text())

TOOL_RE = re.compile(r"argocd_[a-z]+(?:_[a-z]+)+")


@pytest.fixture(scope="module")
async def registered():
    for module in ("mcp_argocd.servers.resources", "mcp_argocd.servers.prompts"):
        importlib.import_module(module)
    tools = {t.name for t in await mcp._list_tools()}
    resources = {str(r.uri) for r in (await mcp._list_resources())}
    prompts = {p.name for p in (await mcp._list_prompts())}
    return tools, resources, prompts


def test_readme_counts():
    assert "## Tools (37)" in README
    assert "## Resources (7)" in README
    assert "## Prompts (7)" in README


def test_agents_counts():
    assert "37 tools, 7 resources, and 7 prompts" in AGENTS
    assert "## Tool Categories (37)" in AGENTS
    total = sum(int(m) for m in re.findall(r"^\|[^|]+\|\s*(\d+)\s*\|", AGENTS, re.M))
    assert total == 37


def test_llms_full_counts():
    assert "37 tools, 7 resources, and 7 prompts" in LLMS_FULL
    assert "## Tools (37)" in LLMS_FULL
    category_sum = sum(int(n) for n in re.findall(r"^### .+ \((\d+)\)$", LLMS_FULL, re.M))
    assert category_sum == 37


def test_server_json_description():
    assert SERVER["description"]
    assert len(SERVER["description"]) <= 100


async def test_every_registered_tool_is_documented(registered):
    tools, _resources, _prompts = registered
    for name in tools:
        assert name in README, f"{name} missing from README"
        assert name in LLMS_FULL, f"{name} missing from llms-full.txt"


async def test_no_undocumented_tool_name_in_docs(registered):
    tools, _resources, _prompts = registered
    for doc_name, text in (("README", README), ("llms-full", LLMS_FULL)):
        for match in TOOL_RE.findall(text):
            assert match in tools, f"{doc_name} references unknown tool {match}"


async def test_resources_and_prompts_documented(registered):
    _tools, resources, prompts = registered
    for uri in resources:
        assert uri in README and uri in LLMS_FULL, uri
    for name in prompts:
        assert name in README and name in LLMS_FULL, name
