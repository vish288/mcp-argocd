"""Shared test fixtures for mcp-argocd."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
import respx
from fastmcp import Client, FastMCP

from mcp_argocd.client import ArgoCDClient
from mcp_argocd.config import ArgoCDConfig
from mcp_argocd.servers.argocd import mcp

TEST_URL = "https://argocd.example.com"
TEST_TOKEN = "test-token"
API_BASE = f"{TEST_URL}/api/v1"

_FIXTURES = Path(__file__).resolve().parent / "fixtures"


def load_fixture(name: str) -> Any:
    """Read a JSON fixture from tests/fixtures/."""
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
async def client() -> AsyncIterator[ArgoCDClient]:
    """A bare ArgoCDClient for client-level tests, closed after the test."""
    ac = ArgoCDClient(ArgoCDConfig(url=TEST_URL, token=TEST_TOKEN))
    yield ac
    await ac.close()


@pytest.fixture
def mock_api() -> Iterator[respx.MockRouter]:
    with respx.mock(base_url=TEST_URL, assert_all_called=False) as router:
        yield router


@asynccontextmanager
async def _tool_client(*, read_only: bool) -> AsyncIterator[tuple[Client, respx.MockRouter]]:
    """The real ``mcp`` server with its lifespan swapped for one that hands tools
    an ArgoCDClient pointed at a respx-mocked API. The original lifespan is
    restored in ``finally`` so a failure cannot leak the mock into later tests.
    """
    config = ArgoCDConfig(url=TEST_URL, token=TEST_TOKEN, read_only=read_only)
    ac = ArgoCDClient(config)

    @asynccontextmanager
    async def mock_lifespan(server: FastMCP) -> AsyncIterator[dict[str, Any]]:
        try:
            yield {"client": ac, "config": config}
        finally:
            await ac.close()

    original = mcp._lifespan
    mcp._lifespan = mock_lifespan
    try:
        with respx.mock(base_url=TEST_URL, assert_all_called=False) as router:
            async with Client(mcp) as mcp_client:
                yield mcp_client, router
    finally:
        mcp._lifespan = original


@pytest.fixture
async def tool_client() -> AsyncIterator[tuple[Client, respx.MockRouter]]:
    """(FastMCP client, respx router) against the server in read-write mode."""
    async with _tool_client(read_only=False) as pair:
        yield pair


@pytest.fixture
async def readonly_client() -> AsyncIterator[tuple[Client, respx.MockRouter]]:
    """(FastMCP client, respx router) against the server in read-only mode."""
    async with _tool_client(read_only=True) as pair:
        yield pair
