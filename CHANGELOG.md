# Changelog

All notable changes to this project are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

A note on what "breaking" means here: a change is breaking if a manifest that validated
cleanly before now fails. Tightening a check is therefore breaking. This project does not tag
versions -- `main` is the release channel (see CONTRIBUTING.md) -- so every tightening is
recorded here under the rule id it changes, and `validate` prints, and `validate --json` and the
manifest's `validatedWith` carry, a `rulesetVersion` hash of the rule table. A transcript
therefore says which rule set judged it, even on an install that updated in between.

## [Unreleased]

### Added

- Two evidence-rigor rules, both `warn` at `internal` and `error` at `standard` and
  `regulated`. Tightening, so it is breaking by this project's definition — a manifest that
  passed on empty tests or hollow evidence now fails:
  - `test-asserts-nothing` — a test file listed under a capability, screen, work queue, or
    `policyTests` must contain an assertion. Field-tested against this repository's own
    `saas-clean` fixture, which passed release at the **regulated** profile with two
    `test('...', () => {})` bodies. An empty test compiles, runs, passes, and proves nothing.
  - `evidence-uninformative` — evidence whose entire content is a single short claim of
    success ("command completed, exit 0") is refused. A real run leaves counts, names, or
    routes behind. The same fixture's accessibility and browser logs were eleven words each.
- Both eval fixtures now carry evidence a reader can check and tests that actually assert,
  because a fixture named `saas-clean` defines what clean means and taught the opposite.
  Their expected findings are unchanged: the new rules add signal without altering what
  either fixture proves.
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
- `tests/test_plugin_packaging.py` locks the auto-update contract: the plugin and
  marketplace files must declare no `version`, because Claude Code resolves a git-sourced
  plugin's version from the commit SHA only while that field is absent. Adding one pins
  every install to a hand-bumped string, and a forgotten bump is invisible from this side.
  The same file checks that every agent the plugin advertises exists and carries
  frontmatter — a renamed agent installs cleanly and simply goes missing for the user.
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
- Packaged as a Claude Code plugin marketplace (`.claude-plugin/`): install once, and every
  push to `main` propagates automatically because the plugin is versioned by commit SHA
  rather than a hand-bumped version field.
- `init` warns when an archetype does not resolve to a known key and lists the known keys.
  Common money words (`financial`, `finance`, `trading`, `crypto`, `investing`) now alias to
  `fintech`. From the field: a trading platform typed `--archetype financial` and coverage
  checking silently never ran (lesson 0003).
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
- **New validator rules from the 2026-09-23 audit.** Each is a tightening, so by this
  project's definition breaking: a manifest that passed before can now fail. Severities are
  `internal / standard / regulated`; the exact table is generated into
  [`references/rule-matrix.md`](references/rule-matrix.md).
  - `manifest-type` (error everywhere) now covers EVERY field the schema types, via a
    schema-driven walker: `"tests": "x.spec.ts"` and `"evidence": true` used to be non-empty
    and skipped every evidence check.
  - `unknown-field` (warn / error / error): a key the schema does not declare, so a typo is
    refused instead of silently persisted by `add` and `set`.
  - `evidence-inside-manifest-dir` (error everywhere): nothing under `.admin-console/` counts
    as a test or as evidence.
  - `test-file-shape` (warn / error / error): a cited test must look like one and assert.
  - `evidence-is-test-file` (warn / error / error): a test file is not gate evidence.
  - `evidence-shared` (off / warn / error): one file for more than two gates, or for a gate
    and a `crossCutting` section.
  - `gate-evidence-unrelated` (off / warn / error): a gate's evidence must mention the gate id
    or a word from its optional `command`.
  - `test-file-shared` (off / warn / error): one test for more than three capabilities, or
    for capabilities, screens and `policyTests` at once.
  - `declared-static-forbidden-path` (error everywhere): `declaredStatic` cannot exempt
    `dataBinding`, `sourceOfTruth`, `serverOperations`, `authorizationPolicies`,
    `auditEvents`, `tests`, `evidence`, `dataSources`, `policyTests` or `uiRoutes`.
  - `declared-static-self-approved` (off / warn / error): the approver is an agent, or the
    capability's own owner, implementer or reviewer.
  - `implementer-unrecorded` (warn / warn / error) and `contested-needs-gap` (warn / warn /
    error): an implemented capability must record who built it, and a `contested` review
    must be paired with a gap naming the capability.
  - `not-applicable-undecided` (off / warn / error): a `not-applicable` command, job or
    export must be named by a confirmed decision.
  - `screen-incomplete` (error at release, warn before): screens without `dataSources` or
    `states`, moved out of `required-field-empty` so the architect and the ux-reviewer can add
    them in turn.
  - `feedback-scope` (warn everywhere): an open feedback entry with no valid `scope`.
- New manifest fields: `capabilities[].implementedBy`, `.surface` (`ui`, `api`, `cli`, `job`,
  `chat`), `.entrypoints`, `.policyTests`; `agents[].previouslyOwned` and `.stolenFrom`;
  `qualityGates[].command`; `gaps[].source`, `.entity`, `.capability`;
  `feedback[].scope` and `.promotedHash`; `platform.id`; top-level `projectRoot` and
  `validatedWith`. A headless capability (`surface` without `ui`) needs `entrypoints` and
  `policyTests` instead of `uiRoutes` and a screen, and a console with no UI capability needs
  no screen.
- Commands and options: `--version`; `validate --allow-lower-profile`; `add --json-file`;
  `emit --format rule-matrix`; `lesson index`; `init --manifest`; `set --path` accepts a
  `manifest.` prefix, array indexes and `evidence[0]`, so a finding's path can be pasted.
  `init` records `projectRoot`, writes `.admin-console/.gitignore`, and records the profile as
  an ASSUMED decision (`dec.profile`) that `regulated` release rejects until it is confirmed.
- The `validate` summary line and `--json` carry the installed commit and a `rulesetVersion`
  hash of the rule table; every `add`, `set`, `claim`, `release-claim`, `harvest` and
  `migrate --write` stamps `validatedWith` in the manifest. Third `evals/` fixture,
  `gate-matrix`, fires every advertised rule; `tests/test_rule_coverage.py` fails if a rule
  is in `RULES` but in no expectation. New tests: `test_release_gates`, `test_honest_work`,
  `test_windows_console`, `test_learning_loop`, `test_cli_correctness`, `test_doc_examples`
  (every documented `add`/`set` example runs), `test_schema_conformance` (against
  `jsonschema`, skipped where absent), `test_malformed_manifests`.
- Core schema pack: `assets/admin-core-schema/grants.sql` (least-privilege grants shared by
  every stack), `django_triggers.sql` and `prisma_raw.sql` (the triggers, functions, CHECKs and
  partial or expression indexes those two variants cannot express), a database-owned audit hash
  chain (`audit_event_chain_link` trigger) with `verify_audit_chain()`, `scrub_audit_payload()`
  as the one permitted payload update, and `tests/test_core_schema*.py` plus a CI job that
  applies `postgres.sql` on PostgreSQL 14 and 16.
- `install_agents.py` writes native agent files where each harness discovers them today:
  `.codex/agents/*.toml`, `.gemini/agents/*.md`, `.cursor/agents/*.md`,
  `.agents/agents/<name>/agent.md`; `.adminwright/agents/` stays as the harness-neutral copy.
- CI: Python 3.14 in the matrix, a Windows job with a cp1252 console, PyYAML and jsonschema
  installed as test-time checkers, an allowed-to-fail job that runs the real `ocr` binary, and
  Dependabot for the first-party actions.

- `remove --kind <kind> --id <id>` and `rename --kind <kind> --id <old> --to <new>`: delete an
  entry, or change an id and every reference to it (screens, transitions, agents, decisions,
  gaps, declared-static paths), under the write guard, so a wrong entry no longer has to be
  fixed by hand-editing the manifest. `set` takes a whole object as JSON.
- `tests/test_token_budget.py`: a ceiling on what each entry point (full build, narrow
  extension, audit, repair) and each agent prompt costs to load.
### Changed

- The `ux-reviewer` agent now reviews what the validator provably cannot: that every control
  actually does something when pressed, that the screen renders without visual defects, and
  that the console is structurally coherent across a domain. It mentioned design, layout,
  or structure twice before, which is why broken components and incoherent organisation kept
  reaching users through a green gate. `references/verification.md` states the boundary in
  the same terms.
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
- Audit mode must leave durable artifacts — initialize the manifest, model findings at
  `discovered`, record `gaps[]` and `feedback[]`, write the gap report to a file. From the
  field: two audit sessions produced thorough chat reports and nothing else, so the learning
  loop never started (lesson 0002).
- **Relicensed to Apache-2.0** from MIT, for the explicit patent grant and the explicit
  refusal to grant trademark rights. Adds `NOTICE` and SPDX headers.
- `gaps[]` now excuses an unreachable lifecycle state as well as an unobservable one. A state
  entered only by an external system — a payment webhook — can be explained rather than only
  suppressed.
- Exit-code semantics for a refused `add`/`set` documented in the script and `SKILL.md`.
- **Validator tightenings from the 2026-09-23 audit** (each breaking by this project's
  definition; severities in `references/rule-matrix.md`):
  - `evidence-token-match` no longer counts a capability's `uiRoutes`: every capability on a
    screen shares its route, so one route-smoke test matched all of them. At `regulated` a
    test file cited by N capabilities needs at least N assertions (`test-asserts-nothing`).
  - The assertion pattern is anchored and comment-stripped: `import { assert }`,
    `// add assertions later` and `jwt.verify(...)` no longer count.
  - `reviewer-identity` compares `reviewedBy` with `owner`, `implementedBy` and every
    implementer that ever held the capability, so the documented `release-claim` handoff can
    no longer launder a self-review. `claim` stamps `implementedBy`; a reviewer's claim no
    longer overwrites `owner`; an id that built capabilities cannot claim as a reviewer.
  - `validate --phase release --profile <lower than the manifest's>` is refused (exit 2)
    unless `--allow-lower-profile` is passed.
  - The lifecycle escape needs an open or accepted gap, not filed by the OCR bridge, that
    names the state and the entity (or carries an explicit `entity`/`capability` field);
    previously any gap mentioning a state name silenced the check for every entity.
  - Placeholder folding also strips zero-width and other format characters, NFKD-normalises
    fullwidth and mathematical letters, and maps Cyrillic `к` and more look-alikes.
  - The substring scan judges one whitespace-delimited token at a time (prose such as
    "Rotation of a key" is no longer `fake`); test files are never placeholder-scanned;
    evidence files are scanned by whole word, without `sample` and `random`, skipping
    framework idioms; `declaredStatic` path `<evidence path>#content` exempts one file.
  - `set` stores strings as written (`"true"` and `"null"` stay strings), takes strict JSON for
    lists and objects, and refuses malformed JSON instead of comma-splitting it. `add`
    normalises `\` to `/` in evidence paths. `claim` also refuses (exit 3) when the manifest
    shows another active holder; `release-claim` of a capability the agent never held exits 3.
  - The write guard validates with the project root, compares findings as a multiset, and
    prints newly introduced warnings; `add`/`set` refuse unknown keys at `standard` and above.
  - The gap report's health line is judged at release and drops its caveat when clean.
- `emit`, `report` and the emitters no longer crash on a role list that contains an object or
  a list-valued `kind`; any `OSError` is exit 2; `migrate` runs under the lock it writes with
  and reports a malformed 1.0 manifest as a usage error; it now adds every collection the 2.0
  schema requires.
- Learning loop: `lesson add` outside a git checkout (or under `plugins/cache`) writes to
  `$ADMINWRIGHT_HOME/outbox/lessons/`; `harvest` writes the store before marking feedback
  promoted and re-banks an entry edited afterwards; `store sync` takes the store lock, writes
  the canonical sorted union, merges with `-s ours` and commits only the store files; the
  promotion bar counts distinct projects by `platform.id`; clustering is average-link with a
  floor on the overlap score, and `promote` prints each cluster's members; `lesson add` emits
  exactly the documented lesson format (quoted id, five headings, vocabularies enforced),
  rebuilds `lessons/index.md` from the files, and refuses `--status adopted`; lessons 0002 to
  0005 were backfilled into that format with neutral project labels.
- Store fingerprints are salted per store; `scrub_text` covers relative paths, any dotted
  domain, and AWS, GitHub and JWT token shapes. `PRIVACY.md` lists the optional integrations,
  including that the `ocr` launcher contacts npm unless `OCR_NO_UPDATE=1`.
- Every CLI test now runs the script under a cp1252 console instead of forcing UTF-8.
- README and CONTRIBUTING no longer claim a Claude Code plugin install "stays up to date":
  third-party marketplaces have auto-update off by default, so the docs give the update
  commands and `--version` shows which commit is running.
- Review agents (`ux-reviewer`, `qa`, `security`) carry `disallowedTools: Edit, NotebookEdit`
  and the harvester pins `model: opus`; each agent lists the plugin-cache and marketplace
  install paths in its skill-location fallback; the ux-reviewer records evidence under
  `crossCutting.experience` (there is no `experience` gate); each `crossCutting` section has
  one owning agent; "release claim" (prose) is now "release declaration" so it cannot be
  confused with the `release-claim` command.
- Canonical core schema: `approval_decision` refuses DELETE and TRUNCATE and its `request_id`
  is `ON DELETE RESTRICT`; `config_setting` and `feature_flag` are unique per
  `(key, environment, tenant)`; `feature_flag` and `config_change` gain `tenant_id`;
  `impersonation_session` gains `read_only`, a maximum-duration CHECK and one live session per
  actor; `audit_event.effective_actor_id` is `text` with no FK; `postgres.sql` creates
  `citext` and names every CHECK `<table>_<column>_check`. Django: `condition=` for Django 6,
  23 CHECKs, `db_column` names, `db_default`. Prisma: valid on Prisma 7, explicit `onDelete`,
  canonical enum strings, raw SQL written out.
- `code_review.py`: OCR bundles are per-run files closed by a successful `record` and ignored
  after 24 hours; `record` without a bundle reports `coverage: unknown`; findings already
  recorded as live gaps are not written twice; `--project-root` defaults to the manifest's
  project root; every `ocr` call is bounded by a timeout; OCR-recorded gaps carry
  `source: "ocr"`. The advisory workflow pins the npm package (`ocr_version: 1.9.6`).
- `install_agents.py` names the skill relative to the project when it lives inside it,
  otherwise as `${CLAUDE_PLUGIN_ROOT}` with the absolute path only in a comment, replaces the
  pointer block in place on reinstall, and writes LF-only files.
- Adopted and shipped lessons no longer name real projects; the GitHub links to features that
  are not enabled (private advisories, Discussions) were replaced with a working contact route.

- References were corrected against current upstream documentation: Next.js `proxy.ts`
  (the `middleware` convention was renamed in Next 16), Supabase publishable and secret keys and
  Cron, DRF `has_object_permission` versus Django `ModelAdmin` hooks, the WCAG 2.2 criteria that
  matter to dense consoles, the six Dashboard Design Patterns genres, and a dead ASVS link.
  `security-governance.md` gains SSRF, rate limiting and lockout, break-glass, CSRF per stack
  family and SCIM/SSO deprovisioning, and ties "tamper-evident" to the hash-chain columns;
  `capability-catalog.md` gains the llm-gateway logging-policy, spend-cap, request-log and
  SSRF controls (with matching `ARCHETYPE_DOMAINS` keywords); `verification.md` links to the
  generated rule matrix instead of keeping a hand-written one, and defines the four required
  screen states once; `multi-agent.md` separates the manifest-level claim from the two kinds of
  lock file, states that the harvester is a pass, and uses one pass order (security before qa).
### Fixed

- `promote` grouped observations by an exact hash of their wording while its docstring
  promised it "groups observations that say the same thing in different words". It could
  only ever group a record with itself, so the "seen on two or more distinct projects"
  route — the main promotion route — was unreachable. Grouping is now content-word overlap
  within a matching scope and category, tunable with `--similarity`. Field-tested: the real
  store held one finding reported by two distinct projects in different words and reported
  "3 distinct ideas, nothing clears the bar"; it now reports one candidate, seen on two
  projects. That finding is adopted as lesson 0005 — roles and capabilities are modelled
  before any screen — and `SKILL.md` Phase 3 carries the rule.
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
- **Release-gate bypasses (audit 2026-09-23).** Each was reproduced at
  `validate --phase release --profile regulated` with exit 0 and no findings: non-list
  `tests`/`evidence` values, a mock `dataBinding` covered by `declaredStatic`, a `package.json`
  and a README standing in for every gate, one route-smoke test matching every capability and
  screen, the manifest itself cited as a test, a self-review after the documented `release-claim`
  handoff, `--profile internal` on a regulated manifest, and a deferred capability relabelled
  `not-applicable`.
- False blocks: prose such as "Rotation of a key", "nothing left to do" and "test-ubuntu-latest"
  was a placeholder error; `jest.mock(` and `getByPlaceholderText` in test files, and "1200
  samples" in a log, were errors; a gate could never be `set` to `passed` with a relative
  evidence path because the write guard validated without a project root; four documented
  `add`/`set` examples were refused verbatim; a headless capability could not be marked
  implemented; recording a rejected catalog candidate as `not-applicable` blocked release.
- Windows: `validate`, `report`, `emit`, `coverage`, `harvest`, `promote` and `lesson list`
  crashed with a traceback on any character cp1252 cannot encode when stdout was a pipe (and
  `harvest` crashed after it had already written); PowerShell 5.1's BOM broke `--json @file`;
  `code_review.py record` crashed at about 60 findings (argv limit); a repository file named
  `x&y.py` executed a command through the npm `ocr.cmd` shim.
- The QA agent's frontmatter held an unquoted `: `, which every strict YAML parser rejects, so
  the agent was silently absent from a marketplace install; packaging tests now parse every
  agent's frontmatter strictly.
- `implementer-unrecorded` was found unreachable by `tests/test_rule_coverage.py` (it fired
  only for contested capabilities) and was fixed in the same change.
- `harvest` stamped feedback `promoted` before the store write; `store sync` could delete
  observations that two devices held in a different order; concurrent `lesson add` allocated
  duplicate ids; a multi-line `--rule` corrupted a lesson's frontmatter.
- A stale `FileLock` could be "taken" by two waiters and released by the wrong one; a timeout
  said only "held by another process".
- `django_models.py` raised `TypeError` at import on Django 6; the Django live-grant unique
  index accepted duplicate global grants; Prisma defaulted every optional relation to
  `ON DELETE SET NULL`, erasing `audit_event.actor_id` when an actor was deleted; the core
  schema's README verification SQL passed vacuously on an empty table.
- `code_review.py` no longer reports "0 finding(s) recorded" for a review in which every file
  failed, and gap ids are no longer refused when the 40-character slug ends in `-`.

## [2.0.0] - 2026-08-07

(This release was never tagged, so there is no compare link; the commit history is the record.)

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
