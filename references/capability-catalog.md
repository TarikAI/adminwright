# Capability catalog and platform archetypes

## Contents

1. How to use the catalog
2. Common feature families
3. Project-type module map
4. Platform archetypes
5. Safety features teams commonly forget
6. Capability selection test
7. The completeness test
8. Anti-patterns

## How to use the catalog

This catalog is a hypothesis generator, not a checklist to copy wholesale. Every entry is a question to answer with repository evidence, never a page to generate because the catalog mentions it.

Order of use:

1. Common feature families. What nearly every console needs.
2. Project-type module map. Fast lookup by product category.
3. Archetype sections. The domains, risks, and exception paths behind each row.
4. Safety features. The safeguards teams skip.
5. Selection test. Include, defer, or exclude.
6. Completeness test. The question that decides whether the console is finished.

Confirm or reject each candidate against the evidence hierarchy in [discovery.md](discovery.md). Record every candidate in the manifest with a `status` of `discovered`, `planned`, `implemented`, `blocked`, `deferred`, or `not-applicable`, plus a `rationale`. A rejected candidate with a written reason is a result. An unexamined candidate is a gap.

Capabilities carry `kind`, `risk`, `entityStates`, `serverOperations`, `authorizationPolicies`, `auditEvents`, `safeguards`, `dataBinding`, `idempotency`, `concurrency`, `recovery`, `tests`, and `evidence`. If a catalog entry cannot be expressed with those fields filled from real code, it is not yet a capability.

## Common feature families

Run this list against every platform, whatever its domain. Each family that survives the selection test becomes one or more capabilities bound to real server operations.

| Family | Not done until |
|---|---|
| Authentication and session | Privileged roles carry MFA or a stronger factor, sessions expire and can be revoked, recovery is as strong as sign-in, and active sessions are visible |
| Roles, permissions, scoped access | Every action is authorized server-side against subject, resource, action, scope, and conditions; scope covers tenant, region, environment, and data class |
| User and member management | Operators can invite, create, edit, deactivate, and delete users through real server operations; assign and revoke roles; reset credentials and MFA; revoke sessions; and see each user's status and history. A read-only user list is not user management |
| Global search | Operators can find a record by the identifier they actually hold (email, order number, external reference), with policy applied to results |
| Lists, filters, sorting, pagination | Dense tables over real queries with server-side filtering, sorting, stable pagination or virtualization, and column choices matched to the operator's decision |
| Detail views | Current state, key facts, related records, history, notes, and the permitted actions on one screen |
| Forms and validation | Server validation is authoritative, client validation mirrors it, field-level policy applies, and partial saves cannot corrupt state |
| Saved views | Operators can persist and share filter sets; shared views respect the viewer's permissions, not the author's |
| Statuses and lifecycle actions | Each transition is a named command with preconditions, actor roles, side effects, and audit; not an editable status field |
| Bulk operations | Preview and count before execution, per-target authorization, progress, partial-failure reporting, downloadable results, and a size limit |
| Import | Dry-run validation, row-level error reporting, idempotent re-run, and a record of who imported what |
| Export | Row and field policy identical to detail views, size limits, async generation for large sets, expiry, and download audit |
| Notifications | Actionable, routed to a role or owner, deep-linked to the record, and dismissible without losing the work item |
| Internal notes and assignment | Notes attributed and timestamped, ownership and reassignment explicit, visibility rules stated |
| Audit history | Actor, effective identity, action, target, reason where required, before and after values, correlation IDs, and result |
| Full state set | Loading, empty, partial, error, forbidden, conflict, stale, degraded, and success rendered for every surface |
| Destructive actions | Impact preview, authority and legal-hold checks, recovery or export path, and separate audit for request and completion |
| Responsive and accessible interaction | Keyboard operation, focus management, screen-reader semantics, contrast, and usable layout on the devices operators actually use |
| Localization, timezone, currency | Explicit timezone on every timestamp, currency with its unit and precision, locale-correct formatting, and no hardcoded strings in operator-facing copy |
| Configuration and feature exposure | Settings write to the store the runtime reads, changes are audited, and effect is observable |
| Operational health | Job status, webhook delivery, integration health, reconciliation state, and retry or replay controls |

Not every role sees every family. Scope each to roles before designing screens.

Applying this list mechanically produces a console that looks complete and controls nothing. Every row must be justified by a real entity, a real server operation, and a real operator job. Where a family exists but the platform is better served by an existing system, record the decision per [buy-vs-build.md](buy-vs-build.md).

## Project-type module map

Fast lookup. The archetype sections below carry the risks and exception paths.

| Project type | Specialized modules beyond the common families | Archetype section |
|---|---|---|
| E-commerce and retail | Catalog, variants, pricing, inventory, promotions, orders, fulfillment, returns, refunds, disputes, fraud | Commerce and retail |
| B2B SaaS | Organizations, memberships, SSO/SCIM, plans, subscriptions, usage, entitlements, invoices, feature flags, API keys | B2B SaaS and multi-tenant platforms |
| Marketplace | Two-sided onboarding, verification, listings, matching, bookings, commissions, payouts, disputes, trust and moderation | Marketplace and gig platforms |
| Content platform | Editorial lifecycle, scheduling, versioning, taxonomy, localization, rights, moderation queues, appeals, creator monetization | Content, media, and community |
| Logistics and mobility | Dispatch, routes, assets, operators, capacity, tracking, proof of delivery, exceptions, service areas, partner reconciliation | Logistics, mobility, and field operations |
| Education | Institutions, terms, courses, cohorts, enrollment, grading, attendance, accommodations, integrity cases, guardians | Education and learning |
| Healthcare | Patient identity, consent, eligibility, scheduling, documents, access disclosure, sensitive-record segmentation, legal holds, break-glass | Healthcare and regulated care |
| Fintech | KYC/KYB, accounts, limits, ledger, transactions, reversals, disputes, holds, maker-checker approval, sanctions and AML cases | Financial services and fintech |
| Infrastructure and developer platform | Projects, environments, resources, deployments, credentials, quotas, metering, pipelines, logs, alerts, rollback | Infrastructure, IoT, developer, and data platforms |
| AI platform | Models, versions, prompts and configuration, datasets, evaluations, cost and latency, safety events, human review, output traceability | AI-enabled platforms |
| LLM gateway / AI API management | Provider registry, credential vault, model catalog and aliases, routing and fallback, platform-issued API keys, usage and cost metering, rate limits, external API connections | LLM gateways and API management platforms |
| Agent-operated platform | Agent registry, granted scopes, per-agent audit identity, approval queues, spend and rate caps, kill switches, replay and rollback | Agent-operated and AI-acting platforms |

Products combine types. A logistics platform sold to enterprises needs the SaaS row and the logistics row. Record every match in `platform.archetypes[]` and derive capabilities from all of them.

## Platform archetypes

### B2B SaaS and multi-tenant platforms

Expected domains:

- Organizations, workspaces, memberships, invitations, domains, SSO, SCIM, and tenant status
- Plans, subscriptions, usage, quotas, entitlements, trials, credits, invoices, and overrides
- Feature flags, rollout cohorts, configuration, API keys, service accounts, and webhooks
- Tenant health, onboarding progress, support access, data export, deletion, and migration
- Account ownership, contracts, success notes, and incident communication when relevant

Critical risks: tenant isolation, entitlement drift, support impersonation, billing inconsistency, and over-broad exports.

### Commerce and retail

Expected domains:

- Catalog, variants, pricing, inventory, locations, promotions, tax, and availability
- Carts, orders, payments, fulfillment, shipment, returns, refunds, and cancellations
- Customers, addresses, fraud signals, chargebacks, credits, and communication
- Inventory adjustments, reservation mismatches, fulfillment exceptions, and reconciliation

Critical risks: duplicate money movement, overselling, unauthorized discounts/refunds, tax errors, and provider inconsistency.

### Marketplace and gig platforms

Expected domains:

- Buyer and seller/provider onboarding, verification, eligibility, and status
- Listings/services, quality review, availability, matching, bookings/orders, and completion evidence
- Commissions, payouts, holds, reserves, disputes, refunds, and fraud review
- Reports, moderation, appeals, trust scores, policy decisions, and geographic controls

Critical risks: identity/KYC exposure, payout fraud, unfair enforcement, collusion, and dispute evidence integrity.

### Financial services and fintech

Expected domains:

- Customer and business onboarding, KYC/KYB, accounts, beneficiaries, limits, and risk cases
- Transactions, authorizations, settlements, reversals, disputes, holds, and reconciliation
- Ledger visibility, immutable history, provider messages, exception queues, and maker-checker approval
- Sanctions/AML cases, evidence, regulatory reports, and restricted data access

Critical risks: ledger mutation, money movement, segregation of duties, regulatory evidence, and irreversible actions. Prefer commands and compensating entries over edits.

### Content, media, and community

Expected domains:

- Content lifecycle, drafts, review, publishing, scheduling, versioning, and rollback
- Taxonomy, localization, media rights, accessibility metadata, and distribution
- Reports, moderation queues, policy reasons, strikes, restrictions, appeals, and evidence
- Creator accounts, monetization, copyright claims, and safety escalations where relevant

Critical risks: policy inconsistency, lost evidence, accidental publication, censorship abuse, and rights violations.

### Logistics, mobility, and field operations

Expected domains:

- Orders/jobs, dispatch, routes, vehicles/assets, operators, locations, and capacity
- Tracking, ETA, proof of pickup/delivery, incidents, exceptions, and reassignment
- Service areas, pricing, availability, compliance documents, maintenance, and SLA
- Failed delivery, damage, loss, cancellation, compensation, and partner reconciliation

Critical risks: real-world safety, stale location data, double assignment, fraudulent evidence, and operational interruption.

### Healthcare and regulated care

Expected domains:

- Patient/member identity, consent, eligibility, scheduling, providers, cases, and documents
- Access disclosures, sensitive-record segmentation, corrections, retention, and legal holds
- Clinical or care workflows only where the product is authorized to manage them
- Audit review, break-glass access, incident investigation, and data-subject requests

Critical risks: sensitive data exposure, unsafe clinical modification, consent violations, and incomplete access audit. Never infer clinical authority.

### Education and learning

Expected domains:

- Institutions, terms, courses, cohorts, enrollment, instructors, learners, and guardians
- Content, assignments, assessments, grading, attendance, certificates, and accommodations
- Progress, integrity cases, appeals, communication, and data retention

Critical risks: minors' data, grade integrity, role boundaries, accessibility, and academic records.

### Infrastructure, IoT, developer, and data platforms

Expected domains:

- Projects, environments, resources, devices, deployments, versions, regions, and ownership
- Credentials, keys, tokens, policies, quotas, metering, billing, and service health
- Jobs, pipelines, logs, traces, alerts, incidents, maintenance, replay, and rollback
- Schema/configuration history, dependency impact, data lineage, and destructive-operation plans

Critical risks: credential exposure, production impact, cross-environment confusion, destructive commands, and stale telemetry.

### AI-enabled platforms

Expected domains:

- Models, providers, versions, prompts/configuration, evaluations, datasets, and deployments
- Usage, latency, cost, quality, safety events, feedback, and human review queues
- Traceability from output to inputs/configuration, access controls, redaction, and retention
- Rollback, provider outage handling, rate/limit controls, and incident investigation

Critical risks: sensitive prompt/output exposure, untraceable decisions, unsafe automation, runaway cost, and silent model/config drift.

### LLM gateways and API management platforms

For platforms whose product is managing model providers and external APIs — an LLM router,
an AI gateway, an inference proxy, an integration hub, or any product where "connect your
API" is the core promise. Field-tested: these platforms kept receiving consoles that showed
provider names with no way to add a key, no base URL field, no edit or delete, and no user
management. The controls below are the product; a console missing them controls nothing.

Expected domains:

- Provider registry, model-agnostic by construction: add, edit, disable, and delete any
  provider — the built-in ones and a custom provider defined by base URL, authentication
  scheme (bearer, header key, basic, OAuth2 client credentials), custom headers, API
  version, and timeout. "Model-agnostic" means the operator can register a provider the
  developers never heard of without a code change; a hard-coded provider list is a gap.
- Credential vault: create, rotate, revoke, and delete API keys and secrets per provider and
  per environment. Secrets are write-only after save — encrypted at rest, displayed masked
  with a last-four hint, never returned in full by any read API, absent from logs, exports,
  and error messages. Every credential shows owner, creation date, last-used, and validity.
- Connection health: a test-connection action per provider and per credential that performs
  a real round trip and reports the actual failure (DNS, auth, quota, model-not-found), plus
  scheduled health checks surfacing broken credentials before operators discover them
  mid-request.
- Model catalog: enumerate models per provider (synced from the provider's API where one
  exists, manually registered where not), enable and disable per model, aliases and
  canonical names, capability metadata (context window, modalities, tool support), and
  pricing per unit for cost attribution.
- Routing policy: default model, per-tenant or per-key overrides, fallback chains on
  provider error, and retry budgets — each a versioned, audited configuration the runtime
  actually reads, not a settings page writing to a store nothing consumes.
- Platform-issued API keys for this platform's own consumers: issue, scope, rotate, revoke,
  and expire; per-key rate limits and quotas; per-key usage visibility.
- Usage and cost metering: requests, tokens, latency, error rate, and spend by provider,
  model, key, user, and time window — with thresholds and alerts, because runaway spend is
  this archetype's signature incident.
- External API connections beyond model providers: the same registry, vault, health-check,
  and audit treatment for every other API the platform consumes (email, billing, storage,
  webhooks). If the platform calls it with a secret, the console manages it.
- User and member management per the common family: invite, create, edit, deactivate,
  delete, role assignment, and session revocation, with per-user visibility into keys owned
  and spend incurred.

Critical risks: secret exposure through reads, logs, error messages, or client bundles;
credentials editable without audit; a revoked key that keeps working because the runtime
caches it; routing changes that silently redirect traffic to a costlier or weaker model;
spend without caps; and cross-tenant leakage of keys or usage data.

Rules for this archetype:

- An "add provider" or "add key" capability is not implemented until the full loop works
  against a real backend: form → validated server operation → encrypted storage → masked
  display → test connection → rotate → revoke → audit trail. A form that renders but stores
  nothing is the canonical placeholder defect of this archetype.
- Key material never appears in `dataBinding` samples, seeds, logs, or evidence files. Test
  with dedicated dummy credentials and record that in the manifest.
- Revocation is a server-enforced kill, verified by a negative test that replays the revoked
  credential and observes rejection — not a status flag.
- Every mutation of a provider, credential, routing policy, or platform key is a named
  command with audit; every credential read above masked level (if permitted at all) is a
  privileged read with audit and reason capture.

### Agent-operated and AI-acting platforms

Distinct from the section above. There a model produces output a human acts on. Here autonomous agents take actions on behalf of users or the business, and each action is a privileged command with no human at the keyboard.

Expected domains:

- Agent registry: identity, owner, purpose, version, deployment state, configuration history, and rollback
- Granted tool and permission scopes per agent, and the human role each scope derives from
- Per-agent identity in audit, distinct from the human who configured, triggered, or approved the run
- Action provenance from output back to triggering input, configuration version, model version, tool calls, and the data the agent read
- Human-in-the-loop approval queues: which actions require approval, who may approve, timeout behavior, and what happens to unapproved work
- Spend, rate, and concurrency caps per agent, per tool, per tenant, and per time window, enforced server-side
- Kill switches at agent, tool, tenant, and global scope, plus a global pause that takes effect without a deploy
- Replay, rollback, and compensating actions for completed agent work, including partially completed runs
- Incident review: what the agent did, on whose authority, at what cost, what changed, and what it may no longer do

Critical risks: an agent holding broader authority than any human operator; actions that cannot be attributed to an agent, a version, and a triggering human; runaway cost or volume; prompt-injection-driven privileged action where untrusted content reaches a tool call; and no way to stop it quickly.

Rules for this archetype:

- Ground agent authority in a role with scopes, never in a service account with unlimited access. The agent's permitted actions must be a subset of some human role's.
- Treat any content the agent reads as untrusted input, not instruction. High-risk tools require human approval regardless of what the input claims.
- Audit rows must name the agent, its version, its configuration, and the triggering human or schedule. An action attributable only to "system" is a defect.
- Caps are enforcement, not reporting. A dashboard showing spend after the fact is not a cap.
- Test the kill switch and the global pause as capabilities with evidence, not as configuration flags.

## Safety features teams commonly forget

These are the controls that separate a console that can be trusted with production from one that cannot. Model each as an entry in `capability.safeguards[]` with a test. Enforcement detail lives in [security-governance.md](security-governance.md); the tables that store the evidence live in [admin-data-model.md](admin-data-model.md).

| Safeguard | Apply to | Prevents |
|---|---|---|
| Reauthentication before high-risk action | Money movement, access grants, credential changes, exports of sensitive data, production impact | A stolen or unattended session performing the worst available action |
| Two-person approval | Fraud-prone, regulated, and critical commands; agent-initiated high-risk actions | A single compromised or malicious operator acting alone |
| Role, amount, geography, and customer limits | Refunds, credits, discounts, payouts, data access, bulk scope | Authority that scales past the operator's actual mandate |
| Reason capture | Refunds, bans, suspensions, overrides, policy exceptions, exports, break-glass | Unexplained decisions that cannot be reviewed or appealed |
| Before-and-after values in audit | Every mutation of a governed field | An audit trail that proves something happened but not what changed |
| Soft delete and restore | Records with downstream references, customer impact, or retention obligations | Irreversible loss from a mistaken click |
| Version history | Content, configuration, pricing, policy, prompts, agent definitions | Silent drift with no way to see or revert the last change |
| Time-limited impersonation with a visible banner | Support access to a customer account | Operators acting as users indefinitely, unnoticed and unattributed |
| Break-glass access with alerting | Emergency access beyond normal scope | Emergency powers becoming routine and unreviewed |
| Redaction | Sensitive fields in views, search, logs, exports, error messages, and support tooling | Disclosure to operators with no business need |
| Rate limits on bulk actions and exports | Bulk commands, export generation, search-driven enumeration | Mass extraction and mass mutation at machine speed |
| Impact preview before large changes | Bulk operations, cascading deletes, configuration and migration changes | Discovering the blast radius after execution |
| Idempotency | Money, messaging, provisioning, integration calls, bulk commands, agent actions | Duplicate execution from retries, double clicks, and replayed requests |

Each safeguard needs a negative test proving it blocks the action, not only a positive test proving the happy path works. See [verification.md](verification.md).

## Capability selection test

Include a capability when at least one is true:

- It is required to complete or recover a core operator workflow.
- The underlying entity or transition already exists and needs authorized human control.
- Operators currently use scripts, direct database access, spreadsheets, or engineering escalation for it.
- It is required for security, privacy, audit, compliance, reconciliation, support, or incident response.
- Its absence creates a material operational bottleneck or uncontrolled risk.

Exclude or defer when:

- It has no real backend behavior or source of truth.
- It duplicates a safer authoritative system without a justified integration.
- No authorized role should perform it.
- The operational volume does not justify a dedicated surface and another safe workflow exists.
- It requires an unresolved business or legal decision.

## The completeness test

Apply this to every important problem the platform can have, including the exceptions and failures, not only the intended paths:

```text
Can the correct administrator
  detect the problem,
  find the affected record,
  understand its state and history,
  take the permitted action,
  verify the result,
  recover from a mistake,
  and prove who did what
without an engineer touching the database?
```

If the answer is no for any major workflow or exception, the console is incomplete regardless of how many pages exist.

Each clause demands a specific capability:

| Clause | Requires |
|---|---|
| Detect | A queue, alert, report, or health surface that surfaces the problem without someone knowing to look |
| Find | Search and filters keyed on the identifier the operator actually holds |
| Understand | A detail view with current state, transition history, related records, and notes |
| Take the action | A command capability with server-side authorization and the safeguards its risk requires |
| Verify | Observable post-action state, job outcome, and provider or downstream reference |
| Recover | Undo, restore, compensating command, or a documented and permitted recovery path |
| Prove | Audit with actor, effective identity, reason, before and after values, and correlation IDs |

Run the test per problem, not per page. Record every failing clause as an entry in `gaps[]` with a severity, and let it drive the slice order in [build-order.md](build-order.md).

## Anti-patterns

- The default generated admin look applied regardless of domain: collapsible sidebar, four KPI cards, one generic data table, gradient accent. It resembles an admin console and controls nothing.
- KPI card walls with no thresholds, owners, or drill-down actions
- Generic CRUD generated from every table
- Buttons wired only to client state, toast messages, or fake delays
- "Manage users" limited to a decorative list
- Role checks that only hide UI controls
- Editable status dropdowns standing in for validated transitions with preconditions, actors, side effects, and audit
- Bulk actions without preview or partial-failure reporting
- Bulk actions authorized once for the batch instead of per target, so one permitted row carries the rest
- Audit records written from the client, or written by the same code path that can be skipped on failure
- Exports that bypass the row and field policy applied to the detail view of the same record
- A settings page that writes to a config store nothing reads
- Impersonation without reason, expiry, banner, audit, and scope restrictions
- Deletion without impact preview, recovery, retention, and legal-hold checks
- Logs that expose secrets or sensitive records
- Agent or automation actions recorded under a shared system identity with no version or trigger
- "Coming soon," placeholder routes, sample charts, or mock data in a release claim
