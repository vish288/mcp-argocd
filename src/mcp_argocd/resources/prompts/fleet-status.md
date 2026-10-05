# Fleet status for project $project

Report fleet health, optionally scoped to destination `$destination`.

1. `argocd_list_clusters` — note each cluster's connection state.
2. `argocd_list_applications(projects=["$project"], destination="$destination")` — filter to apps that are not Synced/Healthy.
3. Build a table per cluster: app, sync status, health, last operation.
4. Flag any cluster whose `connection.status` is not `Successful`.
