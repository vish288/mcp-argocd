# Sync Safety Rules

Rules for syncing an Argo CD application without causing an outage.

- **Dry-run first.** Run `argocd_sync_application(dry_run=True)` before any real sync on a prod app. It reports what would change and surfaces manifest or admission errors with no side effects.
- **`prune` deletes.** With `prune=True`, resources no longer in Git are removed. Never prune a prod app you have not dry-run.
- **`force` recreates.** `force=True` (`strategy.apply.force`) deletes and recreates resources on conflict, which can drop a running workload.
- **Sync options** ([sync options](https://argo-cd.readthedocs.io/en/stable/user-guide/sync-options/)):
  - `Prune=confirm` holds destructive prunes for manual confirmation.
  - `PruneLast=true` prunes only after other resources sync.
  - `PrunePropagationPolicy=foreground|background|orphan` controls dependent deletion.
  - `Replace=true` uses `kubectl replace` instead of `apply` — recreates the object.
  - `ServerSideApply=true` applies server-side; needed for large CRDs.
  - `ApplyOutOfSyncOnly=true` applies only out-of-sync resources.
- **Selective sync** (passing `resources`) skips hooks and does not record history; `ApplyOutOfSyncOnly` keeps both.
- **Respect sync windows.** If `argocd_get_sync_windows` reports `can_sync: false`, a window is blocking; manual sync needs `manualSync: true` on the window.
- **Never override `revision` on a prod app** without the `applications, override` RBAC permission; it bypasses the Git-tracked target.
