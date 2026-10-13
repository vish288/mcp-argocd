"""Shared helpers for the read-only tests.

The write set is derived from the ``@tool_result(write=True)`` guard in source — its
most direct definition — so it cannot drift from a hand-kept list. Both the contract
suite and the lock suite read it from here instead of each keeping a copy.
"""

from __future__ import annotations

import re
from pathlib import Path

_ARGOCD_SRC = Path(__file__).resolve().parents[2] / "src" / "mcp_argocd" / "servers" / "argocd.py"


def write_decorated_names() -> set[str]:
    """Tool names wrapped by ``@tool_result(write=True)``."""
    src = _ARGOCD_SRC.read_text(encoding="utf-8")
    return set(re.findall(r"@tool_result\(write=True\)\s*\nasync def (\w+)\(", src))
