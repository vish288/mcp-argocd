# Triage application $name

Diagnose why application `$name` (namespace `$app_namespace`) is unhealthy or out of sync.

1. `argocd_get_application(name="$name")` — read `conditions` and the last `operation`.
2. `argocd_get_resource_tree(name="$name", health_status="Degraded")` — find unhealthy nodes.
3. `argocd_get_application_events(name="$name", warnings_only=True)` — read recent Warnings.
4. `argocd_get_pod_logs(name="$name", ...)` on the worst pod — read the failing lines.
5. Summarize as a table: resource, symptom, evidence, next step.
