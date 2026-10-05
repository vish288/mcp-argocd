# Rollback Rules

Rules for rolling an Argo CD application back to a previous deployment.

- **Only to kept history.** You can roll back only to entries within `revisionHistoryLimit` (default 10). Older entries are gone.
- **Auto-sync blocks rollback.** Argo CD refuses a rollback while `syncPolicy.automated` is enabled. Disable it first with `argocd_patch_application(patch='{"spec":{"syncPolicy":{"automated":null}}}')`.
- **`dryRun` and `prune` only.** Rollback takes just those two modifiers; it is otherwise a sync to an old revision.
- **A rollback re-applies old Git state.** Because it is a sync to an old revision, the next auto-sync re-applies current Git. Fix Git after rolling back, or the rollback is undone.
- **RBAC.** A separate `rollback` action is opt-in via `server.rbac.rollback.enforce.enable`; otherwise `sync` permission covers it ([RBAC rollback](https://argo-cd.readthedocs.io/en/stable/operator-manual/rbac/#the-rollback-action)).
