# Inspect ApplicationSet $name

Inspect ApplicationSet `$name` (namespace `$appset_namespace`) and the apps it generates.

1. `argocd_get_applicationset(name="$name", include_applications=True)` — read generated app statuses.
2. `argocd_generate_applicationset(applicationset=<its spec>)` — preview what it would generate now.
3. Diff generated vs existing apps.
4. `argocd_list_applications(selector=...)` — check the generated apps' health.
5. Report missing, extra, failed, and progressing-step apps.
