# GitOps Change Flow

How to make changes the GitOps way, where Git is the source of truth.

- **Desired state lives in Git.** The cluster is a projection of Git. Change Git, let Argo CD reconcile.
- **`selfHeal` reverts live edits.** With self-heal on, any `kubectl edit` is undone at the next reconcile. Do not expect live edits to stick.
- **Live fixes are for incidents only.** When an incident justifies a live change, patch the app or run a resource action, then immediately open the matching Git change so the fix survives the next sync.
- **Never `delete_resource` to "fix" drift.** Deleting a managed resource triggers recreation from Git; it does not resolve a spec problem. Fix the manifest in Git instead.
