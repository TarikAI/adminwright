---
name: adminwright
description: Design, build, extend, or audit a complete admin console, admin dashboard, admin panel, back office, internal tool, control panel, ops console, superadmin area, or CRUD dashboard for any platform, in any stack. Use when an agent must infer the platform's administrative capabilities from its domain, repository, data model, APIs, roles, lifecycles, integrations, and risks; implement real end-to-end control surfaces backed by live data and authorized server operations rather than decorative dashboards or mock screens; coordinate several agents building one console; or verify that an existing admin system is connected, secure, auditable, accessible, resilient, and operationally complete. Ships six dispatchable role agents — architect, implementer, ux-reviewer, qa, security, and a harvester that learns from every run — installable into any harness (Claude Code, Codex, opencode, Antigravity, Gemini CLI, Cursor, Pi) via scripts/install_agents.py.
---

# Adminwright

Build the platform's operational control plane, not a decorative analytics page.

An admin capability is complete only when the authorized operator can observe the relevant
state, understand it, act on it through a real backend operation, receive truthful feedback,
recover safely, and produce evidence of who changed what.

## Non-negotiable contract

- Infer expected capabilities from the platform's nature. Do not wait for the user to
  enumerate obvious operational needs.
- Nothing loose. Every screen traces to a capability; every capability traces to a server
  operation, a server-side policy, an authoritative data source, an audit event where
  required, a test, and evidence. An orphan on either side is a defect.
- No mock, placeholder, stub, random, or hard-coded value in the release path. A value that
  is genuinely static by design must be registered in `declaredStatic[]` with a reason and an
  approver. That registry is the only sanctioned exception.
- Identify the actual stack and read its authoritative documentation before implementing
  against it. Record what you consulted in `platform.researchSources[]`. Guessing a
  framework's auth, policy, or job idioms is a defect.
- Never ship a control that is not connected to an authorized server-side operation.
- Never rely on hidden navigation or client-side checks as authorization.
- Implement vertical slices across data, service/API, authorization, UI, audit,
  observability, and tests.
- Prefer safe recovery over irreversible deletion. Add previews, confirmations, reason
  capture, step-up authentication, approvals, or undo according to risk.
- The agent that implements a capability never marks it reviewed. Review is a separate pass
  by a different agent, or an explicitly declared pass that re-reads the code.
- Do not declare completion from screenshots. Use the manifest, automated checks, and browser
  evidence.
- Preserve the project's architecture and design system unless there is evidence that
  changing them is required.

## Select the operating mode

- **Build:** create a new admin console or a major module end to end.
- **Extend:** add capabilities while preserving established policies and component vocabulary.
- **Audit:** compare an existing console with the domain and implementation, then report
  gaps. Do not modify the product's code — but the audit itself must leave durable
  artifacts, not only a chat report. Initialize the manifest if absent, model what you
  found at status `discovered`, record every gap in `gaps[]` and every observation about
  this skill in `feedback[]`, and write the report to a file:
  `emit --format gap-report --out docs/admin-gap-report.md`. A chat report evaporates with
  the session; the manifest is what lets the next agent repair instead of re-audit. Build
  the manifest through `add` and `set`, not by hand-writing JSON — the write guard catches
  malformed entries at the moment they are cheapest to fix. End the report by naming the
  next step: repair mode against this manifest, which turns the audit into a build plan.
- **Repair:** implement verified audit findings and update evidence.

State the mode in your worklog. If the request includes implementation, continue through
verification without stopping after planning.

Every mode ends the same way: `harvest` the manifest's `feedback[]` into the store. The
first field tests of this skill ran two audits and a build across seven projects and
harvested nothing — the learning loop cannot start from observations nobody recorded.

## Select the profile

Record one in the manifest. It scales gate severity, not honesty: a lower profile lowers the
gate, never the truth requirement.

| Profile | Use when | Consequence |
|---|---|---|
| `internal` | A small internal tool with named operators | Quality rules warn rather than block; accessibility and performance gates may be `not-applicable` with a recorded rationale |
| `standard` | Any console real users depend on | Evidence must resolve; all gates must pass |
| `regulated` | Money, health, minors, or audited data | Adds test-token matching, separation of duties, privileged-read audit, and forbids unresolved assumptions |

Choosing a profile is a recorded decision in `decisions[]`, not a default.

## Load references by phase

Do not read every reference up front. Load what the current phase needs.

| Entry point | Minimum set |
|---|---|
| Full build | Phases 1–8 below, loading as each begins |
| Narrow extension | [discovery.md](references/discovery.md), [stack-adapters.md](references/stack-adapters.md), plus the reference for the changed capability |
| Audit only | [discovery.md](references/discovery.md), [capability-catalog.md](references/capability-catalog.md), [verification.md](references/verification.md), [security-governance.md](references/security-governance.md) |
| Repair | [verification.md](references/verification.md) plus the reference covering each finding |
| Release claim | [verification.md](references/verification.md) and [security-governance.md](references/security-governance.md), always |

## Phase 1: Discover the platform

Load [discovery.md](references/discovery.md) for the evidence hierarchy, repository
archaeology, and the six discovery maps.

Inspect before inventing. Reconcile documentation, schemas and migrations, state machines,
public and internal APIs, queues and scheduled jobs, integrations, authentication and
authorization policies, tenant scoping, customer-facing routes that create administrative
obligations, existing admin surfaces, and the support scripts and manual SQL that reveal
missing control surfaces.

Identify the stack and read its documentation. Load
[stack-adapters.md](references/stack-adapters.md) for the seven seams to resolve, and
[resource-index.md](references/resource-index.md) for the authoritative sources.

Treat current behavior as evidence, not automatically as the desired design. Classify the
platform using one or more archetypes from
[capability-catalog.md](references/capability-catalog.md).

Before designing screens, run the buy-versus-build check in
[buy-vs-build.md](references/buy-vs-build.md). Adopting a framework changes who writes the
code, never the contract.

## Phase 2: Model the control plane

Create `.admin-console/manifest.json`:

```text
python <skill-dir>/scripts/admin_console_manifest.py init --project-root <project-root> --name "<platform name>" --archetype <archetype> --profile <profile>
```

Use `py -3` or `python3` if `python` is not on PATH. If Python is unavailable, copy
`assets/admin-console.manifest.template.json` and fill it manually against
`assets/admin-console.manifest.schema.json`, which types every field.

Model incrementally with `add` and `set`. Before release, an unfinished model reports gaps as
warnings rather than blocking — a role is unused until its first capability exists. Malformed
edits still block: bad ids, unknown references, and placeholder values are refused at write
time. `--allow-invalid` overrides that and prints exactly what it wrote through; anything it
waves past still fails the release gate.

Populate from evidence, not guesses: archetypes, tenancy, regulated data, stack, research
sources, volumes, roles and scopes, entities with lifecycle states and transitions, commands
and queries with risk and recovery, work queues, screens, integrations, cross-cutting
controls, quality gates, and decisions.

Use `status: not-applicable` only with a recorded rationale, and `blocked` only with the
exact external dependency. Neither may hide unfinished work. Leave implementation fields
empty rather than filling them with placeholder text — the scanner treats placeholders as
defects, and an empty field is an honest one.

Check the model as you go:

```text
python <skill-dir>/scripts/admin_console_manifest.py validate --manifest <project-root>/.admin-console/manifest.json --phase plan
```

## Phase 3: Derive capabilities

For every entity and workflow answer: what must an operator notice, what decision must they
make, what action must they take, what can fail or stall or conflict or expire or duplicate
or be abused, how do they find the affected records, what permissions and scopes apply, what
is the safe recovery path, and what evidence must remain.

Do not create CRUD mechanically. Some entities are read-only; others need transitions,
assignment, approvals, reconciliation, replay, suspension, redaction, or restoration rather
than arbitrary editing.

Use the catalog to check domain-obvious modules and apply its completeness test. Document why
each expected module is included, deferred, or not applicable.

## Phase 4: Design the information architecture

Load [experience-design.md](references/experience-design.md).

Organize navigation around operator jobs and bounded domains, not database tables. Make the
landing page an exception and decision surface. Keep high-risk settings and security
administration distinct from routine operations. Preserve context between overview, filtered
list, record detail, related records, history, and action result.

Every metric must declare its decision, source, freshness, owner, threshold, and drill-down
destination. Remove metrics that support no action.

## Phase 5: Build the spine, then the slices

Load [build-order.md](references/build-order.md) and
[admin-data-model.md](references/admin-data-model.md).

Order matters. Build the walking skeleton first — authentication, the role and scope spine,
the audit spine, one read-only list backed by real data, one low-risk command end to end —
before fanning out. Retrofitting server-side authorization, tenant scoping, and audit onto
twenty finished screens is a rewrite, not a patch.

For each capability afterward:

1. Confirm the authoritative data source and invariants.
2. Implement or reuse the server-side query or command.
3. Enforce authorization and tenant scope on the server.
4. Handle validation, concurrency, idempotency, transactions, and async execution.
5. Emit audit events for privileged reads and mutations as policy requires.
6. Add structured logs, metrics, traces, alerts, or reconciliation evidence where
   operationally important.
7. Build the interface using the project's design system.
8. Cover loading, empty, populated, filtered-empty, validation, conflict, error, forbidden,
   partial/stale, and success states as applicable.
9. Add unit, integration, authorization, contract, and browser tests proportional to risk.
10. Update the manifest with real implementation and evidence paths.

Do not mark a capability implemented if any required layer is simulated or missing.

## Phase 6: Harden for privileged use

Load [security-governance.md](references/security-governance.md) and
[architecture.md](references/architecture.md).

Apply least privilege and default deny; tamper-evident audit with actor, target, time,
reason, correlation ID, result, and safe before/after values; per-target-row authorization in
bulk operations; safe impersonation, export controls, PII redaction, retention, and consent
boundaries; approval or dual control for high-impact actions; optimistic concurrency;
idempotency for financial, messaging, provisioning, and integration commands; accessible
keyboard operation and focus management; locale, timezone, and currency handling; defined
performance budgets and pagination strategy; and production-grade loading, empty, error,
permission, maintenance, degraded, and offline states.

## Phase 7: Verify and close gaps

Load [verification.md](references/verification.md) and
[test-data.md](references/test-data.md).

Seed production-shaped fixtures first — every lifecycle state, long and localized text,
missing optionals, conflicts, partial job failures, and cross-tenant neighbours. A console
only ever seen with twelve tidy rows fails on contact with production.

Run the project's build, type check, lint, tests, security checks, and browser tests. Then:

```text
python <skill-dir>/scripts/admin_console_manifest.py validate --manifest <project-root>/.admin-console/manifest.json --project-root <project-root> --phase release
python <skill-dir>/scripts/admin_console_manifest.py coverage --manifest <project-root>/.admin-console/manifest.json --project-root <project-root>
python <skill-dir>/scripts/admin_console_manifest.py emit --manifest <project-root>/.admin-console/manifest.json --format gap-report
```

Exit 0 is clean, 1 means findings at error severity, 2 a usage or IO failure, 3 a claim
conflict. Both `validate` and `coverage` must exit 0 before any release claim. A refused
`add` or `set` also exits 2 — the manifest was left unmodified, so the request could not be
carried out; exit 1 always means "read it, and it has findings".

`emit` also produces the working documents the verification pass needs: `authz-matrix`,
`test-plan`, `seed-plan`, `nav-map`, and `operator-handbook`.

Then run the adversarial pass, by an agent that did not implement the work: compare
customer-facing capabilities against administrative obligations; compare schemas, services,
events, jobs, integrations, and flags against the manifest; find actions still performed
through scripts, database edits, or undocumented procedures; find controls without
operations and operations without control surfaces; find privileged operations without policy
tests or audit evidence; and attempt guessed identifiers, direct API calls, cross-tenant
reads, replayed commands, and out-of-scope bulk targets.

A green validate is a floor, not a proof. It cannot tell whether a test asserts the right
thing or a policy is correct. Those remain yours.

## Phase 8: Record what the build taught

Load [skill-evolution.md](references/skill-evolution.md).

Capture observations during the build in `feedback[]`. At the end, move them into the
cross-project store and see what has earned promotion:

```text
python <skill-dir>/scripts/admin_console_manifest.py harvest --manifest <project-root>/.admin-console/manifest.json --date <YYYY-MM-DD>
python <skill-dir>/scripts/admin_console_manifest.py promote
```

The store lives outside every project — `$ADMINWRIGHT_HOME`, or `~/.adminwright` by default —
so observations accumulate across every platform you build. `promote` groups ones that say the
same thing in different words and reports only those that clear the bar: seen on two or more
distinct projects, or a correction of guidance that was factually wrong. Single-project quirks
and style preferences belong in the project's agent contract file, not in this skill.

Record an accepted candidate, then edit the reference it names:

```text
python <skill-dir>/scripts/admin_console_manifest.py lesson add --title "<rule>" --category <gap|friction|incorrect-guidance|new-pattern|tooling> --scope references/<file>.md --trigger "<what happened>" --rule "<the durable rule>" --date <YYYY-MM-DD>
```

Adoption is a judgement call about guidance others will follow literally. Run it with a
capable model and a fresh read of the reference, not as an afterthought at the end of a long
session. In a harness with subagent support, dispatch `adminwright-harvester` for this phase
and pass it a short digest of the session's conversation — corrections the user made,
guidance that proved wrong, phases that were skipped — so learning that lives only in chat
history is banked before it evaporates.

## Working with other agents

Load [multi-agent.md](references/multi-agent.md) when more than one agent will touch this
console, and when a single agent runs the roles in sequence.

Six agents ship with the skill — one per role plus a learning pass:
`adminwright-architect`, `adminwright-implementer`, `adminwright-ux-reviewer`,
`adminwright-qa`, `adminwright-security`, `adminwright-harvester`
([agents/README.md](agents/README.md) has the dispatch order). Each carries its role
contract and needs no conversation history. In a harness with subagent support, dispatch
them; in any other harness, install them with
`python <skill-dir>/scripts/install_agents.py --harness <name> --project-root <root>` and
run the passes sequentially with distinct agent ids.

Coordination happens through the manifest and lock files, never through conversation
history. Claim before you build:

```text
python <skill-dir>/scripts/admin_console_manifest.py claim --manifest <project-root>/.admin-console/manifest.json --agent <id> --role <architect|implementer|ux-reviewer|qa|security> --capability <id>
```

Exit 3 means another agent holds the claim. Mutate the manifest only through `add` and `set`;
rewriting the whole file discards concurrent work.

## Persist the rules in the project

Write [assets/agent-contract.template.md](assets/agent-contract.template.md) into the project
as its agent instruction file — `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, or the equivalent —
filled in for this platform. A skill governs a session; the contract file governs the
repository, and it is what keeps the next agent from undoing this one's work.

For how this skill installs in each harness, and how to run it in one with no skill support at
all, see [agents/README.md](agents/README.md).

## Completion report

Lead with operational outcomes, then report:

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
