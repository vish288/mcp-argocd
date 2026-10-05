# Roll back $name

Roll back `$name` to history entry `$history_id`.

1. `argocd_get_application_history(name="$name")` — pick the known-good entry.
2. `argocd_get_revision_metadata(name="$name", revision=...)` — confirm the candidate commit.
3. Check `auto_sync`. If it is on, ask before disabling it with `argocd_patch_application` — rollback is refused while auto-sync is enabled.
4. `argocd_rollback_application(name="$name", history_id=$history_id, wait=True)`.
5. `argocd_get_application(name="$name")` to confirm, then remind the user to fix Git so the next sync does not undo the rollback.
