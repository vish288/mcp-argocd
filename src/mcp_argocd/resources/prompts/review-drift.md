# Review drift in project $project

Review out-of-sync applications (optionally scoped to app `$name`) and decide what to do.

1. `argocd_list_applications(sync_status="OutOfSync")` — list drifted apps.
2. For each, `argocd_get_managed_resources(modified_only=True)` — read the diffs.
3. Classify each diff: real drift, a field managed by another controller (an `ignoreDifferences` candidate), or a hook.
4. Recommend per app: `argocd_sync_application`, a Git change, or an ignore rule.
