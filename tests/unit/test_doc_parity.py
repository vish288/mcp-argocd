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

from ._expected import EXPECTED_PROMPTS, EXPECTED_RESOURCES, EXPECTED_TOOLS

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


_TAGLINE = f"{EXPECTED_TOOLS} tools, {EXPECTED_RESOURCES} resources, and {EXPECTED_PROMPTS} prompts"


def test_readme_counts():
    assert f"## Tools ({EXPECTED_TOOLS})" in README
    assert f"## Resources ({EXPECTED_RESOURCES})" in README
    assert f"## Prompts ({EXPECTED_PROMPTS})" in README


def test_agents_counts():
    assert _TAGLINE in AGENTS
    assert f"## Tool Categories ({EXPECTED_TOOLS})" in AGENTS
    total = sum(int(m) for m in re.findall(r"^\|[^|]+\|\s*(\d+)\s*\|", AGENTS, re.M))
    assert total == EXPECTED_TOOLS


def test_llms_full_counts():
    assert _TAGLINE in LLMS_FULL
    assert f"## Tools ({EXPECTED_TOOLS})" in LLMS_FULL
    category_sum = sum(int(n) for n in re.findall(r"^### .+ \((\d+)\)$", LLMS_FULL, re.M))
    assert category_sum == EXPECTED_TOOLS


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
