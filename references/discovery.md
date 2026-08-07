# Platform discovery and control-plane modeling

## Contents

1. Dashboard versus console
2. Minimum inputs
3. Evidence hierarchy
4. Repository archaeology
5. Stack and resource discovery
6. The six discovery maps
7. Requirement inference
8. Autonomy boundaries
9. Required discovery output

## Dashboard versus console

Two different surfaces. Keep the words distinct for the whole build.

| | Dashboard | Console |
|---|---|---|
| Answers | what needs attention now | do the work on this record, queue, or batch |
| Content | thresholds, breaches, aging, backlog, health | lists, queues, detail views, forms, commands |
| Every element | routes into a filtered console view | changes state or produces an authoritative answer |
| Owner of value | routing accuracy | task completion time and safety |
| Failure mode | a wall of numbers nobody acts on | a table of every database column |

Most platforms need both. The landing page is an attention router, not the product. Operator value is created in the console; the dashboard only decides where the operator goes first.

Consequences for discovery: a number earns dashboard placement only when it has an owner, a threshold, and a console destination (map 6). A console screen earns existence only when a role performs a recurring task there (map 1).

## Minimum inputs

A complete admin specification needs these ten inputs. Most are recoverable from the repository. Never block on an input you can infer.

| Input | Source | How |
|---|---|---|
| Product description and purpose | infer | README, docs, public routes, package metadata, marketing copy in the repo |
| Admin roles and who fills them | infer, confirm | role enums, policy files, middleware, seed data, ops scripts; responsibilities usually need confirmation |
| Main entities | infer | schemas, migrations, models, aggregate roots |
| Entity lifecycles | infer | status enums, state-machine definitions, transition methods, guards, tests |
| Important workflows and their failures | infer | jobs, retries, dead letters, error branches, support runbooks, fixtures |
| Payments and sensitive data | infer, confirm | provider SDKs, field names, encryption/masking code, consent tables |
| External integrations | infer | clients, webhook handlers, environment variables, infra config |
| Expected data and user volume | ask | current row counts if reachable; expected peak and growth are a human answer |
| Compliance requirements | ask | the repo hints at regulated data; legal obligation is a human answer |
| Countries, languages, currencies, timezones | infer, confirm | locale files, currency enums, timezone handling; target markets not yet built need asking |

Record every inferred answer as `decisions[]` with `status: assumed`. Record every genuinely human input you did not get as `gaps[]` with a severity and a working default. Continue building around an open gap; do not stall the whole build on one unanswered question.

At the `regulated` profile an `assumed` decision is a release error. Convert assumptions to `confirmed` before claiming release, or the profile gate will stop you.

## Evidence hierarchy

Prefer evidence in this order, and resolve conflicts explicitly:

1. Enforced production invariants and authoritative schemas
2. Server-side policies, commands, state machines, and tests
3. Current operator procedures and incident/support runbooks
4. Product and compliance documentation
5. Customer-facing behavior and integrations
6. Existing admin UI
7. Stakeholder assumptions

An existing admin page may be incomplete or obsolete. A database table may expose storage details that should never become editable. Infer requirements from operator outcomes, not surface resemblance. When two sources disagree, record which one you followed and why as a `decisions[]` entry; do not silently pick one.

## Repository archaeology

Search for:

- Models, schemas, migrations, relationships, enums, soft-delete fields, timestamps, ownership, tenant IDs, and version fields
- Route handlers, controllers, resolvers, service methods, command handlers, policy objects, and permission checks
- State transitions such as approve, suspend, publish, refund, retry, reconcile, rotate, restore, or revoke
- Events, consumers, background jobs, scheduled tasks, dead-letter queues, webhooks, and integration adapters
- Public product routes and user journeys that generate records, payments, content, alerts, or support obligations
- Feature flags, limits, plan entitlements, quotas, experiments, configuration, and secrets boundaries
- Audit emitters, logs, metrics, tracing, alerts, incident runbooks, and reconciliation processes
- Tests and fixtures that reveal intended business rules and edge cases
- Scripts or manual SQL used by support and operations; these are the strongest signal of a missing control surface

Build a source map for each administrative domain:

```text
Domain -> entities -> source of truth -> commands -> policies -> events/jobs -> existing UI -> tests -> owner
```

## Stack and resource discovery

Required before any implementation modeling. Guessing a framework's auth, policy, migration, or job idioms is a defect.

Step 1 — identify the actual stack from artifacts, not from assumption:

```text
frontend        dependency manifest, template engine, build/bundler config, router
backend         language and framework manifest, entrypoint, route registration
database        migration tool, ORM or query builder, connection config
auth            session/token library, identity provider config, auth middleware
jobs            queue or worker library, scheduler config, cron definitions
hosting         CI config, container or serverless manifests, infrastructure as code
designSystem    component library dependency, token file, theme config
adminFramework  existing admin package, or "none"
```

Write the result to `platform.stack`. Pin versions from the lockfile; idioms change between major versions.

Step 2 — read the adapter for that stack in [stack-adapters.md](stack-adapters.md). It states the idiomatic place for policies, commands, migrations, jobs, and tests in that ecosystem. When no adapter matches, use the closest one, record the divergence as a decision, and raise a `feedback[]` entry so the gap can be promoted later.

Step 3 — consult the authoritative documentation for each stack element you will implement against. Entry points are curated in [resource-index.md](resource-index.md). Authoritative means the maintainer's own documentation for the version in the lockfile, not a blog post and not recall.

Step 4 — record every source consulted in `platform.researchSources[]`:

```text
{ "topic": "row-level authorization",
  "url":   "<canonical docs URL you actually opened>",
  "appliedTo": ["capability:payout.approve", "crossCutting.authorization"],
  "checkedOn": "YYYY-MM-DD" }
```

Step 5 — capture `platform.volumes` (`entityCounts`, `peakConcurrentOperators`, `retentionHorizon`). Volume decides server-side versus client-side filtering, pagination style, export limits, and job-versus-synchronous execution. See [architecture.md](architecture.md) for the budgets these numbers set.

## The six discovery maps

Discovery is done when six maps exist, recorded in the manifest rather than in conversation. Work them in order; each feeds the next.

| Map | Populates |
|---|---|
| 1 Administrator map | `roles[]`, `screens[].roles`, `crossCutting.authentication` |
| 2 Entity map | `entities[]`, `platform.regulatedData`, `platform.sourceSystems`, `crossCutting.data` |
| 3 Lifecycle map | `entities[].lifecycleStates`, `entities[].lifecycleTransitions`, `capability.entityStates` |
| 4 Permission matrix | `roles[].scopes`, `capability.roles`, `capability.authorizationPolicies`, `crossCutting.authorization` |
| 5 Workflow and exception map | `workQueues[]`, `capability.kind`, `capability.recovery`, `integrations[].failureHandling`, `gaps[]` |
| 6 Decision-to-metric map | dashboard entries in `screens[]`, `platform.operationalObjectives`, query capabilities |

### 1. Administrator map

One row per role, not per person. Candidate operators: platform owner, tenant administrator, operations specialist, support agent, finance/reconciliation operator, trust-and-safety moderator, compliance or privacy reviewer, account manager, content manager, technical support or SRE, delegated partner administrator, read-only analyst.

```text
role: finance-operator
responsibilities: settlement review, refunds, dispute evidence
decisions owned: refund approve or deny below the role limit
frequent tasks: settlement mismatch queue (~40/day), refund queue (~15/day), month-end close
most dangerous: issue refund, adjust ledger entry, release payout hold
may touch: tenants=all; regions=EU,UK; amount<=5000 USD; data classes<=confidential
auth strength: SSO + MFA; step-up above 1000 USD
separated from: payout-approver (may not approve a payout it requested)
```

A declared role that no screen and no capability uses is a modeling error, and a profile gate at `standard` and above. Delete it or find its work.

### 2. Entity map

For every managed entity:

```text
entity: payout
operator owner: finance-operator
source of truth: postgres:payouts (ledger authoritative for amounts)
identifier: pay_<ulid>          tenant boundary: organization_id
fields: amount (immutable) | status (command-only) | provider_ref (derived)
        internal_note (editable by finance-operator)
sensitive: bank_last4 restricted; tax_id restricted, masked by default
relationships: order 1..n, ledger_entry 1..n, dispute 0..n   aggregate root: payout
retention: 7 years; legal hold blocks deletion
volume: ~2.1M rows, +40k/month
upstream: banking provider webhook   downstream: reporting warehouse
```

The field list is where "must never become editable" is decided. A storage column is not automatically an operator field. Field-level shapes and the audit, note, and attachment tables every console needs are in [admin-data-model.md](admin-data-model.md).

### 3. Lifecycle map

States, allowed transitions, actor, conditions, side effects, notifications. Include the states teams forget: failed, stale, duplicate, expired, cancelled, disputed, archived.

```text
payout lifecycle

  draft            --submit--------------> pending_review
  draft            --discard-------------> discarded
  pending_review   --approve-------------> queued
  pending_review   --reject--------------> rejected
  queued           --cancel--------------> cancelled
  queued           --dispatch------------> sent
  sent             --provider:success----> paid          (terminal)
  sent             --provider:failure----> failed
  sent             --provider:unknown----> reconciling
  failed           --retry---------------> queued        (max 3, then escalated)
  reconciling      --resolve-------------> paid | failed
```

Each arrow is a business command with rules and effects, never a status dropdown. Record the detail per transition:

```text
transition: queued -> sent          command: DispatchPayout
actor roles: payout-approver (never the requester)
conditions: approved within 24h, provider healthy, amount <= role limit
safeguards: idempotency key = payout_id + attempt; step-up auth above 1000 USD
side effects: provider API call, ledger entry, recipient notification
unknown result: move to reconciling; never retry blindly
notifications: recipient on paid; finance channel on failed and on escalation
audit: actor, payout, amount, reason, provider reference, result
```

Two rules the validator enforces at `standard` and above: every lifecycle state must be observable through a query capability, and every non-initial state must be reachable through a command capability. A state you can enter but cannot see is an operational blind spot.

### 4. Permission matrix

Model permissions as `subject + resource + action + scope + conditions + obligations`.

| Role | Resource | Action | Scope | Conditions | Obligations |
|---|---|---|---|---|---|
| support-agent | user | read | tenants in assigned region | — | mask tax_id |
| support-agent | user | suspend | tenants in assigned region | target is not a staff account | reason capture, audit |
| finance-operator | payout | approve | amount <= 5000 USD, regions EU/UK | state = pending_review, requester != actor | step-up above 1000, dual control |
| finance-operator | payout | export | same as read scope | — | row cap, logged as privileged read |
| analyst | payout | read | own tenant only | — | no bank fields |

Default deny. A cell you did not write is denied.

**Scope is the column implementations get wrong.** Role and action are usually correct; scope is usually enforced in one place and missed in five. Check every one:

- List and detail queries filtered server-side, not in the client
- Search index and autocomplete filtered by the same scope
- Exports and reports obeying the same row filter and field masks as the detail view
- Bulk selection re-checked at execution time, not only at selection time
- Object-level check on direct identifier access, tested with guessed and enumerated IDs
- Cross-tenant work requiring an explicit platform-wide scope, never an implicit one
- Scope re-evaluated when a queued, scheduled, or previously approved operation finally runs

Enforcement placement is in [architecture.md](architecture.md); policy testing and access review are in [security-governance.md](security-governance.md).

### 5. Workflow and exception map

Admin systems are queue-and-exception systems wearing CRUD clothing. Map the normal path once, then spend most of the effort on everything that fails, stalls, duplicates, expires, conflicts, or gets abused.

```text
workflow: seller onboarding
normal: signup -> document upload -> automated KYC -> manual review -> approved
actors: seller (self-serve), risk-reviewer (manual review)
volume and SLA: ~200/day, decision within 24h

exceptions
  fails       KYC provider error                -> queue kyc-error; retry x3 then manual
  stalls      documents uploaded, unreviewed 48h-> queue onboarding-aging; escalate at 72h
  duplicates  same tax id on two accounts       -> queue duplicate-identity; merge or reject
  expires     documents older than 90 days      -> re-request; approval blocked
  conflicts   two reviewers open the same case  -> claim lock; version check on decision
  abused      resubmission loop after rejection -> rate limit; prior decision visible; appeal path
```

Run all six classes against every workflow:

| Class | Ask | Produces |
|---|---|---|
| fails | which errors, from where, is retry safe | retry and dead-letter queue, `capability.recovery` |
| stalls | what has no owner or no deadline | aging queue, SLA, escalation path |
| duplicates | what can be created or executed twice | idempotency key, merge or dedupe capability |
| expires | what goes stale, invalid, or out of window | expiry job, re-request path, blocked transitions |
| conflicts | who else edits the same record at the same time | version or lock, conflict state, safe re-entry |
| abused | how a hostile operator or user exploits this | rate limit, approval, audit, separation of duties |

For each discovered queue record `workQueues[]`: what arrives, how priority and SLA are computed, who owns and can reassign, what blocks completion, what is safe in bulk, what escalates, how aging and breach are visible, and what happens when a downstream system is unavailable.

Common queues: pending approvals, failed payments, disputed orders, reported content, onboarding reviews, stuck jobs, webhook failures, delivery exceptions, security alerts, data-subject requests, reconciliation mismatches.

### 6. Decision-to-metric map

Every metric on an operational dashboard must answer seven questions. If it supports no decision, it is not on the operational dashboard — it belongs to reporting.

```text
metric: open settlement mismatches
question:   is money unaccounted for right now?
owner:      finance-operator
threshold:  >0 attention; >25 open or any item >24h old = breach
action:     work the payout-mismatch queue; escalate to finance lead on breach
source:     postgres:reconciliation_mismatch via ReconciliationRepository.openForAdmin
freshness:  5 minutes; staleness shown; degraded state above 15 minutes
drill-down: the payout-mismatch queue filtered to status=open
```

Rules:

- No metric without an owner and a numeric threshold.
- No metric without a drill-down to the actual records behind the number.
- Every dashboard metric becomes a query capability with a real `dataBinding`. Sample series and static numbers fail the placeholder scan at every profile.
- Trend charts only when the shape of the trend changes an operator decision.

Layout and density rules are in [experience-design.md](experience-design.md). The KPI-wall anti-pattern is in [capability-catalog.md](capability-catalog.md).

## Requirement inference

For each customer-facing capability, derive the administrative obligation.

| Customer capability | Likely administrative obligation |
|---|---|
| Account creation | Search, verification state, suspension, merge/duplicate handling, access history |
| Payments | Reconciliation, refunds, disputes, provider references, idempotency, audit |
| User-generated content | Moderation queue, reports, evidence, actions, appeals, policy history |
| Subscription plans | Entitlements, overrides, proration visibility, billing events, plan migration |
| Notifications | Delivery status, template/version, suppression, retry, consent evidence |
| File upload | Scan status, access policy, retention, quarantine, deletion, lineage |
| External integration | Connection health, credentials boundary, sync state, replay, reconciliation |
| AI output | Model/version trace, prompt/config lineage, evaluation, override, incident controls |

Infer a capability when it is necessary to operate, support, secure, reconcile, or govern the product. Do not infer arbitrary business policy. Before designing a large obligation from scratch, check [buy-vs-build.md](buy-vs-build.md) — some obligations are better met by an existing authoritative system than by a new console surface.

## Autonomy boundaries

Decide and document. Do not ask:

- Entity, capability, screen, and queue naming; information architecture and route structure
- Which archetype patterns from the catalog apply
- Table columns, filters, default sorts, saved views, and detail layout
- Loading, empty, error, forbidden, conflict, stale, and success state design
- Test structure, evidence file layout, and fixture strategy
- Library choice within the detected stack's own conventions
- Defaults for pagination size, timezone display, and history depth
- Administrative obligations inferred from customer-facing features

Ask the human. These change the product materially and cannot be recovered from code:

- Who is legally or contractually permitted to view or change this data
- Which system is authoritative when records disagree
- Monetary or operational thresholds that require approval
- Retention, deletion, and legal-hold policy
- Whether support may impersonate users, and under what controls
- Which failures require human intervention versus automated retry
- What must remain intentionally impossible from the console
- Anything requiring production credentials, provider accounts, or live-environment access
- Destructive migrations against real data
- Accepting a risk that a profile gate would otherwise fail

How to ask: batch the questions once, propose a default for each, state the consequence of the default, and keep building. Answers land as `decisions[]` moving from `assumed` to `confirmed`. Unanswered questions stay as `gaps[]`.

Role ownership of these boundaries is in [multi-agent.md](multi-agent.md). The `architect` owns discovery and every modeling decision here. Anything that changes the authorization surface or data exposure is escalated to `security` rather than decided by the architect alone.

## Required discovery output

Discovery produces manifest state, not a chat summary. Populate:

```text
platform.name, summary, archetypes, tenancy
platform.regulatedData, sourceSystems, operationalObjectives
platform.stack.{frontend,backend,database,auth,jobs,hosting,designSystem,adminFramework}
platform.researchSources[]      {topic, url, appliedTo, checkedOn}
platform.volumes.{entityCounts, peakConcurrentOperators, retentionHorizon}
roles[]                         {id,name,responsibilities,scopes,mfaRequired,
                                 authenticationStrength,separationOfDuties}
entities[]                      {id,name,sourceOfTruth,sensitivity,tenantScoped,
                                 lifecycleStates,retention}
entities[].lifecycleTransitions[] {from,to,command,actorRoles}
entities[].capabilities[]       {id,outcome,kind,roles,risk,rationale,entityStates,
                                 status:"discovered"}
workQueues[]                    {id,purpose,roles,source,priorityRule,sla,actions}
integrations[]                  {id,direction,sourceOfTruth,credentialBoundary,operations,
                                 failureHandling,reconciliation,monitoring}
screens[]                       dashboard and console routes, each with roles and purpose
decisions[]                     every inferred answer, status assumed|confirmed
gaps[]                          every blocking unknown, with severity and working default
```

Leave implementation fields empty at this phase: `dataBinding`, `serverOperations`, `authorizationPolicies`, `auditEvents`, `safeguards`, `tests`, `evidence`. Do not fill them with `TBD`, `mock`, or `coming soon` — the placeholder scan errors on those at every profile. Empty is honest; a placeholder is a defect.

Discovery is complete when all of the following hold:

- All six maps are recorded in the manifest.
- Every declared role is used by at least one screen and at least one capability.
- Every lifecycle state is observable by a query capability; every non-initial state is reachable by a command capability.
- Every queue has a source, a priority rule, an SLA, and at least one action.
- Every dashboard metric has an owner, a threshold, and a drill-down target.
- `platform.stack` is filled and `platform.researchSources[]` has an entry for every stack element you will implement against.
- Every unresolved question is a `gaps[]` entry, not silence.

Record entries as you go and verify before handing off to build sequencing in [build-order.md](build-order.md):

```text
python <skill-dir>/scripts/admin_console_manifest.py add --manifest <path> --kind decision --json '<obj>'
python <skill-dir>/scripts/admin_console_manifest.py add --manifest <path> --kind gap --json '<obj>'
python <skill-dir>/scripts/admin_console_manifest.py validate --manifest <path> --phase plan
python <skill-dir>/scripts/admin_console_manifest.py coverage --manifest <path> --project-root <root>
```

Use `py -3` or `python3` if `python` is not on PATH.
