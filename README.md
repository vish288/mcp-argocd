# mcp-argocd

[![PyPI version](https://img.shields.io/pypi/v/mcp-argocd)](https://pypi.org/project/mcp-argocd/)
[![PyPI downloads](https://img.shields.io/pypi/dm/mcp-argocd)](https://pypi.org/project/mcp-argocd/)
[![Python](https://img.shields.io/pypi/pyversions/mcp-argocd)](https://pypi.org/project/mcp-argocd/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![CI](https://github.com/vish288/mcp-argocd/actions/workflows/tests.yml/badge.svg)](https://github.com/vish288/mcp-argocd/actions/workflows/tests.yml)
[![MCP Registry](https://img.shields.io/badge/MCP-Registry-blue)](https://registry.modelcontextprotocol.io)

<!-- mcp-name: io.github.vish288/mcp-argocd -->

**mcp-argocd** is a [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) server for the Argo CD REST API. It gives AI assistants **37 tools**, **7 resources**, and **7 prompts** to triage application status, sync, roll back, inspect drift, read logs, and manage ApplicationSets, clusters, projects, and repositories. Works with Claude Desktop, Claude Code, Cursor, Windsurf, VS Code Copilot, and any MCP-compatible client.

Works with Argo CD 3.3+ (any install: OSS, Akuity, OpenShift GitOps). Needs an API token, no cluster access.

Supports the MCP 2026-07-28 specification, often called MCP 2.0, and stays compatible with 2025-11-25 clients.

Built with [FastMCP](https://github.com/jlowin/fastmcp) 4.x, [httpx](https://www.python-httpx.org/), and [Pydantic](https://docs.pydantic.dev/).

**Install:** `uvx mcp-argocd` | [PyPI](https://pypi.org/project/mcp-argocd/) | [MCP Registry](https://registry.modelcontextprotocol.io) | [Changelog](https://github.com/vish288/mcp-argocd/releases)

## 1-Click Installation

[![Install in Cursor](https://cursor.com/deeplink/mcp-install-dark.svg)](https://vish288.github.io/mcp-install?server=mcp-argocd&install=cursor)

[![Install in VS Code](https://img.shields.io/badge/VS_Code-Install_Server-0098FF?style=flat-square&logo=visualstudiocode&logoColor=white)](https://vish288.github.io/mcp-install?server=mcp-argocd&install=vscode)

> **Tip:** For other AI assistants (Claude Code, Windsurf, IntelliJ, Gemini CLI), visit the **[Argo CD MCP Installation Gateway](https://vish288.github.io/mcp-install?server=mcp-argocd)**.

<details>
<summary><b>Manual Setup Guides (Click to expand)</b></summary>
<br/>

> Prerequisite: Install `uv` first. [Install uv](https://docs.astral.sh/uv/getting-started/installation/).

### Claude Code

```bash
claude mcp add argocd -- uvx mcp-argocd
```

### Windsurf & IntelliJ

**Windsurf:** Add to `~/.codeium/windsurf/mcp_config.json`
**IntelliJ:** Add to `Settings | Tools | MCP Servers`

```json
{
  "mcpServers": {
    "argocd": {
      "command": "uvx",
      "args": ["mcp-argocd"],
      "env": {
        "ARGOCD_URL": "https://argocd.example.com",
        "ARGOCD_TOKEN": "<token>"
      }
    }
  }
}
```

### Gemini CLI

The repo ships `gemini-extension.json`; install it with the Gemini CLI extension flow.

### pip / uv

```bash
uvx mcp-argocd            # run without installing
pip install mcp-argocd    # or install into an environment
```

</details>

## Configuration

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ARGOCD_URL` | Yes | - | Base URL including any path prefix. Also reads `ARGOCD_SERVER`. |
| `ARGOCD_TOKEN` | Yes | - | Bearer token. Also reads `ARGOCD_AUTH_TOKEN`, `ARGOCD_API_TOKEN`. |
| `ARGOCD_READ_ONLY` | No | `false` | `true` blocks the 9 write tools before any API call |
| `ARGOCD_TIMEOUT` | No | `30` | Request timeout in seconds |
| `ARGOCD_SSL_VERIFY` | No | `true` | `false` skips SSL verification (`ARGOCD_INSECURE=true` is an alias) |
| `ARGOCD_APP_NAMESPACE` | No | - | Default `appNamespace` for apps-in-any-namespace installs |

## Getting an API token

Argo CD tokens come from a local account with the `apiKey` capability, or from a project role.

1. Add a local account in `argocd-cm`: set `accounts.ci-bot: apiKey`.
2. Generate a token: `argocd account generate-token --account ci-bot --expires-in 24h`.
3. Set `ARGOCD_URL` and `ARGOCD_TOKEN`.

Minimum read-only RBAC:

```
p, role:mcp-ro, applications, get, */*, allow
p, role:mcp-ro, logs, get, */*, allow
p, role:mcp-ro, applicationsets, get, */*, allow
p, role:mcp-ro, projects, get, *, allow
p, role:mcp-ro, clusters, get, *, allow
p, role:mcp-ro, repositories, get, *, allow
```

## Compatibility

| Component | Supported |
|---|---|
| Argo CD | 3.3+ (OSS, Akuity, OpenShift GitOps); most tools also work on 2.x |
| Python | 3.10, 3.11, 3.12, 3.13, 3.14 |
| Transports | stdio, streamable-http, sse (deprecated) |
| MCP spec | 2026-07-28 (MCP 2.0); compatible with 2025-11-25 |

## Protocol support

Supports the MCP 2026-07-28 specification (MCP 2.0) and stays compatible with 2025-11-25 clients.
Built on FastMCP 4.x. A regression test lists all 37 tools with an in-memory MCP client pinned to
2026-07-28. The `sse` transport is
deprecated by the 2026-07-28 specification; it still works and prints a warning. The server uses
no roots, sampling, logging, elicitation, or resource subscriptions.

## Tools (37)

| Category | Count |
|---|---|
| Applications — read | 14 |
| Applications — write | 8 |
| ApplicationSets | 3 |
| Projects | 2 |
| Clusters | 3 |
| Repositories | 3 |
| Server and account | 4 |

<details>
<summary><b>Full tool reference (Click to expand)</b></summary>

### Applications — read

- `argocd_list_applications` — List applications with slim rows; filter by sync, health, destination.
- `argocd_get_application` — Get one application: spec, sync/health, conditions, operation, counts.
- `argocd_get_resource_tree` — Get the live resource tree as slim nodes.
- `argocd_get_managed_resources` — Get live-vs-desired diffs for managed resources.
- `argocd_get_resource` — Get a single managed resource's live manifest.
- `argocd_get_manifests` — Get the rendered desired manifests for a revision.
- `argocd_get_application_events` — Get Kubernetes events for an application.
- `argocd_get_pod_logs` — Get container logs; never follows.
- `argocd_get_application_history` — Get deployment history, newest first.
- `argocd_get_revision_metadata` — Get commit metadata for a revision.
- `argocd_get_operation` — Get the current or last sync operation.
- `argocd_wait_for_operation` — Poll until an operation is terminal, gone, or times out.
- `argocd_get_sync_windows` — Get sync windows and whether the app can sync now.
- `argocd_list_resource_actions` — List the custom actions available on a resource.

### Applications — write

- `argocd_sync_application` — Sync an application; prune and force can delete or recreate resources.
- `argocd_rollback_application` — Roll back to a prior deployment history entry.
- `argocd_terminate_operation` — Terminate the running sync operation.
- `argocd_create_application` — Create an application from flattened parameters.
- `argocd_patch_application` — Patch an application; the one tool for every update.
- `argocd_delete_application` — Delete an application.
- `argocd_run_resource_action` — Run a custom resource action, such as restart.
- `argocd_delete_resource` — Delete a single managed resource so the controller recreates it.

### ApplicationSets

- `argocd_list_applicationsets` — List ApplicationSets with slim rows.
- `argocd_get_applicationset` — Get one ApplicationSet and the status of its generated apps.
- `argocd_generate_applicationset` — Dry-run the generators to preview generated apps; creates nothing.

### Projects

- `argocd_list_projects` — List projects with slim rows.
- `argocd_get_project` — Get a project's repos, destinations, and roles.

### Clusters

- `argocd_list_clusters` — List clusters with connection state, versions, and counts.
- `argocd_get_cluster` — Get one cluster by name or server URL.
- `argocd_invalidate_cluster_cache` — Invalidate a cluster's cached resources.

### Repositories

- `argocd_list_repositories` — List repositories; credentials are never returned.
- `argocd_get_repository_refs` — Get a repository's branches and tags.
- `argocd_list_repository_apps` — List the application paths discoverable in a repository.

### Server and account

- `argocd_get_version` — Get the Argo CD server version and bundled tool versions.
- `argocd_get_userinfo` — Get the authenticated identity.
- `argocd_can_i` — Check whether the account may perform a resource/action.
- `argocd_get_settings` — Get server settings and enabled features.

</details>

## Resources (7)

The server exposes curated GitOps rules and guides as MCP resources.

- `resource://rules/sync-safety` — Sync Safety Rules.
- `resource://rules/rollback` — Rollback Rules.
- `resource://rules/gitops-change-flow` — GitOps Change Flow.
- `resource://rules/applicationsets` — ApplicationSet Rules.
- `resource://guides/status-triage` — Status Triage Guide.
- `resource://guides/rbac` — RBAC and Permission Errors.
- `resource://guides/api-token-setup` — API Token Setup.

## Prompts (7)

The server provides MCP prompts — multi-tool workflow templates clients surface as slash commands.

- `triage_application` — Diagnose a Degraded or OutOfSync application.
- `diagnose_sync_failure` — Classify why the last sync failed.
- `review_drift` — Review OutOfSync apps and recommend a fix.
- `safe_sync` — Check sync windows, dry-run, then sync and wait.
- `rollback_application` — Roll back with the auto-sync check.
- `fleet_status` — Report clusters and apps that are not Synced/Healthy.
- `inspect_applicationset` — Compare generated vs existing apps.

## Usage Examples

- **Triage**: "Why is `payments` Degraded?" runs `triage_application` — app conditions, unhealthy nodes, warning events, then pod logs.
- **Drift**: "What has drifted in `prod`?" runs `review_drift` — lists OutOfSync apps and shows each diff.
- **Safe sync**: "Sync `web` safely" runs `safe_sync` — checks sync windows, dry-runs, then syncs and waits.
- **Rollback**: "Roll `api` back to the last good deploy" runs `rollback_application` — checks auto-sync first.
- **Fleet**: "Show cluster health" runs `fleet_status` — clusters and the apps that are not healthy.

## Security Considerations

- **Token scope**: use the minimum RBAC the workflow needs. A read-only role needs `applications, get` and `logs, get`.
- **Read-only mode**: `ARGOCD_READ_ONLY=true` blocks all 9 write tools before any API call.
- **SSL verification**: on by default. Disable only for self-signed certificates in trusted networks.
- **Secret scrubbing**: repository, cluster, and project payloads are scrubbed of credentials, cluster `config`, and role `jwtTokens`, even with `full=True`.
- **MCP tool annotations**: every tool declares `readOnlyHint`, `destructiveHint`, and `idempotentHint`.
- **No credential storage**: the server reads the token from the environment at startup and never persists it.
- **Destructive tools**: `argocd_sync_application`, `argocd_rollback_application`, `argocd_terminate_operation`, `argocd_delete_application`, `argocd_run_resource_action`, `argocd_delete_resource`.

## Permissions

| Operation | Argo CD RBAC `resource, action` |
|---|---|
| Read apps and resources | `applications, get` |
| Read pod logs | `logs, get` |
| Sync | `applications, sync` |
| Override a revision | `applications, override` |
| Roll back | `applications, sync` (or `applications, rollback` when enforced) |
| Delete an app | `applications, delete` |
| Run a resource action | `applications, action/<group>/<kind>/<action>` |
| Read clusters, projects, repos | `clusters, get` / `projects, get` / `repositories, get` |

## CLI & Transport Options

```bash
uvx mcp-argocd                                   # stdio (default)
uvx mcp-argocd --transport streamable-http --port 9000
uvx mcp-argocd --transport sse                   # deprecated; prints a warning
uvx mcp-argocd --read-only --insecure
```

## FAQ

**Does mcp-argocd support MCP 2026-07-28 (MCP 2.0)?** Yes. It supports the MCP 2026-07-28 specification, often called MCP 2.0, and stays compatible with 2025-11-25 clients.

**How is it different from argoproj-labs/mcp-for-argocd?** argoproj-labs/mcp-for-argocd is the official TypeScript server with 15 tools. mcp-argocd adds 37 tools with slim payloads by default, plus diff, rollback, terminate, wait-for-operation, ApplicationSet dry-run, and RBAC error hints.

**Which Argo CD versions work?** Argo CD 3.3 and newer. Most tools also work on 2.x; only `run_resource_action` uses a 3.x endpoint.

**Does it need kubectl or cluster credentials?** No. It talks to the Argo CD API over HTTPS with a bearer token. It never touches the cluster directly.

**Is it safe for read-only use?** Yes. Set `ARGOCD_READ_ONLY=true`. The server blocks all nine write tools before any API call.

**Which token does it need?** It needs a bearer token in `ARGOCD_TOKEN`. Generate one from a local account with the `apiKey` capability, or use a project-role token. It also reads `ARGOCD_AUTH_TOKEN` and `ARGOCD_API_TOKEN`.

**Why do I get a 403 for an app that exists?** Without `project`, Argo CD returns 403 for an app that does not exist. Pass `project` to get a real 404.

**Can it sync only one resource?** Yes. Pass `resources` to `argocd_sync_application` with `[group:]kind:name[/namespace]` selectors.

**Can it roll back?** Yes. Use `argocd_rollback_application` with a history id. It refuses while auto-sync is on and tells you how to disable it.

**How does it keep responses small?** Every tool returns a slim payload by default and pages lists client-side. Pass `full=True` for the raw payload.

**Which transports?** stdio, streamable-http, and sse. sse is deprecated by the 2026-07-28 spec but still works.

**How do I install?** Run `uvx mcp-argocd`, or add it to your MCP client config.

## Related MCP Servers

- [mcp-gitlab](https://github.com/vish288/mcp-gitlab) — GitLab integration (83 tools, 7 resources, 6 prompts)
- [mcp-atlassian-extended](https://github.com/vish288/mcp-atlassian-extended) — Jira + Confluence integration (22 tools, 15 resources, 5 prompts)
- [mcp-coda](https://github.com/vish288/mcp-coda) — Coda integration (53 tools, 12 resources, 5 prompts)

## Development

```bash
git clone https://github.com/vish288/mcp-argocd.git
cd mcp-argocd
uv sync --all-extras

uv run pytest --cov
uv run ruff check .
uv run ruff format --check .
```

## License

MIT — see [LICENSE](LICENSE).
