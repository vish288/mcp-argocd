# Test fixtures

Real-shaped Argo CD API payloads used by the unit tests. Field names and
nesting follow the Argo CD master swagger (`.internal/argocd-swagger.json`,
fetched 2026-10-03) and the gitops-engine type definitions.

## Source

These fixtures were **built from the swagger and research artifacts, not
captured from a live cluster.** The planned live capture (T3 in the spec)
needs a Kubernetes cluster via `kind` or `k3d`; neither is installed on the
build machine, and installing one is a system-package change that was not made.
`podman` is present but does not by itself provide the kind/k3d cluster the
spec's setup script requires. The shapes are therefore derived from:

- `.internal/argocd-swagger.json` — definitions `v1alpha1Application`,
  `v1alpha1ApplicationTree`, `v1alpha1ResourceDiff`, `v1alpha1Repository`,
  `v1alpha1AppProjectSpec`, `applicationLogEntry`, `versionVersionMessage`,
  `v1alpha1ClusterInfo`, `applicationApplicationSyncWindowsResponse`,
  `v1alpha1RevisionMetadata`, and the `clusterCluster` / `ApplicationSet` types.
- `.internal/argocd-research/` — competitor READMEs and the official server's
  issue dump, which show the real response shapes operators hit.

## Argo CD version

Targeted against **Argo CD v3.5.3** (stable, released 2026-09-14 —
`.internal/argocd-research/argocd-latest.json`). The server targets 3.3+ and
uses only the `/api/v1` REST gateway plus `/api/version`.

## `fields=` finding (spec D10)

The Argo CD UI sends `fields=a,b` / `fields=-a,b` on list calls
(`ui/src/app/shared/services/applications-service.ts`). Whether the REST
gateway honours it could **not be verified live** here, because no cluster was
available. The exact request that a live check would run is:

```
GET /api/v1/applications?fields=items.metadata.name,items.status.sync.status
```

compared against the same call without `fields`. Until that is verified on a
real server, `argocd_list_applications` trims **client-side only** and sends no
`fields` parameter. The tool's output contract is identical either way, so this
decision can flip to server-side `fields` later without changing any tool
signature or test row.

## Files

| File | Endpoint |
|---|---|
| `application.json` | `GET /applications/{name}` (guestbook, Synced/Healthy, with history, resources, operationState) |
| `application_list.json` | `GET /applications` (guestbook, payments OutOfSync/Degraded, api Synced/Progressing) |
| `resource_tree.json` | `GET /applications/{name}/resource-tree` |
| `managed_resources.json` | `GET /applications/{name}/managed-resources` |
| `events.json` | `GET /applications/{name}/events` |
| `logs.ndjson` | `GET /applications/{name}/logs` (NDJSON stream) |
| `applicationset.json` | `GET /applicationsets/{name}` |
| `cluster.json` | `GET /clusters/{id}` (carries a `config` to prove scrubbing) |
| `repository.json` | `GET /repositories` item (carries every credential field to prove scrubbing) |
| `project.json` | `GET /projects/{name}` (role with `jwtTokens` to prove scrubbing) |
| `version.json` | `GET /api/version` |
