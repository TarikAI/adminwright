# Admin control-plane data model

## Contents

1. How to use this model
2. Table map
3. Actors, roles, permissions, grants
4. Scope encoding
5. Authorization evaluation model
6. Audit event
7. Hash chaining: what it proves and what it does not
8. Audit retention and partitioning
9. Impersonation
10. Approvals and separation of duties
11. Jobs, saved views, configuration
12. Exports and data-subject requests
13. Indexing for real access patterns
14. Immutability rules
15. Manifest mapping
16. Profile adaptation
17. Sources

## How to use this model

This is a reference model, not a requirement. It exists so you stop reinventing audit, RBAC,
impersonation, approvals, jobs, and saved views badly on every project.

Order of preference:

1. An existing equivalent in the project wins. Extend it. Two parallel audit trails is a
   worse outcome than one imperfect audit trail.
2. If the framework ships a maintained equivalent, use it. Record the choice in `decisions[]`
   and the doc you consulted in `platform.researchSources[]`.
3. Only then adopt these tables, renamed to the project's conventions.

The runnable definition is `postgres.sql`, with per-stack translation rules, adaptation
guidance, and profile requirements in
[../assets/admin-core-schema/README.md](../assets/admin-core-schema/README.md). Read the
README before copying: three constraints here cannot be expressed by most ORMs and must stay
in the database.

Drop what you do not need. A three-operator internal tool does not need `approval_request`,
`data_subject_request`, or a hash chain. Keeping unused tables costs review attention and
implies controls that do not exist.

## Table map

| Table | Answers |
|---|---|
| `admin_actor` | Who is allowed to operate the console |
| `admin_role` | What named job functions exist |
| `admin_permission` | What discrete actions exist and what each one requires |
| `admin_role_permission` | Which role may perform which action |
| `admin_actor_role` | Who holds which role, in which scope, until when, granted by whom, why |
| `admin_policy_version` | Which policy definition was in force when a decision was made |
| `audit_event` | What happened, who caused it, to what, with what result |
| `impersonation_session` | Who acted as someone else, why, within what limits, for how long |
| `approval_request` / `approval_decision` | What was requested, by whom, approved by whom |
| `admin_job` / `admin_job_failure` | What bulk or long-running work ran and what failed inside it |
| `saved_view` | Which list configurations operators keep and share |
| `feature_flag` / `config_setting` / `config_change` | What is configured and how it changed |
| `export_request` | What data left the system, under which policy, and who downloaded it |
| `data_subject_request` | Which privacy obligations are open and when they are due |

## Actors, roles, permissions, grants

`admin_actor` is the privileged identity. Two viable shapes:

- **Reuse** the application user table and add `is_privileged`, `auth_strength`, `mfa_enrolled`.
  Simplest. Acceptable when the console is an authenticated area of the same product.
- **Separate** admin identity linked by `app_user_id`. Correct when operator accounts have a
  different lifecycle, a different identity provider, or a stricter authentication policy, or
  when the console must survive a compromise of the customer-facing auth path.

Do not store credentials here. `auth_subject` holds the identity provider's subject claim.

`admin_permission` is the atom of authorization. Key it `<resource>.<action>` so it matches the
capability id in the manifest exactly. Each permission carries its own obligations
(`requires_reason`, `requires_approval`, `requires_step_up`) rather than leaving them to
call-site convention, so a new call site cannot silently skip them.

`admin_actor_role` is a grant, not a join row. Grants carry:

- `scope` — where the grant applies (next section)
- `starts_at` / `expires_at` — `NULL` expiry means permanent; permanent high-risk grants are a
  finding at `regulated`
- `granted_by` + `granted_reason` + `ticket_ref` — every privilege has an origin story
- `revoked_at` / `revoked_by` / `revoke_reason` — revocation is a state change, never a delete

Two invariants worth enforcing in the database: an actor cannot grant a role to itself
(`granted_by <> actor_id`), and an actor cannot hold two live grants of the same role in the same
tenant scope (partial unique index filtered on `revoked_at IS NULL`).

## Scope encoding

Scope answers "where does this grant apply". Encode it as one JSON document per grant with a
fixed shape, plus a denormalized `scope_tenant_id` column for the query the console runs
constantly.

```text
{
  "tenants":      ["*"] | ["t_9f2", "t_a41"],
  "regions":      ["eu-west-1"],
  "environments": ["production", "staging"],
  "limits":       { "refund_minor_units": 50000, "currency": "USD", "bulk_rows": 500 },
  "conditions":   [ { "attr": "order.state", "op": "in", "value": ["paid", "fulfilled"] } ]
}
```

Rules that keep this workable:

- Fixed operator set. `eq`, `neq`, `in`, `not_in`, `lte`, `gte`, `prefix`. Nothing else. An
  unknown operator evaluates to deny, never to allow.
- Absent key means unrestricted on that axis only if the role's `max_scope` permits it.
  Otherwise absent means deny. Pick one rule and encode it in the evaluator, not per call site.
- `limits` are compared against the request, not the record. A refund limit constrains the
  requested amount.
- Union across grants for allow, intersection within a grant for narrowing. Two grants widen;
  two conditions in one grant narrow.

Trade-off against a dedicated policy engine:

| Signal | Stay with JSON scope | Move to a policy engine |
|---|---|---|
| Condition inputs | Attributes on the request | Relationships (`owner-of`, `member-of`, org hierarchy) |
| Scope axes | One or two plus limits | Three or more, interacting |
| Evaluators | This service only | Several services must agree |
| Policy change cadence | Same as schema | Independent deploy, own tests, own versioning |
| Explainability need | "which grant matched" | Full decision trace with rule provenance |

An engine costs you a second source of truth, decision latency, and audit correlation work. Even
when an engine decides, keep these tables as the record of who was granted what by whom — the
engine answers "may this proceed", the tables answer "why did this person have that access in
March".

## Authorization evaluation model

The decision is a function of six inputs. All six are storable.

```text
subject    admin_actor + its live admin_actor_role rows
resource   admin_permission.resource, plus the target row's tenant/owner attributes
action     admin_permission.action
scope      admin_actor_role.scope, narrowed by admin_role_permission.constraint_expr
condition  scope.conditions[] evaluated against the request context
obligation admin_permission.requires_* plus any obligations in constraint_expr
```

Evaluation order:

```text
1  authenticate       subject := admin_actor where status = 'active'
2  resolve grants     live admin_actor_role: revoked_at IS NULL
                      AND now() BETWEEN starts_at AND COALESCE(expires_at, 'infinity')
3  resolve permission admin_role_permission -> admin_permission WHERE key = requested action
4  default deny       no matching permission -> deny, record the denial
5  scope check        request tenant/region/environment must fall inside a matching grant
6  condition check    every condition on that grant must hold against the request context
7  limit check        numeric request values compared to scope.limits
8  obligations        collect requires_reason / requires_step_up / requires_approval
9  enforce            an unmet obligation is a deny, not a warning
10 record             emit audit_event with action, result, policy_version, and the reason
```

Steps 8 and 9 are where real systems fail. An obligation the decision point returns but the
enforcement point ignores is a silent policy failure — the audit trail will show `succeeded` with
no reason captured and no step-up performed. Test obligations as their own case in the
authorization matrix required by [verification.md](verification.md).

`policy_version` on the audit event is what lets you answer "under which rules was this allowed".
Without it, a policy change makes every historical decision unexplainable. Store the version, its
checksum, and a reference to the definition's location in `admin_policy_version`.

## Audit event

One append-only table. The column set exists to answer investigation questions without joining
against mutable state that may since have changed.

| Column group | Columns | Why |
|---|---|---|
| Identity | `id`, `event_uuid`, `chain_id`, `chain_seq` | Internal ordering, external reference, chain position |
| Time | `occurred_at`, `recorded_at` | Backfilled and delayed events must be distinguishable |
| Actor | `actor_id`, `actor_kind`, `effective_actor_id`, `impersonation_session_id` | Real actor and acted-as identity are different facts |
| Context | `tenant_id`, `environment`, `source_ip`, `user_agent` | Isolation and environment questions |
| Action | `action`, `target_type`, `target_id` | `action` is the capability id, not a free-text sentence |
| Justification | `reason`, `ticket_ref`, `approval_request_id` | Why this was allowed to happen |
| Correlation | `request_id`, `correlation_id`, `idempotency_key` | Ties the event to logs, traces, jobs, retries |
| Outcome | `result`, `error_code` | `succeeded`, `failed`, `denied`, `partial`, `unknown` |
| Change | `before_state`, `after_state`, `before_hash`, `after_hash`, `redaction_policy` | What changed, and under which redaction rules |
| Decision | `policy_version` | Which rules were in force |
| Integrity | `prev_hash`, `row_hash`, `hash_algorithm` | Tamper evidence |

Non-obvious requirements:

- `result` must include `denied` and `unknown`. A console that logs only successes cannot
  detect probing, and a console that forces every external call into success/failure will
  misreport timeouts where the provider may still have acted.
- `action` is the permission key. Free-text action strings make the table unqueryable within a
  year and break the manifest's `capability.auditEvents` traceability.
- `effective_actor_id` is `NULL` unless impersonating. Never overwrite `actor_id` with the
  impersonated identity — that is the single most common way impersonation audit is destroyed.
- `before_hash` / `after_hash` are hashes of the payloads, and the row hash covers the hashes
  rather than the payload text. This is what makes later payload redaction possible without
  invalidating the chain.
- Redaction is not optional. Audit payloads become the least-governed copy of sensitive data in
  the system. Apply a named redaction policy at write time and record its name.

## Hash chaining: what it proves and what it does not

Each row stores `prev_hash` (the previous row's `row_hash` in the same `chain_id`) and
`row_hash = H(canonical(fields) || prev_hash)`. Define the field list and the canonical encoding
explicitly; an unspecified serialization makes verification irreproducible, which is the same as
having no chain.

It proves, given an independently held anchor:

- A row's content was not edited after the anchor was taken.
- A row was not deleted or inserted mid-sequence after the anchor was taken.
- Two copies of the log agree or do not.

It does not prove:

- **That an event was ever written.** Nothing detects an action the code never logged. Omission
  at write time is invisible to any integrity scheme.
- **That timestamps are true.** `occurred_at` is whatever the writer supplied.
- **Anything against an attacker with write access, absent an anchor.** Recomputing the entire
  chain after an edit is trivial. Without an external anchor the chain only detects careless
  tampering.
- **Non-repudiation.** That needs signatures with keys the database cannot reach.

Anchoring options, cheapest first: replicate events to a separate account or system on write;
write the head hash periodically to append-only or object-lock storage; sign the head hash with a
key held outside the database; obtain an RFC 3161 timestamp for the head hash. Choose one and
record it in `crossCutting.audit`. A chain with no anchor is documentation, not a control.

Cost: chaining serializes inserts within a `chain_id`. Shard by tenant, region, or day if audit
write throughput matters, and verify per chain.

## Audit retention and partitioning

Partition `audit_event` by range on `occurred_at`, typically monthly. Consequences to plan for:

- The primary key must include the partition key. Use `(occurred_at, id)`.
- Create partitions ahead of time. A missing partition is an insert failure on the write path of
  every privileged action.
- Retire by detaching and archiving the partition, not by `DELETE`. Bulk deletes on the hottest
  insert table cause bloat and vacuum pressure.

Retention has three separate clocks, and conflating them is the usual mistake:

| Data | Typical driver | Note |
|---|---|---|
| Event metadata | Regulation or contract | Often the longest; frequently not an engineering decision |
| Change payloads | Data minimization | Scrub earlier than metadata; `before_hash` keeps the chain intact |
| Correlated application logs | Cost | Much shorter; do not treat as audit |

Archiving splits the chain into segments. Preserve each archived segment's head and tail hash
plus its manifest, and make verification per-segment with explicit segment linkage. Decide this
before the first archive, not after.

## Impersonation

`impersonation_session` makes support access a first-class object rather than a session flag.

Required at start: real actor, target subject, reason, expiry. `read_only` defaults to true —
opting into write impersonation should be a deliberate, separately permissioned act.
`scope_restrictions` holds the permission keys that stay denied while impersonating, so
impersonation can never widen the real actor's authority.

Enforce in the database: expiry strictly after start, a bounded maximum duration, and at most one
live session per real actor (partial unique index on `ended_at IS NULL`). Ending is a state
change with an `end_reason` of `operator_exit`, `expired`, `revoked`, or `session_lost` — sessions
that simply stop appearing are indistinguishable from sessions that were never closed.

Every audit event produced during the session carries both `actor_id` and `effective_actor_id`,
plus `impersonation_session_id`. That triple is what makes "show me everything support did as
this customer" a single query. Policy detail is in
[security-governance.md](security-governance.md).

## Approvals and separation of duties

`approval_request` stores the command key, the immutable payload, and `payload_hash`. The hash
binds the approval to exactly what was requested; without it, an approved request whose payload is
later edited is an authorization bypass with a clean audit trail.

State machine:

```text
pending ──approve(n of required)──> approved ──execute──> executed
   │                                   │                     └──> execution_failed
   ├──reject──> rejected               └──expire──> expired
   ├──expire──> expired
   └──cancel(requester)──> cancelled
```

`approved` is not `executed`. Re-evaluate authorization at execution time — the approver's or the
requester's grants may have been revoked in between.

Separation of duties can be enforced declaratively rather than in application code. Denormalize
`requested_by` onto `approval_decision`, add a composite foreign key back to
`approval_request (id, requested_by)`, and add `CHECK (approver_id <> requested_by)`. The database
then refuses self-approval regardless of which service writes the row. `required_approvals`
covers n-of-m; a unique constraint on `(request_id, approver_id)` stops one approver from
counting twice.

## Jobs, saved views, configuration

`admin_job` covers every bulk or long-running admin operation. `input_summary` and `input_hash`
are immutable after creation — a job whose inputs can change is not reconstructable. States are
`queued`, `running`, `succeeded`, `partial`, `failed`, `cancelled`, `expired`. `partial` must
exist as a distinct state; folding it into `succeeded` is how bulk failures get hidden.
Cancellation in progress is the `cancel_requested` flag rather than a separate state, because a
worker between rows is still `running` until it stops. Per-row failures go to
`admin_job_failure` so the operator can be handed the exact list that did not apply.
`(kind, idempotency_key)` unique where the key is present stops double submission.

Two extensions worth adding when the workload justifies them, both absent from the reference
schema: a `heartbeat_at` column to distinguish a running job from an abandoned one, and a
dead-letter reference for jobs that exhaust their retries.

`saved_view` stores filters, columns, and sort — never results. A shared view must be
re-authorized on every use: the viewer's scope, not the author's, decides which rows come back.
Visibility is `private` or `shared`. If the project needs finer sharing — per role, per tenant,
or global — extend the enum and add the column the new value depends on; do not overload
`shared` to mean different things in different screens.

`feature_flag` and `config_setting` are keyed by `(key, environment)` because the same key in
production and staging are different facts. `config_setting` never holds a secret value — it holds
a reference (`value_type = 'secret_ref'`). Both write to a single `config_change` history with
before/after, actor, reason, and the audit event id. Configuration changes are privileged commands
and belong in the audit trail like any other.

## Exports and data-subject requests

`export_request` records what left the system. `row_policy` and `field_policy` are the names of
the policies applied at generation time, stored on the row — the policies themselves will change,
and "what was this export allowed to contain" must remain answerable. `artifact_ref` is a storage
key, never the payload. `expires_at` is mandatory. Downloads are audit events with
`target_type = 'export_request'`; the counters on the row are a convenience, not the record.

`data_subject_request` tracks privacy obligations with a due date, a state machine, and an
explicit legal-hold interaction. The hold conflict is real and must be encoded, not left to
process: an erasure request cannot be marked fulfilled while a hold blocks it. Access and
portability requests link to an `export_request`; erasure links to an `admin_job`, because
erasure across a real system is a long-running job with partial failures.

## Indexing for real access patterns

Admin consoles have a narrow, predictable query set. Index for it and nothing else — `audit_event`
is the hottest insert path in the system and every index taxes it.

| Question | Index |
|---|---|
| Recent activity, paged | `(occurred_at DESC, id DESC)` |
| What happened to this object | `(target_type, target_id, occurred_at DESC)` |
| What did this operator do | `(actor_id, occurred_at DESC)` |
| Activity within a tenant | `(tenant_id, occurred_at DESC)` partial, `tenant_id IS NOT NULL` |
| Occurrences of one action | `(action, occurred_at DESC)` |
| Incident correlation | partial indexes on `correlation_id`, `request_id`, `idempotency_key` |
| Chain verification | unique `(chain_id, chain_seq)` |

Paginate audit and other high-volume lists by keyset on `(occurred_at, id)`, not offset. Offset
pagination on an append-heavy table shifts rows under the operator and degrades linearly.

Elsewhere: index the queue reads (`admin_job` on state where active, `approval_request` on
pending plus expiry, `export_request` on state plus expiry, `data_subject_request` on state plus
due date) and the grant lookup (`admin_actor_role` on actor filtered to live grants). Free-text
operator search belongs on the domain tables, not on `audit_event`.

## Immutability rules

| Table | Immutable after write | Permitted later change |
|---|---|---|
| `audit_event` | Every column | Payload scrub, itself audited, hashes retained |
| `admin_job` | `input_summary`, `input_hash`, `initiated_by` | State, progress, result |
| `approval_request` | `payload`, `payload_hash`, `requested_by`, `command_key` | State, decision and execution timestamps |
| `approval_decision` | Whole row | None; a reversal is a new request |
| `impersonation_session` | Real actor, target, reason, `started_at` | End state and revocation |
| `export_request` | `filters`, `row_policy`, `field_policy` after generation | State, download counters |
| `admin_actor_role` | `granted_by`, `granted_reason`, `granted_at` | Revocation fields only |
| `config_change`, `admin_job_failure` | Whole row | None |

Deleting from any of these is a defect, not a cleanup. Retention removes whole partitions or
whole archived segments under a stated policy.

## Manifest mapping

| Manifest | Table / column |
|---|---|
| `roles[].id` | `admin_role.key` |
| `roles[].mfaRequired` | `admin_role.requires_mfa`, checked against `admin_actor.mfa_enrolled` |
| `roles[].scopes[]` | axes present in `admin_actor_role.scope` |
| `roles[].separationOfDuties[]` | `admin_permission.requires_approval` plus the SoD check on `approval_decision` |
| `entities[].id` | `admin_permission.resource` |
| `entities[].tenantScoped` | presence of `tenant_id` on the domain table and on `audit_event` |
| `capability.id` | `admin_permission.key` |
| `capability.risk` | `admin_permission.risk` |
| `capability.roles[]` | `admin_role_permission` rows |
| `capability.safeguards[]` | `admin_permission.requires_reason` / `requires_step_up` / `requires_approval` |
| `capability.auditEvents[]` | permitted values of `audit_event.action` |
| `capability.authorizationPolicies[]` | `admin_policy_version.source_ref` |
| `capability.idempotency` | `admin_job.idempotency_key`, `audit_event.idempotency_key` |
| `capability.kind: job` | `admin_job.kind` |
| `capability.kind: export` | `export_request.resource` |
| `screens[].route` | `saved_view.route` |
| `crossCutting.audit` | `audit_event` plus the chosen anchoring approach |

Keep the mapping literal. When `admin_permission.key` and `capability.id` are the same string, the
authorization matrix emitted by the manifest script is checkable against the database instead of
being asserted.

## Profile adaptation

Which tables are required at each profile, and what may be dropped, is adaptation guidance
rather than model definition. It lives with the runnable schema:
[../assets/admin-core-schema/README.md](../assets/admin-core-schema/README.md).

Dropping a table is a decision. Record it in `decisions[]` with the reason, so a later
reviewer sees a choice rather than an omission.

External sources for this model are indexed in
[resource-index.md](resource-index.md) under "Access control and log management models".
