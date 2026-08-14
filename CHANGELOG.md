# Changelog

All notable changes to this project are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

A note on what "breaking" means here: a change is breaking if a manifest that validated
cleanly before now fails. Tightening a check is therefore usually breaking, and lands in a
major version or behind a profile.

## [Unreleased]

### Added

- Six shipped subagents — one per coordination role (`adminwright-architect`,
  `adminwright-implementer`, `adminwright-ux-reviewer`, `adminwright-qa`,
  `adminwright-security`) plus `adminwright-harvester`, a learning pass that runs last on
  every mode, sweeps the manifest, worklogs, and a session-conversation digest for
  observations, and drives `harvest`/`promote`/`lesson add` so the skill improves from
  every run. Registered in the plugin manifest and documented in `agents/README.md`. Each
  agent file carries its role's entry and exit conditions, manifest protocol, boundaries,
  and handoff format, so a harness can dispatch a pass with no conversation history and the
  independence rule stays machine-checkable.
- `scripts/install_agents.py`: install the agents into any harness — Claude Code
  (non-plugin), opencode, Codex, Antigravity, Gemini CLI, Cursor, Pi, or generic. Resolves
  the skill path, replaces `${CLAUDE_PLUGIN_ROOT}` in the prompts, writes to the harness's
  agent directory, and emits (or idempotently appends) a pointer block for `AGENTS.md`-style
  instruction files. Stdlib only, with regression tests.
- `llm-gateway` archetype, in the catalog and the coverage checker, with aliases
  (`model-gateway`, `ai-gateway`, `llm-api`, `api-management`, `llmops`, `model-router`,
  `llm-proxy`). From the field: platforms whose product is managing LLM and third-party
  APIs kept receiving consoles with provider names but no way to add an API key, no base
  URL, no edit/delete, and no user management. The archetype's expected domains — provider
  registry, credential vault, model catalog, routing, platform-issued keys, usage and cost,
  user management — now make those absences a named coverage finding, and the catalog
  section spells out what "add a provider" must mean end to end.
- "User and member management" added to the catalog's common feature families: invite,
  create, edit, deactivate, delete, role assignment, credential reset, and session
  revocation through real server operations. A read-only user list is not user management.
- Harness-specific dispatch notes in the installer's pointer block, and a plan-of-record
  rule in the architect and implementer prompts. From the field: Antigravity drafted a
  fresh implementation plan even when the user supplied one — a supplied plan is now
  explicitly the plan of record, mirrored verbatim into any harness-required plan artifact
  rather than regenerated. Sequential harnesses (Codex, Gemini, Cursor, Pi) get pass-switch
  announcement rules.
- Human-first final reports: every agent now leads its final message with one
  plain-language paragraph (what operators can now do, what was found, whether the console
  is safe), before the evidence lists; the pointer block closes every run with the skill's
  completion-report format and its exit codes.
- The agents are now part of the learning loop's write surface: the harvester scopes
  lessons to `agents/adminwright-<role>.md` when the defect is in a role prompt, edits the
  agent file, and flags installed copies as stale until `install_agents.py` is rerun — so
  the agents themselves improve across projects, not just the references.

- Every `emit` surfaces manifest health: a stderr warning when plan validation finds
  errors, a health line inside the gap-report document, and a nudge when `feedback[]` is
  empty. From the field: an audit shipped a gap report while 83 validation errors sat
  invisible, because `emit` was the only command the session ever ran (lesson 0004).
- Audit guidance: build the manifest through `add`/`set` rather than hand-written JSON, and
  end the report by naming repair mode as the next step, so the audit turns into a build
  plan.

- Packaged as a Claude Code plugin marketplace (`.claude-plugin/`): install once, and every
  push to `main` propagates automatically because the plugin is versioned by commit SHA
  rather than a hand-bumped version field.
- `init` warns when an archetype does not resolve to a known key and lists the known keys.
  Common money words (`financial`, `finance`, `trading`, `crypto`, `investing`) now alias to
  `fintech`. From the field: a trading platform typed `--archetype financial` and coverage
  checking silently never ran (lesson 0003).

### Changed

- Audit mode must leave durable artifacts — initialize the manifest, model findings at
  `discovered`, record `gaps[]` and `feedback[]`, write the gap report to a file. From the
  field: two audit sessions produced thorough chat reports and nothing else, so the learning
  loop never started (lesson 0002).


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
