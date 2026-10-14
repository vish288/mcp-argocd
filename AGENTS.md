# mcp-argocd — Agent Context

MCP server exposing 37 tools, 7 resources, and 7 prompts over the Argo CD REST API. Read-mostly
operations for Argo CD 3.x: application status, sync, rollback, drift, logs, ApplicationSets,
clusters, projects, and repositories. Needs an API token; no cluster access.

## Protocol Support

Supports the MCP 2026-07-28 specification (MCP 2.0) and stays compatible with 2025-11-25 clients.
Built on FastMCP 4.x and the MCP Python SDK 2.x. A regression test lists all 37 tools with an
in-memory MCP client pinned to 2026-07-28.

Transports: `stdio` (default), `streamable-http` (recommended for remote), and `sse`. The
2026-07-28 specification deprecates `sse`, so the server prints a warning when you use it. The
server uses no roots, sampling, logging, elicitation, or resource subscriptions, so the 2026-07-28
deprecations do not affect it.

## Architecture

- **Entry point**: `src/mcp_argocd/__init__.py` — click CLI, loads `.env`, runs the FastMCP server
- **Client**: `src/mcp_argocd/client.py` — async httpx client; path-prefix-safe `{url}/api/v1` base with `/api/version` requested absolutely; grpc-gateway error mapping; `stream_lines` for logs
- **Tools**: `src/mcp_argocd/servers/argocd.py` — all 37 FastMCP tool registrations and the `_slim_*` functions
- **Resources**: `src/mcp_argocd/servers/resources.py` — 7 MCP resources (4 `resource://rules/*`, 3 `resource://guides/*`), content in `src/mcp_argocd/resources/*.md`
- **Prompts**: `src/mcp_argocd/servers/prompts.py` — 7 MCP prompts (multi-tool workflows)
- **Helpers**: `src/mcp_argocd/servers/_helpers.py` — file loader with traversal guard, `_scrub` over one `SECRET_KEYS` tuple, `_page`, `_split_app_name`, `_parse_resource_selector`, `_truncate`, `_terminal`
- **Config**: `src/mcp_argocd/config.py` — `ArgoCDConfig` built from env vars
- **Exceptions**: `src/mcp_argocd/exceptions.py` — `ArgoCDError` base; `ArgoCDApiError` (with `grpc_code`), `ArgoCDAuthError`, `ArgoCDNotFoundError`, `ArgoCDConflictError`, `ArgoCDWriteDisabledError`, `ArgoCDTimeoutError`, `ArgoCDConnectionError`
- **Tests**: `tests/` — `unit/test_tools.py`, `test_tool_contract.py`, `test_client.py`, `test_config.py`, `test_exceptions.py`, `test_helpers.py`, `test_server_assembly.py`, `test_resources.py`, `test_prompts.py`, `test_doc_parity.py`, `test_derived_artifacts.py`, plus `tests/test_links.py`. Fixtures in `tests/fixtures/` and shared fixtures in `tests/conftest.py`

## Development

```bash
uv sync --all-extras
uv run pytest --cov
uv run ruff check .
uv run ruff format --check .   # CI runs --check; formatting drift fails the build
```

`pytest` uses `asyncio_mode = "auto"` — async tests need no marker. Ruff line length is 100,
target py310; lint rules include `S` (bandit), `EM`, `N`, `UP`. `tests/**` waives `S101`,
`S105`, `S106`.

## Patterns

- All tools are `async def` returning a `ToolResult`: `_ok`/`_ok_scrubbed`/`_paginated` wrap a JSON object in pretty text plus the same object as `structuredContent`. Tools advertise no `outputSchema` (payloads pass upstream fields through untyped); a typed schema is opt-in per tool via `output_schema=`
- `_ok(data)` for success, `_err(e)` for failure, `_paginated(items, total, has_more, next_offset)` for lists; `_ok_scrubbed` for repository/cluster/project payloads
- `_slim_*` functions trim each payload type; `get_*` tools expose `full=True` for the raw payload
- Write access control: `@tool_result(write=True)` runs `_check_write(ctx)`, which raises `ArgoCDWriteDisabledError` when read-only; in read-only the write tools are also hidden from `tools/list` by `_lock_read_only` (see Read-only mode)
- Client-side paging: Argo CD list endpoints take no paging parameters, so the server fetches once, filters, sorts by name, and slices
- `namespace/name` handling: `_split_app_name` splits apps-in-any-namespace names into `name` + `appNamespace`
- Tags: every tool tagged `{"argocd", "<category>", "read"|"write"}`
- Parameters use `Annotated[type, Field(description=...)]`; `Literal[...]` for closed sets

## MCP Compliance Rules

### Tool annotations (mandatory)
Every tool MUST have `annotations={}` with at minimum `readOnlyHint`.
- Read tools: `annotations={"readOnlyHint": True, "idempotentHint": True, "openWorldHint": True}`
- Non-destructive writes: `destructiveHint: False`, `readOnlyHint: False`
- Destructive writes: `destructiveHint: True`, `readOnlyHint: False`
- Idempotent writes: add `idempotentHint: True`

### Tool descriptions
1-2 sentences. Front-load what it does AND what it returns.

### Error handling
- Every tool wraps in `@tool_result`; expected `ArgoCDError` failures become an `isError: true` result carrying the `_err` JSON body as text content only — no `structuredContent`, because strict MCP clients (the TypeScript SDK) validate `structuredContent` against the tool's `outputSchema` even when `isError` is true. Anything else surfaces as a `ToolError` with a logged traceback.
- Error text MUST be actionable: what went wrong plus a suggested fix.
- Never expose the token, stack traces, or request headers.

### Parameter design
- `Annotated[type, Field(description="...")]` on every parameter; a default on every optional.
- `Literal[...]` for known value sets; flatten — no nested dicts unless necessary.

### Read-only mode
A truthy `MCP_READ_ONLY` (shared across the mcp-* servers) or the `ARGOCD_READ_ONLY` alias, or
`--read-only`, locks the server read-only for the process: `Config.from_env` ORs the sources (any
truthy one wins; a falsy value never unlocks and logs one WARNING), and the lifespan calls
`_lock_read_only(server)`, which `server.disable(tags={"write"})` so write tools and write-workflow
prompts drop out of `tools/list`/`prompts/list` and `tools/call` on them returns "Unknown tool". The
lock is computed once and never cleared; `src/` never calls `enable`/`enable_components`. Every write
tool still calls `_check_write(ctx)` (via `@tool_result(write=True)`) as defence in depth.

## Naming verbs

`list`, `get`, `sync`, `rollback`, `terminate`, `create`, `patch`, `delete`, `run`, `invalidate`,
`generate`, `wait`, `can`.

## Tool Categories (37)

| Category | Count | Operations |
|---|---|---|
| Applications — read | 14 | list, get, resource-tree, managed-resources, resource, manifests, events, logs, history, revision-metadata, operation, wait, sync-windows, resource-actions |
| Applications — write | 8 | sync, rollback, terminate, create, patch, delete, run action, delete resource |
| ApplicationSets | 3 | list, get, generate (dry-run) |
| Projects | 2 | list, get |
| Clusters | 3 | list, get, invalidate cache |
| Repositories | 3 | list, refs, apps |
| Server and account | 4 | version, userinfo, can-i, settings |

## Common Workflows

- **Status triage**: `argocd_get_application` → `argocd_get_resource_tree` → `argocd_get_application_events` → `argocd_get_pod_logs`
- **Diagnose sync failure**: `argocd_get_operation` → `argocd_get_application_events` → `argocd_get_managed_resources`
- **Review drift**: `argocd_list_applications(sync_status=OutOfSync)` → `argocd_get_managed_resources`
- **Safe sync**: `argocd_get_sync_windows` → `argocd_sync_application(dry_run=True)` → `argocd_sync_application(wait=True)` → `argocd_get_application`
- **Rollback**: `argocd_get_application_history` → `argocd_get_revision_metadata` → `argocd_rollback_application(wait=True)`
- **Fleet status**: `argocd_list_clusters` → `argocd_list_applications`
- **Inspect ApplicationSet**: `argocd_get_applicationset` → `argocd_generate_applicationset`

## Prompts

Prompt content lives as `.md` files in `src/mcp_argocd/resources/prompts/`. `servers/prompts.py`
loads each via `string.Template.safe_substitute` and registers it with `@mcp.prompt()`. Each
returns a user message (workflow template) plus an assistant acknowledgment.

| Prompt | Purpose | Tags |
|---|---|---|
| `triage_application` | Diagnose a Degraded/OutOfSync app | argocd, triage |
| `diagnose_sync_failure` | Classify a sync failure | argocd, sync |
| `review_drift` | Review OutOfSync apps and diffs | argocd, drift |
| `safe_sync` | Dry-run then sync and wait | argocd, sync |
| `rollback_application` | Roll back with the auto-sync check | argocd, rollback |
| `fleet_status` | Cluster and app health across a fleet | argocd, fleet |
| `inspect_applicationset` | Compare generated vs existing apps | argocd, applicationset |

## Environment Variables

| Variable | Required | Default | Notes |
|---|---|---|---|
| `ARGOCD_URL` | yes | — | Base URL including any path prefix; trailing slash stripped; `/api/v1` appended. Falls back to `ARGOCD_SERVER` |
| `ARGOCD_TOKEN` | yes | — | Bearer token; also reads `ARGOCD_AUTH_TOKEN`, `ARGOCD_API_TOKEN` |
| `MCP_READ_ONLY` | no | `false` | Shared name across the mcp-* servers. Any value other than `false`/`0`/`no`/`off`/empty locks the server read-only: the 9 write tools and write-workflow prompts are not listed, and nothing can turn it back off at runtime |
| `ARGOCD_READ_ONLY` | no | `false` | Per-server alias for `MCP_READ_ONLY`; either one locks the server read-only. `--read-only` can only raise the lock |
| `ARGOCD_HTTP_BEARER_TOKEN` | no | — | Bearer token (min 16 chars) required to bind an HTTP transport to a non-loopback host; clients send `Authorization: Bearer` |
| `ARGOCD_TIMEOUT` | no | `30` | Request timeout in seconds |
| `ARGOCD_SSL_VERIFY` | no | `true` | `false`/`0`/`no` skips verification; `ARGOCD_INSECURE=true` is an alias |
| `ARGOCD_APP_NAMESPACE` | no | — | Default `appNamespace` for apps-in-any-namespace installs |

HTTP transports add a network layer: Host/Origin protection is not authentication, so binding to a
non-loopback host requires `ARGOCD_HTTP_BEARER_TOKEN` (min 16 chars, constant-time compared via a
`TokenVerifier` subclass). The CLI refuses to start on a non-loopback host without it.

CLI flags override env: `--argocd-url`, `--read-only`, `--insecure`, plus
`--transport {stdio,sse,streamable-http}` with `--host` and `--port` for non-stdio transports.
`--read-only` can only raise the lock (it sets `ARGOCD_READ_ONLY=true`); there is no
`--no-read-only`, so argv can never unlock a server whose env already locked it.
`--argocd-token` is deprecated and hidden from `--help`; it still works and warns on stderr (it
exposes the token to `ps` and shell history). Set `ARGOCD_TOKEN` instead.

## Release Workflow

Releases run through GitHub Actions — never bump versions manually.

```bash
gh workflow run release.yml -f version=0.1.0
gh workflow run release.yml -f bump=minor -f dry_run=true  # preview changelog, no push
```

1. `release.yml` (workflow_dispatch) — runs the `tests.yml` and `lint.yml` gates on the released
   commit first (`needs: [test, lint]` via `workflow_call`), then bumps the version in
   `pyproject.toml`, `llms-full.txt`, `server.json`; runs `scripts/derive_artifacts.py`;
   regenerates `uv.lock`; prepends a CHANGELOG entry. The release commit is created via the GitHub
   API with `GITHUB_TOKEN` and **no** `author`/`committer`, so GitHub signs it as
   `github-actions[bot]` (Verified); the job then fails unless `verification.verified` is true.
   `main` and the tag ref are moved with `RELEASE_PAT` (a fast-forward that fails if `main` moved
   mid-run; the PAT bypasses the ruleset). `release.yml` then dispatches `publish.yml`, and the PAT's tag push no longer triggers publish (publish.yml is dispatch-only).
2. `publish.yml` runs as split, idempotent jobs (`status → build → pypi → github-release →
   registry`): each target is checked first and written only when not already done, so PyPI, the
   GitHub release and the MCP Registry each complete independently and recoverably.
3. Recovery of a failed publish is `gh run rerun <id> --failed` or `gh workflow run publish.yml
   --ref refs/tags/vX.Y.Z`; both are no-ops for targets that are already done.
4. `reconcile.yml` does this unattended — about 5 min after a failed publish run, then every 6 h on
   a schedule — scanning the last three tags and dispatching `publish.yml` as needed, capped at 8
   failed attempts per tag before it asks for a human.

Rules: never edit the `pyproject.toml` version directly; never create tags manually; use
Conventional Commits (`feat:`, `fix:`, `docs:`, …).

Release commits on `main` are Verified, but PR commits merged by GitHub's rebase-only policy show
as unsigned: GitHub rewrites each rebased commit with a new committer and cannot sign it as a
system user. That is by design — PR review plus the required `lint`/`test` checks are the
integrity control; only the release commit is signed.

## Documentation Freshness (mandatory)

When a changeset adds, removes, or modifies tools, resources, or prompts, update ALL of these in
the same commit:

- `README.md` — counts in heading and intro, tool table, full tool reference, usage examples, permissions
- `llms.txt` — count in tagline and documentation link (derived from `llms-full.txt`)
- `llms-full.txt` — count in tagline, documentation link, full tool reference
- `AGENTS.md` — counts in intro, tool category table
- `server.json` — description field (≤100 chars)

`llms.txt` and `gemini-extension.json` are derived — run `python scripts/derive_artifacts.py`,
never hand-edit them.

## Known Limitations

- 37 tools in one server file, past the 5-15 guideline. If you refactor it, split by category.
- Argo CD list endpoints have no server-side paging, so a very large install pays one full fetch
  per list call; the server pages the result client-side.
- Pod logs are capped at 1000 lines.

## Future work

- **OAuth 2.1 / identity passthrough** is out of scope for 0.1.0 (spec decision D6). A design
  exists at `.internal/specs/2026-10-03-oauth21.md`; when it lands it applies to all four sibling
  servers at once. Until then, use an API-key account or a project-role token.
- **Session login** (`ARGOCD_USERNAME`/`ARGOCD_PASSWORD` → `POST /api/v1/session`) is deferred: it
  works only for local accounts with the `login` capability, never SSO, and tokens expire in 24h.
