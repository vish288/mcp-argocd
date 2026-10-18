# Changelog

## [0.5.2] - 2026-10-18

### Bug Fixes
- fix(ci): dispatch publish with RELEASE_PAT so reconcile's failure path fires (53321ac)
- fix(ci): dispatch-only publish.yml and wait for PyPI propagation (517b714)
- fix(ci): keep retry.sh stdout clean so captured ids aren't corrupted (d8a9d97)
- fix(ci): create the release draft by id, no list-lookup race (105e176)

### Documentation
- docs: describe signed rebase + fast-forward landing in AGENTS.md (5d38924)

### Other
- ci: pin stubs to vish288/.github v1.0.0 (aa3e092)
- ci: top-level PyPI publish job, explicit permissions, SHA pins (b94173a)
- ci: pin stubs to the updated vish288/.github R1a head (aa29491)
- ci: call vish288/.github reusable workflows via stubs (d702f1a)


## [0.5.1] - 2026-10-14

### Documentation
- docs(read-only): surface both modes, add HTTP bearer gate and Dockerfile (331b995)

### Other
- ci(publish): resilient, recoverable publish pipeline (d4e3f11)


### Notes
- Source distributions up to and including 0.5.0 accidentally bundled `mcp-publisher.tar.gz` (about 7.3 MB), the MCP Registry publisher binary that the publish workflow downloaded into the build tree before `uv build`. Wheels were never affected and `pip`/`uv` install the wheel, so there is no runtime impact and those versions are not yanked. The bundled file is a third-party build from modelcontextprotocol/registry (MIT, transitioning to Apache-2.0 per its LICENSE) and was redistributed without its licence text. From this release the build asserts that the sdist contains only tracked files.

## [0.5.0] - 2026-10-13

### Features
- feat(read-only): lock via MCP_READ_ONLY; hide write tools and prompts (e5cb803)

### Tests
- test: consolidate argocd count constants and drop tautologies (6dfc1b0)
- test: merge three redundant argocd behaviour tests (158238f)


## [0.4.0] - 2026-10-12

### Features
- feat!: send tool results as structuredContent objects and drop the placeholder outputSchema (f05e53d)

### Bug Fixes
- fix: correct orphan delete, transport errors, URL redaction, rollback guard (e93bf65)
- fix: surface Argo CD connection errors and harden the test suite (93bd70b)


## [0.3.1] - 2026-10-11

### Bug Fixes
- fix: drop structuredContent from error results so strict MCP clients accept them (4964db3)


## [0.3.0] - 2026-10-11

### Features
- feat!: report expected tool failures with isError (f87cf2e)

### Chores
- chore: gate releases on CI, sign release commits, pin mcp-publisher, deprecate token flags, cap deps (d6ce14e)


## [0.2.0] - 2026-10-10

### Bug Fixes
- fix: correct resource request shapes, block path injection, fix sync/rollback and log caps (review 2026-10-06) (a6e6b8e)

### Documentation
- docs: STE-100 and AEO/SEO pass; refresh related servers (e543ffb)


## [0.1.0] - 2026-10-06

### Features
- feat: initial mcp-argocd server for the Argo CD REST API (9e8f481)

