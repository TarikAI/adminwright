# Changelog

All notable changes to this project are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

A note on what "breaking" means here: a change is breaking if a manifest that validated
cleanly before now fails. Tightening a check is therefore usually breaking, and lands in a
major version or behind a profile.

## [Unreleased]

## [2.0.0] - 2026-08-07

First public release, under the name Adminwright.

### Added

- **Manifest v2** with `profile`, `platform.stack`, `platform.researchSources`,
  `platform.volumes`, `entities[].lifecycleTransitions`, `capability.entityStates`,
  `capability.dataBinding`, and the `declaredStatic`, `feedback`, and `agents` collections.
- **Three profiles** — `internal`, `standard`, `regulated` — scaling rule severity so a team
  can pass truthfully at its tier rather than falsify the top one.
- **Structural coverage checks**: unused roles, entities with no query, unreachable lifecycle
  states, dangling transitions, capabilities not linked from a declaring screen.
- **Cross-project learning**: `harvest` collects a project's `feedback[]` into a store in the
  user's home directory; `promote` surfaces observations seen on two or more distinct
  projects, or corrections of wrong guidance.
- **Archetype coverage check**: warns when a manifest declares an archetype but engages almost
  none of its expected domains.
- **New commands**: `migrate`, `coverage`, `emit` (six formats), `add`, `set`, `claim`,
  `release-claim`, `harvest`, `promote`, `lesson`.
- **Multi-agent coordination** through manifest locks, with a distinct exit code for claim
  conflicts and five defined agent roles.
- **Admin core schema** — audit log with hash chaining, scoped RBAC, impersonation, approvals
  with separation of duties, jobs, saved views, exports, data-subject requests — in PostgreSQL
  plus Prisma, Drizzle, Django, Laravel, and Rails.
- **Eight new references**: build order, buy-vs-build, stack adapters, resource index, admin
  data model, multi-agent, test data, skill evolution.
- **35 regression tests** and CI across Linux, macOS, and Windows.

### Changed

- Reference loading is phase-gated rather than loaded up front.
- `coverage` exits non-zero on errors only, matching `validate`. It previously failed on
  warnings alone, blocking `internal`-profile teams for findings that profile permits.
- Completeness rules warn before release and error at release, so a model can be built
  incrementally. Previously the first `add --kind role` was refused at the default profile.
- The placeholder scan runs at every phase and status, not only once a capability is marked
  implemented.

### Fixed

- Placeholder bypasses: Unicode homoglyphs, tokens with no case boundary
  (`gmockRepositoryImpl`), strings nested inside arrays, and camelCase or snake_case embedding.
- Evidence integrity: directories, whitespace-only files, paths resolving outside the project
  root, and evidence files whose contents were themselves placeholder text.
- `--allow-invalid` no longer writes silently; it reports everything it waved through.
- Clean exit code 2 instead of a traceback on non-UTF-8 and deeply nested JSON.
- Missing required-field enforcement on `workQueues` and `integrations`.
- `capability_tokens` returned an empty set for short ids, silently skipping the
  evidence-token check.

[Unreleased]: https://github.com/TarikAI/adminwright/compare/v2.0.0...HEAD
[2.0.0]: https://github.com/TarikAI/adminwright/releases/tag/v2.0.0
