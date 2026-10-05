# Status Triage Guide

A guide for diagnosing why an Argo CD application is unhealthy or out of sync.

## Sync status x health status

- `Synced` + `Healthy`: nominal.
- `OutOfSync` + `Healthy`: live differs from Git but workloads are fine — decide sync vs Git fix.
- `Synced` + `Degraded`: Git applied, but a workload is failing — inspect pods and events.
- `OutOfSync` + `Progressing`: a rollout is mid-flight.
- any + `Missing`: a resource in Git is not present in the cluster.
- any + `Suspended`: a CronJob or Rollout is paused.
- `Progressing` for more than ~10 minutes usually means a stuck rollout, failing probes, or an image pull error.

## Condition types

`ComparisonError`, `InvalidSpecError`, `SyncError`, `SharedResourceWarning`, `OrphanedResourceWarning`, `RepeatedResourceWarning`, `UnknownError`.

## Order of investigation

1. Read `conditions` on the app (`argocd_get_application`).
2. Read the last `operation` (`argocd_get_operation`) for failed resources.
3. List unhealthy nodes (`argocd_get_resource_tree(health_status=Degraded)`).
4. Read Warning events (`argocd_get_application_events(warnings_only=True)`).
5. Tail logs on the worst pod (`argocd_get_pod_logs`).
