# Diagnose sync failure for $name

Find out why the last sync of `$name` failed.

1. `argocd_get_operation(name="$name")` — list the failed resources and their messages.
2. `argocd_get_application_events(name="$name", resource_name=...)` for each failed resource.
3. `argocd_get_manifests` or `argocd_get_managed_resources` for the failing kind.
4. Classify the failure: manifest error, admission webhook, RBAC, hook failure, or timeout.
5. Recommend one of: retry `argocd_sync_application`, `argocd_terminate_operation`, or a Git fix.
