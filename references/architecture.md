# Enterprise admin-console architecture

## Contents

1. Stack seams
2. Control-plane boundary
3. Queries and commands
4. Authorization placement
5. Tenancy and environment safety
6. Consistency and concurrency
7. Asynchronous operations
8. Integrations and reconciliation
9. Audit and observability
10. Performance and scale
11. Realtime and collaborative operation
12. Headless admin surface
13. Agent- and automation-initiated actions
14. Placeholder data and the release boundary
15. Failure behavior

## Stack seams

The control plane described here is stack-neutral. Its landing points are not. Before
implementing, map it onto the project's actual framework and resolve all seven seams: admin
gateway, authorization enforcement point, command modeling, job execution, audit emission,
tenant-scope propagation, and server-side list querying.

Per-stack mappings and the traps that break each one are in
[stack-adapters.md](stack-adapters.md). A seam that does not exist yet is a finding for
`gaps[]` and part of the walking skeleton in [build-order.md](build-order.md), not something
to improvise inside one screen.

## Control-plane boundary

Treat the admin console as a privileged client of explicit application services, not as a database editor.

```text
Admin UI -> authenticated admin gateway/API -> policies -> domain commands/queries
         -> authoritative stores, providers, jobs, events, audit, observability
```

Reuse domain services and invariants shared with the product. Do not duplicate business rules in the UI. Do not expose database tables directly merely because they exist.

Separate:

- Customer APIs from privileged administrative APIs where threat models differ
- Queries from commands
- Routine operations from security administration
- Production, staging, and test environments with unmistakable identity
- Tenant administration from platform-wide administration
- Operational audit events from debugging logs

## Queries and commands

Design read models for operator decisions. A useful query returns the state, provenance, freshness, permissions, and related context required for the task without exposing unrelated sensitive fields.

Model mutations as named commands:

```text
SuspendAccount, ApprovePayout, RetryWebhook, PublishRevision, RotateKey
```

Each command should define:

- Authenticated actor and effective role
- Target and tenant/environment scope
- Preconditions and invariant validation
- Idempotency behavior
- Transaction or saga boundary
- Side effects and emitted events
- Authorization decision and obligations
- Audit event
- Result shape and operator-facing status
- Recovery or compensation path

Avoid generic `PATCH /resource/:id` for privileged lifecycle transitions when business rules matter.

## Authorization placement

Enforce authorization at the service/API boundary for every query and command. UI visibility is a usability feature, not a security boundary.

- Default deny unknown actions and scopes.
- Filter query results by scope on the server.
- Protect object-level access, not only route access.
- Re-evaluate permissions at execution time for queued or approved operations.
- Keep policy decisions testable and observable without logging sensitive inputs.
- Represent delegated, temporary, and break-glass access explicitly.
- Ensure exports and search obey the same field- and row-level policies as detail views.

Use role-based, attribute-based, or relationship-based policies according to the domain. Avoid role explosion by separating job role, resource relationship, scope, and conditions.

## Tenancy and environment safety

For multi-tenant platforms:

- Carry tenant identity through every request, query, command, event, job, audit record, and cache key.
- Require explicit platform-wide scope for cross-tenant operations.
- Make the active tenant visible and stable during a workflow.
- Prevent tenant switching from retaining selections, drafts, cached data, or bulk-operation targets.
- Test horizontal privilege escalation with guessed identifiers and search/export paths.

For multiple environments:

- Display environment identity persistently.
- Use distinct credentials, hosts, storage, and queues.
- Require extra safeguards for production mutations.
- Never silently fall back from one environment to another.

## Consistency and concurrency

Operators often act on shared and changing data.

- Use versions, ETags, timestamps, or domain locks for conflict detection.
- Show when displayed data was fetched and when it becomes stale.
- Reject or reconcile commands based on obsolete state.
- Preserve operator input when a recoverable conflict occurs.
- Explain the current state and offer reload, compare, or retry paths.
- Use transactions for invariants within one boundary and sagas/compensation across systems.
- Do not imply success until the authoritative result is known.

## Asynchronous operations

Use jobs for long-running, high-volume, provider-dependent, or retryable work.

Every admin-triggered job needs:

- Stable job ID and initiating actor
- Target scope and immutable input summary
- Queued, running, succeeded, partially succeeded, failed, cancelled, and expired states as applicable
- Progress or processed/failed counts
- Idempotency and duplicate-submission protection
- Retry policy and dead-letter/escalation path
- Result artifact or failure details safe for the operator
- Audit linkage and correlation ID
- Cancellation semantics where technically safe

Do not block a browser request for work that can outlive it. Do not hide partial failures behind a success toast.

## Integrations and reconciliation

For each provider or external system record:

- Direction and ownership of data
- Authentication and secret boundary
- Mapping between internal and external identifiers
- Delivery/sync checkpoints and freshness
- Retry, backoff, idempotency, and replay behavior
- Unknown-result handling
- Rate-limit and outage behavior
- Reconciliation strategy and mismatch queue
- Operator actions and permissions
- Monitoring and escalation owner

Never expose raw secrets. Show metadata such as credential owner, scope, creation, rotation, last use, and status when authorized.

## Audit and observability

Audit answers governance questions; telemetry answers operational questions. Maintain both.

Audit events should be structured domain events linked to actor, target, tenant, reason, request, result, and change set. Application logs should support debugging and detection without becoming an uncontrolled sensitive-data store.

Instrument:

- Query and command latency/error rates
- Authorization failures and unusual privileged activity
- Job queue age, retries, dead letters, and partial failures
- Integration delivery, lag, rate limits, and reconciliation mismatch
- Bulk-operation size and failure rate
- High-risk command attempts and approvals
- Admin UI/API errors by route and correlation ID

Provide operators only the observability needed for their job. Route deep infrastructure investigation to appropriate tools when embedding it would weaken security or usability.

## Performance and scale

Define budgets using realistic production volumes.

- Use server-side search, filtering, sorting, and pagination for large datasets.
- Use stable cursor pagination when records change frequently.
- Bound exports and bulk operations; move large work to jobs.
- Avoid N+1 queries and unbounded relationship expansion.
- Cache read models only with tenant-, role-, and field-aware keys.
- Communicate stale or eventually consistent data.
- Virtualize only when pagination does not fit the operator workflow.
- Preserve filters and position across detail navigation.

Measure time to useful content and time to complete high-frequency tasks, not only bundle size.

## Realtime and collaborative operation

Operators share queues and act on the same records. Decide deliberately whether live updates
are worth their cost.

- Poll when the data changes on a human timescale and staleness is tolerable for seconds:
  queue counts, job progress, integration health. Polling is cheaper to operate and degrades
  predictably.
- Stream when an operator's decision depends on sub-second freshness, or when a stale view
  causes duplicated work: dispatch boards, live incident queues, shared moderation queues.
- Soft-claim shared queue items. Show who is working an item and when the claim expires.
  A claim is an advisory lock for humans, not an authorization decision, and the server still
  enforces policy at execution.
- Invalidate on external change, not only on local action. A record changed by a job, a
  webhook, or another operator must not keep rendering as current.
- When the live channel drops, say so and fall back to explicit refresh. Never let a frozen
  view present itself as live — an operator acting on silently stale data is worse off than
  one who knows the feed is down.

The operator-facing side of this is in [experience-design.md](experience-design.md).

## Headless admin surface

The admin API is a product surface, not an implementation detail of the UI. Scripts, internal
tools, incident automation, and data fixes will use it whether or not you designed for it.

- Same policies, same audit events, same obligations as the interface. "The UI enforces it"
  is not an answer, because the UI is one client.
- Its own credentials, rate limits, and pagination contract. Do not reuse customer API keys.
- Versioned and documented, because scripts written against it outlive the screens.
- Included in the authorization test matrix. An endpoint reachable only by an undocumented
  path is still reachable.

## Agent- and automation-initiated actions

Non-human actors invoke admin commands: scheduled jobs, internal services, and increasingly
autonomous agents acting on model output. Treat them as subjects with their own architecture.

- Distinct machine identity, never a shared or human credential. Audit records carry the
  agent identity, not the person who deployed it.
- Scopes narrower than any human role. An agent that can do more than the operator it assists
  is an escalation path.
- Provenance from every action back to the triggering input and the configuration version
  that produced it. An action that cannot be explained cannot be reviewed or reversed.
- Spend, rate, and volume caps enforced server-side, with a global pause or kill switch that
  does not require a deploy.
- Replay and rollback for the classes of action the agent may take.
- Prompt injection is a live threat the moment an agent holds privileged tools. Constrain
  what it may do rather than trusting what it decides; content the agent reads is data, not
  instruction. Requirements are in
  [security-governance.md](security-governance.md); the operator surfaces are in
  [capability-catalog.md](capability-catalog.md).

## Placeholder data and the release boundary

Fixtures, factories, seeds, story files, and offline design tooling are legitimate and
necessary. See [test-data.md](test-data.md).

The boundary is placement, not existence. A fixture becomes a defect when it is reachable
from the paths the manifest names as authoritative — `dataBinding`, `sourceOfTruth`, and
`dataSources`. Keep fixture modules out of production dependency graphs, and let the build
fail rather than silently resolve a fixture import in a release bundle.

A value that is genuinely static in production is registered in the manifest's
`declaredStatic[]` with a reason and an approver. That registry is the only sanctioned
exception to the no-mock rule.

## Failure behavior

Design failures as normal states:

- Distinguish validation, conflict, forbidden, not found, rate limited, dependency unavailable, timeout, and unknown result.
- Return stable machine-readable error codes and safe human messages.
- Include correlation IDs without leaking internals.
- Preserve safe form input and selections.
- Offer retry only when retry is safe.
- Provide reconciliation or investigation paths for unknown external outcomes.
- Avoid optimistic success for high-risk commands unless rollback is reliable and visible.
- Degrade read-only views independently when one optional dependency fails.

