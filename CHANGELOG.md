# Changelog

All notable changes to this project are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

A note on what "breaking" means here: a change is breaking if a manifest that validated
cleanly before now fails. Tightening a check is therefore usually breaking, and lands in a
major version or behind a profile.

## [Unreleased]

### Added

- Optional integrations with two external tools, both soft (absence changes nothing and
  blocks nothing), documented in `SKILL.md` and the new `INTEGRATIONS.md`:
  - **DesignArchitect** (UI closure): the architect agent detects it at Phase 4
    (`DESIGN_ARCHITECT_HOME` or a sibling checkout), feeds the manifest's screens,
    capabilities, and states to its spec miner, and accepts closure only when
    `.design-architect/holes.json` is empty and `handoff/coverage.md` reports
    `holes_remaining: 0`. The downstream contract is affordance coverage — every control
    resolves to a real destination, every state exists — never the prototype's visual
    design, which the project's design system overrides. Recorded in
    `agents/adminwright-architect.md` and `agents/adminwright-implementer.md`.
  - **Alibaba Open Code Review** (line-anchored review): `scripts/code_review.py` —
    three subcommands (`diff`, `scan`, `record`), delegate engine by default (no
    OCR-side API key: OCR selects files and resolves rules, the reviewing agent
    reviews, and findings persist as `gaps[]` through `add --kind gap` with mandatory
    file accounting), endpoint engine optional (`ocr review/scan --audience agent
    --format json`, needs `ocr config provider`). Exit codes follow the skill
    convention (0 clean, 1 findings, 2 usage/IO; 3 intentionally unused). Ships
    `assets/adminwright-ocr-rules.json` and 13 regression tests with a faked `ocr` —
    no network. The security and qa agents carry the pass in "The pass";
    `references/verification.md` gains "External review pass (optional)". Findings
    are judgments with guaranteed file coverage — evidence, never a gate replacement.
- `ocr-advisory.yml`: non-blocking advisory OCR review of pull requests via the official
  GitHub Action, skipped until `OCR_LLM_URL`/`OCR_LLM_TOKEN` secrets exist, never a
  required check; documented in `CONTRIBUTING.md` under "Advisory OCR review".
- Concurrent writes to one manifest are now safe on Windows, which the multi-agent model
  has always assumed and never tested. Eight concurrent `add` calls lost roughly one in
  six to two distinct races, both pre-existing:
  - `write_text_file` staged through a shared `<name>.tmp` and called `os.replace` once.
    Windows refuses that rename with Access Denied while any other process holds the
    destination open — an unlocked reader is enough, and the write lock cannot prevent
    one. The staging file now carries the pid and the rename retries.
  - `FileLock` retried on `FileExistsError` but treated every other `OSError` as fatal,
    so a lock attempt landing while the previous holder's unlink was still pending died
    on `[Errno 13]` instead of waiting. `PermissionError` is now a contention state and
    retries to the deadline.
  `code_review.py` also re-reads taken gap ids and retries when a concurrent `record`
  claims one first, disambiguating retry candidates by pid so contenders cannot converge
  on the same next id, and says so rather than blaming the payload. Regression tests
  cover eight-way contention on both scripts; 40 stress trials now pass clean.
- `code_review.py` keeps the line anchor when a finding's `start_line` arrives as a
  digit string — the form LLM-authored JSON routinely emits. It was silently dropped,
  recording `path:?` and discarding the one thing this pass guarantees. Booleans are
  still not line numbers.
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
- **OCR bridge: malformed payloads no longer crash with exit 1.** A finding missing its
  `path`, a findings list holding a bare string, `--findings @missing-file`, and a corrupt
  `ocr-bundle.json` all raised unhandled exceptions — Python exit 1 with a traceback, which
  the documented exit-code table defines as "findings recorded", so an automated caller
  would read the crash as success. All four now surface as `ERROR: …` with exit 2
  (`scripts/code_review.py`).
- **`ocr-advisory.yml` failed validation on every pull request.** The job-level `if` read
  the `secrets` context, which GitHub only allows at step level — every PR got
  "Unrecognized named-value: 'secrets'" instead of the documented skip. The secrets are now
  mapped into job `env` and the steps gate on that; the action is pinned to a release SHA
  (`v1.9.6`) instead of `@main`, matching the SHA-pinning the action applies to its own
  internals — `OCR_LLM_TOKEN` no longer follows whatever lands on the branch.
- **`coverage_rate` lied in both directions.** It divided by bundle *entries* while the
  gate counts unique *paths* — a workspace-mode duplicate (staged deletion + untracked
  recreation) legitimately satisfied the gate yet printed `coverage_rate=50%`. And nothing
  checked that a reported path was in the bundle at all — a finding for an invented path
  was persisted and pushed the rate to `100%`. Coverage now counts unique bundled paths,
  and findings/skips for paths outside the bundle are refused (exit 2, nothing persisted).
  The bundle instructions now tell the reviewing agent the same thing.
- **"Exit 2 leaves the manifest unmodified" is now actually true.** `record` wrote gaps one
  at a time and raised on the first refusal, so a multi-finding payload could exit 2 with
  earlier gaps already persisted. Findings are now written as one atomic batch:
  `admin_console_manifest.py add --json` accepts an array, validated in full before
  anything is appended (one bad id refuses the whole batch), and `code_review.py` picks
  collision-free ids up front — which also removes the five-attempt retry budget whose
  sixth failure ("the manifest refused the gap") explained neither cause nor remedy.
  A refused `record` can now never duplicate on retry.
- A `delegate preview` entry without a path is refused loudly instead of silently dropped
  — a dropped file left the coverage gate satisfied for a file nobody reviewed.
  `find_findings` prefers lists under known keys (`comments`/`findings`/`results`) so a
  document echoing rule groups or file lists cannot be mistaken for findings. `--repo` is
  now placed before the positional file list, where no parser can swallow it as a
  positional. An unknown severity is still refused (it maps 1:1 onto gap severities); an
  unknown category degrades to `other` with a visible notice — taxonomy drift in real OCR
  output should not discard a real finding.
- A `@unittest.skipUnless` smoke test validates the flags and subcommands the bridge and
  `INTEGRATIONS.md` rely on (`--audience`, `--format`, `--rule`, `config set`,
  `rules check`) against a real `ocr` install when one is present; every other test fakes
  the binary, so nothing else catches a renamed flag.


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
