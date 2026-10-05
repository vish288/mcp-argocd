# ApplicationSet Rules

Rules for working with ApplicationSets and the applications they generate.

- **Generated apps are owned by the set.** Edit the ApplicationSet or its Git source, never the generated Application directly — the controller overwrites direct edits.
- **`preservedFields`** lets you keep specific fields on generated apps across regeneration.
- **`ignoreApplicationDifferences`** suppresses diffs the set should not reconcile.
- **Progressive `strategy`** (e.g. `RollingSync`) rolls changes out in ordered steps.
- **Preview with generate.** Use `argocd_generate_applicationset` to see what a spec would produce before applying it.
- **Deleting a set deletes its apps** unless the `resources-finalizer.argocd.argoproj.io` finalizer is removed first.
