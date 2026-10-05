# RBAC and Permission Errors

A guide to Argo CD RBAC and reading permission-denied errors.

- **Resource/action model** ([RBAC](https://argo-cd.readthedocs.io/en/stable/operator-manual/rbac/)): policies are `p, <subject>, <resource>, <action>, <object>, allow|deny`. Resources include `applications`, `applicationsets`, `clusters`, `projects`, `repositories`, `logs`, `exec`. Actions include `get`, `create`, `update`, `delete`, `sync`, `rollback`, `action`, `override`.
- **Fine-grained sub-resource actions** (3.0 default): `update/<group>/<kind>/<ns>/<name>` and `delete/<group>/<kind>/<ns>/<name>` gate per-resource writes.
- **`logs, get` is separate** from `applications, get` since 3.0 ([2.14 to 3.0 upgrade](https://github.com/argoproj/argo-cd/blob/master/docs/operator-manual/upgrading/2.14-3.0.md)). A read-only role needs both.
- **Resource actions** use `action/<group>/<kind>/<action>`.
- **Reading an error.** `permission denied: applications, sync, proj/app, sub: alice` means subject `alice` lacks `applications, sync` on `proj/app`. Check with `argocd_can_i`.
- **403 vs 404.** Without `project`, Argo CD returns 403 for an app that does not exist. Pass `project` to get a real 404.
