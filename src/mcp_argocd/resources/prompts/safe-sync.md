# Safe sync of $name

Sync `$name` safely, with prune set to `$prune`.

1. `argocd_get_sync_windows(name="$name")` — confirm a window is not blocking.
2. `argocd_sync_application(name="$name", dry_run=True)` — preview the change.
3. Review the dry-run result. Stop if it would delete or recreate anything unexpected.
4. `argocd_sync_application(name="$name", prune=$prune, wait=True)` — run it and wait.
5. `argocd_get_application(name="$name")` — confirm Healthy/Synced, or escalate to diagnose_sync_failure.
