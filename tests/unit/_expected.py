"""Single source of the registered-object counts.

``test_server_assembly`` ties these to the ``@mcp.*`` decorator count scanned from
``src/`` and ``test_doc_parity`` ties them to the counts printed in the docs, so the
three numbers live here once instead of being retyped in every file that checks them.
"""

from __future__ import annotations

EXPECTED_TOOLS = 37
EXPECTED_RESOURCES = 7
EXPECTED_PROMPTS = 7
