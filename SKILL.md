---
name: adminwright
description: Designs, builds, extends, audits or repairs a real admin console (admin panel or dashboard, back office, internal operations tool where staff act on customer or platform data, support or moderation tooling, ops or superadmin area, admin API, CRUD dashboard) for any platform and stack, backed by live data, server-side authorization, audit trails and tests instead of mock screens. Use when an admin surface must be inferred from a platform's domain, data model, APIs, roles, lifecycles and risks; when several agents build one console; or to audit an existing admin system for being connected, secure, auditable, accessible and operationally complete, and repair what the audit finds. Not for customer-facing product features, analytics-only charts, or developer scripts that no operator uses.
license: Apache-2.0
compatibility: Python 3.9+ (standard library only) for scripts/. Any agent harness that can run shell commands.
---

# Adminwright

Build the platform's operational control plane, not a decorative analytics page. An admin
capability is complete only when the authorized operator can observe the relevant state,
understand it, act on it through a real backend operation, receive truthful feedback, recover
safely, and produce evidence of who changed what.

If you were dispatched as an adminwright role agent, your agent file is your contract: use
this file only for its reference table and the phase you were given.

## Non-negotiable contract

- Infer expected capabilities from the platform's nature. Do not wait for the user to
  enumerate obvious operational needs.
- Nothing loose. Every screen traces to a capability; every capability traces to a server
  operation, a server-side policy, an authoritative data source, an audit event where
  required, a test, and evidence. An orphan on either side is a defect.
- No mock, placeholder, stub, random, or hard-coded value in the release path. A value that
  is genuinely static by design is registered in `declaredStatic[]` with a reason and an
  approver; that registry is the only sanctioned exception.
- Identify the actual stack and read its authoritative documentation before implementing
  against it; record what you consulted in `platform.researchSources[]`. Guessing a
  framework's auth, policy, or job idioms is a defect.
- Never ship a control that is not connected to an authorized server-side operation, and
  never rely on hidden navigation or client-side checks as authorization.
- Prefer safe recovery over irreversible deletion. Add previews, confirmations, reason
  capture, step-up authentication, approvals, or undo according to risk.
- The agent that implements a capability never marks it reviewed. Review is a separate pass
  by a different agent, or an explicitly declared pass that re-reads the code.
- Do not declare completion from screenshots. Use the manifest, automated checks, and
  browser evidence.
- Preserve the project's architecture and design system unless there is evidence that
  changing them is required.

## Conventions

`<skill-dir>` means `${CLAUDE_SKILL_DIR}`, the directory that holds this file;
`<project-root>` is the repository the console administers. The manifest, `<manifest>` in
commands, is `<project-root>/.admin-console/manifest.json`, changed only through
`python <skill-dir>/scripts/admin_console_manifest.py` (`py -3` or `python3` where `python`
is not on PATH). Take arguments from `<command> --help`, never from the script's source.
Exit 0 is clean, 1 means findings at error severity, 2 a usage failure or a write
the guard refused (nothing was written), 3 a claim conflict.
[manifest-cli.md](references/manifest-cli.md) has every command form, shell quoting, what
to commit, and monorepo layouts.

## Completion report

Every mode ends with Phase 8 and then this report. Lead with operational outcomes, then:

- Profile selected and why
- Domains and workflows now controllable
- Roles and scopes supported
- Real systems and integrations connected
- High-risk safeguards and audit coverage
- Automated and browser verification performed
- Manifest validation and coverage results
- Lessons recorded
- Remaining blocked, deferred, or not-applicable items with rationale

Never use "enterprise-grade", "complete", or "production-ready" without evidence from the
release gates.

## Workflow

Copy this checklist into your worklog and tick it as each exit condition holds:

```text
- [ ] Mode stated; profile proposed (the owner confirms it after Phase 1)
- [ ] 1 Discover: stack docs read, listed in platform.researchSources[]
- [ ] 2-3 Model: roles and capabilities before screens; validate --phase plan exits 0
- [ ] 4 Design: navigation by operator job; every metric's x-metric filled
- [ ] 5 Build: walking skeleton, agent contract written, then each capability through all ten steps
- [ ] 6 Harden: safeguards, audit and states by risk
- [ ] 7 Verify: validate --phase release and coverage exit 0; gap report; adversarial pass
- [ ] 8 Learn: harvest and promote run; completion report written
```

## Select the operating mode

- **Build:** a new admin console or a major module, end to end.
- **Extend:** new capabilities in an existing console, preserving its policies and component
  vocabulary. With no manifest, run Phase 1 only far enough to `init`, and model the
  existing console at `discovered`. Skip Phase 4, `add` and build the new capabilities, and
  hold them to Phase 7: no release finding may name them, though the whole-console gate
  passes only once nothing is left `discovered`.
- **Audit:** compare an existing console with the domain and implementation, changing no
  product code but leaving durable artifacts: the manifest (initialized if absent) models what
  you found at status `discovered`, implementation fields included, with every gap in
  `gaps[]` and every observation about this skill in `feedback[]`; `validate --phase plan`
  exits 0; the report comes from `emit --format gap-report --out
  <project-root>/docs/admin-gap-report.md` and ends by naming repair mode as the next step.
- **Repair:** fix chosen audit gaps through planned, claimed, reviewed slices; a partial
  repair reports per gap instead of declaring a release
  ([build-order.md](references/build-order.md#repairing-from-an-audit)).

If the request includes implementation, continue through
verification without stopping after planning. Operator surfaces may be the admin UI, an admin
API, a CLI, a scheduled job, or a chat/ops bot (a capability's `surface`).

## Select the profile

Record one in the manifest. It scales gate severity, not honesty: a lower profile lowers the
gate, never the truth requirement.

| Profile | Use when | Consequence |
|---|---|---|
| `internal` | A small internal tool with named operators | Quality rules warn rather than block; the accessibility and performance gates, and the release flags for MFA, tamper-protected audit, keyboard and responsive checks, may be not applicable with a recorded reason ([list](references/verification.md#profiles-and-the-gate-matrix)); `standard` and `regulated` require them |
| `standard` | Any console real users depend on | Evidence must resolve; all gates must pass |
| `regulated` | Money, health, minors, or audited data | Adds test-token matching, separation of duties, privileged-read audit, and forbids unresolved assumptions |

`init` records the profile as an assumed decision, `dec.profile`. Only a person confirms a
decision or accepts a gap: propose, record `assumed`, and ask.

## Load references by phase

Load only what the current phase needs, and only the named section when a link names one.

| Entry point | Minimum set |
|---|---|
| Full build | Phases 1–8 below, loading as each begins |
| Narrow extension | [discovery.md](references/discovery.md), [stack-adapters.md](references/stack-adapters.md), plus the reference for the changed capability |
| Audit only | [discovery.md](references/discovery.md), [capability-catalog.md](references/capability-catalog.md), [verification.md](references/verification.md), [security-governance.md](references/security-governance.md), [experience-design.md](references/experience-design.md#ux-acceptance-checks) |
| Repair | [verification.md](references/verification.md), [build-order.md](references/build-order.md#repairing-from-an-audit), plus the reference covering each finding |
| Release declaration | [verification.md](references/verification.md) and [security-governance.md](references/security-governance.md), always |

## Phase 1: Discover the platform

Load [discovery.md](references/discovery.md). Inspect before inventing: reconcile docs,
schemas, state machines, APIs, jobs, integrations, auth policies, tenant scoping, existing
admin surfaces, and the support scripts and manual SQL that reveal missing controls.

Identify the stack and read its documentation ([stack-adapters.md](references/stack-adapters.md),
[resource-index.md](references/resource-index.md)). Current behavior is evidence, not
automatically the desired design. Classify the platform with archetypes from
[capability-catalog.md](references/capability-catalog.md), and run
[buy-vs-build.md](references/buy-vs-build.md) before designing screens: a framework changes
who writes the code, never the contract.

## Phase 2: Model the control plane

If the manifest already exists, do not `init`: read it, read every worklog whose
`agents[].status` is `handed-off`, run `validate --phase plan`, and resume at the first phase
whose exit condition fails. Otherwise create it:

```text
python <skill-dir>/scripts/admin_console_manifest.py init --project-root <project-root> --name "<platform name>" --archetype <archetype> --profile <profile>
```

Add in dependency order, roles, then entities, then capabilities, then screens: the write
guard refuses a reference to anything not yet added. Imitate the field shapes of
`<skill-dir>/evals/fixtures/saas-clean/.admin-console/manifest.json`, a complete manifest
that passes at `regulated`. The fields each capability needs, and when, are in
[verification.md](references/verification.md#capability-fields-the-validator-requires).

```text
python <skill-dir>/scripts/admin_console_manifest.py add --manifest <manifest> --kind role --json '{"id":"ops","name":"Operations","responsibilities":["Account support"],"scopes":["all"]}'
python <skill-dir>/scripts/admin_console_manifest.py add --manifest <manifest> --kind entity --json '{"id":"user","name":"User","sourceOfTruth":"postgres:users","lifecycleStates":["active","suspended"],"retention":"7 years"}'
python <skill-dir>/scripts/admin_console_manifest.py add --manifest <manifest> --kind capability --entity user --json '{"id":"user.list","outcome":"Find an account","kind":"query","roles":["ops"]}'
python <skill-dir>/scripts/admin_console_manifest.py set --manifest <manifest> --path 'entities[user].capabilities[user.list].status' --value planned
```

Use `add`, `set`, `remove` and `rename`, never a hand edit: their write guard refuses malformed edits while they are cheap to fix, and an unfinished model only
warns until release. Populate from evidence, not guesses. Leave an implementation field empty
rather than filling it with placeholder text: the scanner treats placeholders as defects, and
an empty field is honest. `not-applicable` needs a recorded rationale and `blocked` the exact
external dependency; neither may hide unfinished work.

## Phase 3: Derive capabilities

For every entity and workflow answer what an operator must notice, decide and do; what can
fail, stall, conflict, expire, duplicate or be abused; how they find the records; which
scopes apply; how they recover; and what evidence must remain.

Do not create CRUD mechanically. Some entities are read-only; others need transitions,
assignment, approvals, reconciliation, replay, suspension, redaction, or restoration rather
than arbitrary editing. Use the catalog to check domain-obvious modules and apply its
completeness test; document why each expected module is included, deferred, or not
applicable.

Finish roles and capabilities before adding a single screen: a screen is a view onto
capabilities that already exist. Run `validate --phase plan` before Phase 4; an entity whose
lifecycle no command reaches, or a screen with no capability, is an unfinished model, not a
design choice.

## Phase 4: Design the information architecture

Load [experience-design.md](references/experience-design.md).

Organize navigation around operator jobs and bounded domains, not database tables. Make the
landing page an exception and decision surface. Keep high-risk settings and security
administration distinct from routine operations. Preserve context between overview, filtered
list, record detail, related records, history, and action result.

Every metric is a query capability whose `x-metric` declares its owner, threshold,
freshness, drill-down and action ([discovery.md](references/discovery.md#6-decision-to-metric-map)).
Remove metrics that support no action.

## Phase 5: Build the spine, then the slices

Load [build-order.md](references/build-order.md) and
[admin-data-model.md](references/admin-data-model.md).

Build the walking skeleton first (authentication, the role and scope spine, the audit spine,
one read-only list on real data, one low-risk command end to end): retrofitting
authorization, tenancy and audit onto finished screens is a rewrite, not a patch. Then write
[assets/agent-contract.template.md](assets/agent-contract.template.md), filled in, into the
project's agent instruction file (`AGENTS.md`, `CLAUDE.md` or the equivalent): a skill
governs a session, the contract governs the repository.

For each capability afterward:

1. Confirm the authoritative data source and invariants.
2. Implement or reuse the server-side query or command.
3. Enforce authorization and tenant scope on the server.
4. Handle validation, concurrency, idempotency, transactions, and async execution.
5. Emit audit events for privileged reads and mutations as policy requires.
6. Add structured logs, metrics, traces, alerts, or reconciliation evidence where
   operationally important.
7. Build the interface using the project's design system.
8. Cover every applicable state (loading, empty, populated, filtered-empty, validation,
   conflict, error, forbidden, partial/stale, success). Release requires `loading`,
   `populated`, `error` and `forbidden` on every screen, and `success` wherever there are
   actions.
9. Add unit, integration, authorization, contract, and browser tests proportional to risk.
10. Update the manifest with real implementation and evidence paths.

Do not mark a capability implemented if any required layer is simulated or missing.

## Phase 6: Harden for privileged use

Load [security-governance.md](references/security-governance.md) and
[architecture.md](references/architecture.md).

Apply least privilege and default deny, tamper-evident audit, per-target authorization in
bulk operations, safe impersonation and exports, approval or idempotency by risk, keyboard
operation, locale handling, performance budgets, and degraded states, as those references
specify.

## Phase 7: Verify and close gaps

Load [verification.md](references/verification.md) and
[test-data.md](references/test-data.md).

Seed production-shaped fixtures first, every lifecycle state and cross-tenant neighbour
included.

Run the project's build, type check, lint, tests, security checks, and browser tests. Then:

```text
python <skill-dir>/scripts/admin_console_manifest.py validate --manifest <manifest> --phase release
python <skill-dir>/scripts/admin_console_manifest.py coverage --manifest <manifest>
python <skill-dir>/scripts/admin_console_manifest.py emit --manifest <manifest> --format gap-report --out <project-root>/docs/admin-gap-report.md
```

Both `validate` and `coverage` must exit 0 before any release declaration. `emit` also writes
`authz-matrix`, `test-plan`, `seed-plan`, `nav-map` and `operator-handbook`.

The `qa` pass, which did not implement the capability, signs it: `set reviewedBy <its id>`,
then `reviewStatus reviewed`, refused while an `open` or `blocked` critical or high gap
targets it. A `contested` capability returns to `unreviewed` through its implementer's fix,
never straight to `reviewed`.

Then an agent that did not implement the work runs the
[adversarial pass](references/verification.md#adversarial-qa-pass), attacking the console
rather than exercising it: missed obligations, work still done by script or SQL, controls
without operations, guessed identifiers, cross-tenant reads, replayed commands.

A green validate is a floor, not a proof. It cannot tell whether a test asserts the right
thing or a policy is correct. Those remain yours.

## Phase 8: Record what the build taught

Load [skill-evolution.md](references/skill-evolution.md).

Record observations as you work in `feedback[]`, each with a `scope`: the one file or area of
the skill it would change, as a path from the skill root (`SKILL.md`, `references/<file>.md`,
`scripts`, `agents`). At the end, harvest them into the cross-project store and see what has
earned promotion:

```text
python <skill-dir>/scripts/admin_console_manifest.py harvest --manifest <manifest> --date <YYYY-MM-DD>
python <skill-dir>/scripts/admin_console_manifest.py promote
```

With subagents, dispatch the harvester and give it a digest of the conversation (the user's
corrections, guidance that proved wrong, skipped phases): a dispatched agent cannot see the
chat.

## Working with other agents

Load [multi-agent.md](references/multi-agent.md) when more than one agent will touch this
console, and when one agent runs the roles in sequence.

Six agents ship with the skill: five claimable roles in pass order (architect, implementer,
ux-reviewer, security, qa) and the harvester, a learning pass that never registers in
`agents[]`. In Claude Code they are `adminwright:adminwright-<role>`; elsewhere,
`install_agents.py --harness <name>` installs them ([agents/README.md](agents/README.md)).

Coordination goes through the manifest and its locks, never through conversation or a
harness's messaging. Claim before you build (`claim --agent <id> --role <role> --capability
<id>`; exit 3 means it is held). The orchestrating session verifies each pass itself
(`validate --phase plan` after the architect, `review-scope` after each review, the release
gates after qa) and never edits product code, because its fixes would skip review.

## Optional integrations

DesignArchitect (Phase 4) and Open Code Review (Phase 7, audit) sharpen the skill when
present and change nothing when absent; record their use in `decisions[]`. See
[INTEGRATIONS.md](INTEGRATIONS.md).
