# API Token Setup

How to create a token for headless Argo CD access.

- **Local account with `apiKey`.** Add the account to `argocd-cm` (`accounts.ci-bot: apiKey`), then generate a token:
  ```
  argocd account generate-token --account ci-bot --expires-in 24h
  ```
  Revoke with `argocd account delete-token` ([user management](https://argo-cd.readthedocs.io/en/stable/operator-manual/user-management/), [generate-token](https://argo-cd.readthedocs.io/en/stable/user-guide/commands/argocd_account_generate-token/)).
- **Project role tokens** scope a token to one project's role.
- **`/api/v1/session`** issues tokens for local accounts with the `login` capability only — never SSO. Session tokens default to 24h.
- **Minimum read-only policy:**
  ```
  p, role:mcp-ro, applications, get, */*, allow
  p, role:mcp-ro, logs, get, */*, allow
  p, role:mcp-ro, applicationsets, get, */*, allow
  p, role:mcp-ro, projects, get, *, allow
  p, role:mcp-ro, clusters, get, *, allow
  p, role:mcp-ro, repositories, get, *, allow
  ```
