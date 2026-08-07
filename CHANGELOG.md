# Changelog

All notable changes to this project are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

A note on what "breaking" means here: a change is breaking if a manifest that validated
cleanly before now fails. Tightening a check is therefore usually breaking, and lands in a
major version or behind a profile.

## [Unreleased]

### Added

- **Cross-device store sync**: `store init | sync | status` makes the observation store a git
  repository pointed at a remote you own. Observations merge by id union rather than git's
  line merge, and the file is normalised after each join so repeated syncs converge.
- **Opt-in community lane**: `promote --export` writes a sanitised bundle (emails, URLs,
  paths, addresses, hostnames, domains, tokens and hashes replaced; project names replaced by
  one-way fingerprints; stacks coarsened; evidence references dropped).
  `promote --include-community` lets contributed records corroborate across the promotion
  bar. Contributions arrive by pull request under `community/observations/`, so review is the
  trust gate, and their fingerprints are recomputed on load rather than trusted.
- **`PRIVACY.md`** stating what the store holds and what leaves the machine, which is nothing
  unless you export and choose to share it.
- A weekly workflow that surfaces promotion candidates in a standing issue. It never adopts.
- **Golden-fixture evals** (`evals/`) run in CI: one truthful platform that must pass at every
  profile, one with deliberate defects that must each be caught.
- `reviewedBy` on capabilities, checked against `owner` at `regulated`, so "reviewed by
  someone other than the implementer" is verifiable rather than asserted.
- Enforcement of `platform.researchSources[].appliedTo` and `declaredStatic[].value`.
- Evidence-token matching extended from capability tests to screen tests at `regulated`.

### Changed

- **Relicensed to Apache-2.0** from MIT, for the explicit patent grant and the explicit
  refusal to grant trademark rights. Adds `NOTICE` and SPDX headers.
- `gaps[]` now excuses an unreachable lifecycle state as well as an unobservable one. A state
  entered only by an external system — a payment webhook — can be explained rather than only
  suppressed.
- Exit-code semantics for a refused `add`/`set` documented in the script and `SKILL.md`.

### Fixed

- Removed a dead `if False else None` expression in the lifecycle-transition loop.


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
