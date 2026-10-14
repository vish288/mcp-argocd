# Changelog

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

