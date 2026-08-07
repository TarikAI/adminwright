# Build order and sequencing

## Contents

1. Order before scaffolding
2. The walking skeleton
3. Why the spine precedes screens
4. Slice ordering after the skeleton
5. Reading the repository for operational pain
6. Definition of ready and definition of done
7. Parallel and serial work
8. Demo-safe and release-safe milestones
9. Anti-sequences

## Order before scaffolding

Settle the adopt-or-build question first ([buy-vs-build.md](buy-vs-build.md)). A framework changes who writes the code, not the order in which the layers must exist.

Then commit the order to the manifest before writing feature code. The build order is a set of `decisions[]` entries with an explicit sequence, not a preference held in conversation. In concurrent operation it is the only thing preventing two agents from independently choosing a different first slice.

The `architect` role owns the order and revises it when evidence changes. See [multi-agent.md](multi-agent.md).

## The walking skeleton

Build one thin path through every layer before building any second path.

| # | Step | Establishes | Unfixable if skipped |
|---|---|---|---|
| 1 | Identity | Authenticated operator session, persistent environment identity, expiry and sign-out | Every later surface is written against an implicit current user that must be threaded through by hand; no test suite has a session fixture, so every test is rewritten |
| 2 | Role and scope spine | One server-side decision point over subject + resource + action + scope + conditions, plus tenant resolution | Authorization becomes per-route inference with no single place to enumerate or test; the permission matrix cannot be generated or audited |
| 3 | Audit spine | Append-only event write with actor, target, tenant, reason, correlation ID, result; one query surface that reads it back | Each command invents its own event shape; history for everything built before the spine is permanently absent and cannot be backfilled |
| 4 | One read-only list on real data | Server-side pagination, scope predicate from step 2, field policy, and the loading / empty / filtered-empty / forbidden / error states | The read-model shape is discovered after twenty lists already exist in a different shape |
| 5 | One low-risk command end to end | Policy call, validation, transaction boundary, audit emission, truthful result, recovery path, tests | The command contract — error taxonomy, idempotency, confirmation, conflict handling — is invented separately by every slice |
| 6 | Fan out | Everything else | — |

Each step must be visible in the manifest before the next begins. Steps 4 and 5 must each reach `status: implemented` with non-empty `evidence[]`. A spike that is deleted afterward does not count.

The finished skeleton traces end to end:

```text
operator signs in -> role and scope resolved server-side
  -> list query filtered by scope -> real store -> read model
  -> command invoked -> policy allows -> transaction -> audit event written
  -> result returned -> reload shows authoritative state -> audit event readable
  -> forbidden role is denied -> negative test asserts the denial
```

The skeleton is the smallest thing that can fail honestly. If the step 5 command cannot be denied for a role, cannot be read back from the authoritative store after reload, and cannot be found in the audit query, the skeleton is unfinished. Fanning out multiplies the defect by the number of slices.

Choosing the two skeleton capabilities:

- **First list:** the entity the largest number of operator roles touch. Usually accounts, orders, tenants, or the primary work object. It must be tenant-scoped if the platform is multi-tenant, otherwise step 2 goes untested.
- **First command:** reversible, small blast radius, but still requiring a real policy check and a real audit event. Good candidates: assign to queue, add internal note, change a non-billing flag. Bad candidates: refund, delete, impersonate, anything a stakeholder wants to see in a demo.

## Why the spine precedes screens

Retrofitting authorization, tenant scoping, and audit onto twenty built screens is a rewrite. The cost is not proportional to the number of screens alone.

| Layer | Cost when the spine exists first | Cost retrofitted onto N screens |
|---|---|---|
| Query | One scope predicate in the shared query path | Every list, detail, search, and export query, individually |
| Read model | Field policy declared once | Every response shape and every serializer test |
| Cache | Tenant-, role-, and field-aware key once | Every cached read, plus an audit for cross-tenant cache poisoning |
| Command | Policy call in the command base | Every handler, plus proof that no handler bypasses it |
| Audit | Emitted by the command base | Every handler; prior events are unrecoverable |
| UI | Forbidden and filtered-empty states in the shared shell | Every screen's state matrix |
| Tests | Role axis in the test harness | Every test file gains a role dimension |

Three properties make the retrofit qualitatively different from a patch:

1. **Shape change, not addition.** Adding a scope predicate changes the query, the read model, the cache key, the export, the empty-state copy, and the fixtures. Nothing is appended; things are replaced.
2. **Intent is unrecoverable.** On already-shipped code you cannot distinguish a list that is deliberately platform-wide from a list where nobody considered tenancy. Each of the N screens becomes a research task with an interview attached.
3. **Manifest reconciliation.** Capabilities already `implemented` and `reviewed` must be reopened. Reopening discards their evidence and their independent review pass, so the verification cost is paid twice.

Below roughly four screens the difference is invisible. Above ten it decides whether the work is a patch or a rewrite. The same argument applies to anything every screen depends on: tenancy resolution, the design-system component layer, the navigation registry, and the error taxonomy are all spine.

## Slice ordering after the skeleton

Dependency order is a hard constraint and overrides every heuristic:

- an entity's read surface precedes its command surface
- the entity a command targets precedes the command
- a queue precedes its bulk action
- a shared component precedes the second screen that needs it
- a migration precedes anything that reads the column

Within what dependencies allow, sort in this order:

1. **Operational pain** — how much manual work the absence causes today.
2. **Risk** — money movement, irreversibility, data exposure, tenant crossing, legal obligation.
3. **Volume** — records touched per week multiplied by operators doing the touching.

Pain first because it buys the mandate to continue and because a pain-driven slice has a verifiable before-and-after. Risk second because safeguard patterns — confirmation, reason capture, step-up authentication, dual control, idempotency — are cheap to establish while few slices exist and expensive to impose on many. Volume last because a high-volume surface that causes no pain is an optimization, and optimizations built before real usage encode wrong assumptions.

Tie-breaks, in order: the slice that unblocks the most other slices; then the smaller slice.

One override: any capability with `risk: critical` enters the first third of the order regardless of its pain score. Do not queue a money-moving or irreversible command behind ten medium-pain conveniences.

## Reading the repository for operational pain

Pain is evidence, not opinion. [discovery.md](discovery.md) covers what to search for. This section covers how to rank what is found.

| Signal | Where it lives | What it proves |
|---|---|---|
| Ad hoc operational scripts | `scripts/`, `ops/`, `bin/`, `tools/`, `tasks/` | A capability exists but has no console surface, no authorization, and no audit |
| Raw SQL in documents | Runbooks, wikis, incident notes, README fragments | An operator is editing the authoritative store by hand |
| Support macros ending in escalation | Help-desk templates, canned replies | A support role needs a capability it does not have |
| One-off scheduled jobs | Cron entries, scheduler configs, workflow files | Recurring manual work already automated halfway |
| Recurring issue labels | Tracker labels such as `ops-request`, `data-fix`, `manual` | Frequency, and who is paying the cost |
| Code comments | `TODO admin`, "support will do this manually", "run the script" | Known missing surfaces, dated by blame |
| Flags flipped by deploy | Config files changed only to toggle behavior | Configuration that needs an operator surface |
| Direct writes in incident notes | Postmortems, chat exports checked into the repo | The riskiest manual path, usually unaudited |

Probe the history rather than the current tree; an old script that still runs is worse than a new one. Adapt these to the version control and shell available:

```text
git log --since="12 months ago" --name-only -- scripts/ ops/ bin/ tools/
git log --format="%an" -- <script-path> | sort | uniq -c | sort -rn
grep -rniE "update .* set|delete from|insert into" docs/ runbooks/ *.md
grep -rniE "manually|by hand|ask engineering|run the script|prod console" docs/
```

Score each candidate on frequency (executions per month), spread (distinct people who ran it), recency (still used this quarter), blast radius (does a mistake cost money or data), and current safety (a script run from a laptop has no authorization and no audit at all).

A script executed monthly by four people against production with no audit outranks a screen a stakeholder asked for.

Record each finding as a `gaps[]` entry with its evidence path before it becomes a slice. The gap is the justification the ordering rests on, and it is what proves the slice worked once the manual path is deleted.

## Definition of ready and definition of done

A slice is **ready** when every line below is resolved in the manifest, not during implementation:

- the capability exists with `id`, `outcome`, `kind`, `risk`, `roles`, and `entityStates`
- `dataBinding` names the real store and access path, for example `postgres:orders via OrderRepository.findForAdmin`
- the authoritative source is confirmed rather than assumed, and conflicts between sources are resolved
- the authorization shape is decided — role, scope, object, or condition — and the spine can express it without change
- the audit requirement is decided: mutation, privileged read, both, or none with a recorded rationale
- safeguards proportional to `risk` are named in `safeguards[]`
- the design-system components the slice needs already exist, or building them is inside this slice's scope
- `tests[]` names the paths that will exist
- the agent holds a claim on the capability

A slice is **done** when the ten steps of Phase 5 in [../SKILL.md](../SKILL.md) are complete, and in addition:

- `status: implemented`, with `evidence[]` paths that exist and are non-empty
- `reviewStatus: reviewed`, set by an agent or a declared separate pass that did not implement it
- validation passes at the project's profile:

```text
python <skill-dir>/scripts/admin_console_manifest.py validate --manifest <project-root>/.admin-console/manifest.json --project-root <project-root> --phase plan
```

Use `py -3` or `python3` if `python` is not on PATH.

- no new `critical` or `high` gap is left `open`
- the manual procedure the slice replaces is deleted, or the runbook step is marked superseded with a pointer to the new capability

Update the manifest at the end of each slice, never in a batch at the end of the day. A slice that is done but unrecorded is indistinguishable from one never started, and under concurrency it will be started again by someone else.

## Parallel and serial work

Coordination runs through the manifest and lock files. See [multi-agent.md](multi-agent.md). Claim before touching:

```text
python <skill-dir>/scripts/admin_console_manifest.py claim --manifest <project-root>/.admin-console/manifest.json --agent <agent-id> --role implementer --capability <capability-id>
```

Serialize — one author, merged before dependents start:

| Work | Why it cannot be concurrent |
|---|---|
| Authentication, session, environment identity | Every slice reads it; two shapes cannot coexist |
| Authorization policy engine and scope resolution | A second decision point is a second security boundary |
| Audit event schema and writer | Divergent event shapes make the audit unqueryable |
| Shared schema migrations | Conflicting migration order corrupts the sequence |
| Design-system component layer | Two authors produce two table components and two form idioms |
| Navigation and route conventions | Route collisions and inconsistent deep links |
| Error codes and result shapes | Clients cannot handle an unstable taxonomy |
| `crossCutting` manifest section | Concurrent writes to one object lose edits |

Safe concurrently:

- vertical slices over disjoint entities with disjoint tables
- screens whose components already exist
- integrations with distinct providers
- test and evidence authoring for capabilities already implemented
- the `qa` and `security` passes over completed capabilities
- emitted artifacts such as the authorization matrix, test plan, and navigation map

Three rules keep concurrency honest. Only one agent authors migrations at a time. If two slices need the same new component, extract it as a serialized item before either proceeds. If a slice discovers it needs a spine change, it releases its claim and the spine change is scheduled as its own serialized item; it never forks the spine locally.

The agent that implements a capability may not be the agent that reviews it. Schedule the review pass as separate work inside the order, not as an afterthought.

## Demo-safe and release-safe milestones

| Property | Demo-safe | Release-safe |
|---|---|---|
| Data | Real store, seeded non-production records | Real store, production-like volume |
| Roles exercised | One intended role | Every declared role, including forbidden paths |
| Tenancy | Single tenant | Isolation proven with negative tests |
| Command paths | Happy path | Validation, conflict, forbidden, timeout, unknown result, recovery |
| Audit | Event written | Event written, queried back, and asserted in a test |
| Screen states | Populated and loading | All applicable states from Phase 5 step 8 |
| Concurrency and idempotency | Not exercised | Asserted for every mutating capability |
| Accessibility and performance | Not measured | Gates passed or `not-applicable` with rationale |
| Evidence | Optional | Paths exist, are non-empty, and reference the capability |
| `decisions[]` | May be `assumed` | `confirmed` at the levels the profile requires |
| Validator phase | `--phase plan` | `--phase release` |

What changes between them is not polish. It is the negative half of the system: denials, failures, conflicts, isolation, and the evidence that each was exercised.

- A demo-safe milestone is never described as complete, done, shipped, or ready.
- Demo-safe runs in a non-production environment with unmistakable environment identity.
- If a demo-safe build is exposed to real operators, it must sit behind a flag limited to a named pilot group, and that exposure is itself an open `gaps[]` entry until the milestone reaches release-safe.
- Release-safe is decided by the gates in [verification.md](verification.md) and the validator, not by the agent's confidence.

## Anti-sequences

| Anti-sequence | What it looks like | What it costs | Correction |
|---|---|---|---|
| UI-first | Build screens against fixtures, wire data later | Every screen's state matrix, loading model, and error handling was designed for data that always arrives; the rewiring pass touches every component and the fixtures leak into the release path | Skeleton step 4 before any second screen |
| Table-per-model CRUD sweep | One list and one edit form per database table | Produces a database editor, not a control plane; lifecycle transitions degrade into a status dropdown with no preconditions or side effects; storage columns become editable that never should be | Derive capabilities from operator outcomes; see [capability-catalog.md](capability-catalog.md) |
| Dashboard-first | Start with charts and KPI tiles | Metrics are built before the actions they should lead to exist, so no tile has a drill-down or an owner; the landing page becomes decoration and gets rebuilt once queues exist | Build queues and lists first; add the overview once its links have destinations |
| Auth-last | Ship screens, add roles before release | The rewrite described above, plus an unanswerable question per screen about whether its breadth was intentional | Skeleton steps 1 and 2 |
| Tests-last | Feature work now, test suite as a milestone | Tests written after the fact are written from the implementation and inherit its blind spots; negative and permission tests are the ones that get cut when time runs out | Tests are inside the slice, not after it; see [verification.md](verification.md) |
| Seed-data-last | Develop against a handful of hand-made rows | Pagination, long values, missing values, every lifecycle state, failed jobs, and stale versions are all discovered in review; performance budgets were never measurable | Seed representative data with the skeleton; see [test-data.md](test-data.md) |

The common failure in all six is the same: they defer the parts of the system that constrain the other parts. Order the work so that every constraint is discovered while exactly one thing depends on it.
