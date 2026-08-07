# Security, privacy, and governance

## Contents

1. Threat model
2. Authentication
3. Authorization
4. Bulk authorization
5. Machine and agent identity
6. Static values in the release path
7. Standards mapping
8. Privileged actions
9. Audit records
10. Impersonation and support access
11. Sensitive data and exports
12. Deletion, retention, and recovery
13. Security testing
14. Release evidence

## Threat model

Assume threats from unauthenticated attackers, compromised operator accounts, malicious insiders, confused deputies, cross-tenant access, stale privileges, forged identifiers, CSRF, injection, replay, automation abuse, insecure integrations, excessive exports, and operator mistakes.

Identify:

- Trust boundaries and authoritative identities
- Sensitive resources and operations
- Tenant and environment boundaries
- Attacker-controlled inputs
- High-value credentials and external providers
- Actions with financial, safety, privacy, availability, or irreversible impact
- Required detection and incident evidence

Do not make compliance claims solely from UI behavior.

## Authentication

- Use established identity providers and protocols rather than custom authentication when practical.
- Require phishing-resistant MFA or the strongest appropriate factor for privileged roles.
- Support SSO and centralized lifecycle management when enterprise customers require it.
- Reauthenticate or require step-up authentication for sensitive actions and credential changes.
- Apply secure session expiration, revocation, rotation, device/context visibility, and concurrent-session policy.
- Protect recovery paths at least as strongly as sign-in.
- Separate routine and privileged sessions/accounts when the threat model requires it.
- Log authentication and recovery events without logging credentials or tokens.

## Authorization

- Enforce every authorization decision server-side.
- Default deny and grant least privilege.
- Validate both action permission and object/tenant scope on every request.
- Apply field-level policy to sensitive data.
- Apply policy consistently to UI queries, search, export, APIs, jobs, events, and direct object URLs.
- Prevent self-escalation, unauthorized role assignment, and unsafe delegation.
- Support separation of duties where one actor should not initiate and approve the same action.
- Expire temporary access and review privileged assignments periodically.
- Test the authorization matrix automatically.

Represent policies clearly enough to answer:

```text
Who can perform which action on which object, within which scope,
under which conditions, with which required obligations?
```

## Bulk authorization

Authorize bulk commands per target, not per request. A single check on the batch is one of
the most common high-severity defects in an admin console: it converts a partial-scope
operator into a platform-wide one for the cost of a filter change.

- Resolve the target set server-side. Accept explicit identifiers, or re-run the filter under
  the actor's scope. Never trust a client-supplied filter, count, or "select all matching".
- Evaluate the same object-level policy for every target that the single-record command uses.
  Partition the set into permitted and denied before executing anything.
- Execute only the permitted partition. Return the denied count and reason to the operator; a
  silent drop teaches the operator the wrong mental model of their own scope.
- Treat each target as an independently recoverable unit. A failure on one target must not
  roll back or conceal the others, and re-running the batch under the same idempotency key
  must not apply it twice.
- Write one audit event per target, each carrying the shared batch identifier, plus one event
  for the batch request itself.
- The authorization matrix test must cover a scoped role whose filter results span its scope
  boundary, not only a role denied the action outright.

## Machine and agent identity

Service accounts and autonomous agents are subjects, not exceptions.

- Give each a distinct identity. Actions taken by automation must never be attributed to the
  human who configured it.
- Scope automation narrower than any human role. Automation never inherits a person's full
  authority, and a compromised agent must not be able to do more than the operator it serves.
- Apply the same policy evaluation, audit, and obligations as human actors, including reason
  capture where the command requires it.
- Rotate and revoke credentials on a schedule; record issuance, last use, and owner.
- Treat any agent that acts on model output as reachable by prompt injection. Constrain what
  it may do rather than trusting what it decides. See the agent-operated archetype in
  [capability-catalog.md](capability-catalog.md) and the automation section of
  [architecture.md](architecture.md).

## Static values in the release path

No mock, placeholder, stub, sample, random, or hard-coded value belongs in a release path.
A value that is genuinely static by design is registered in the manifest's `declaredStatic[]`
with the path it covers, the reason, and an approver.

That registry is the only sanctioned exception. Anything matching the placeholder patterns and
not registered is a release-blocking defect, and the validator treats it as one. The point is
not the word: it is that an unregistered placeholder is indistinguishable from unfinished work
that someone intended to come back to.

## Standards mapping

The requirements in this file exist to satisfy recognized control families. Sources are
indexed in [resource-index.md](resource-index.md).

| Requirement here | Control family |
|---|---|
| Least privilege, default deny, scoped grants, access review | NIST SP 800-53 AC; OWASP ASVS access control |
| Audit content, protection, and retention | NIST SP 800-53 AU; NIST SP 800-92 |
| Authentication strength and step-up | OWASP ASVS authentication; OWASP MFA guidance |
| Subject access, erasure, processing records | GDPR Articles 15, 17, 30 |
| Change control and monitoring of privileged operations | SOC 2 CC6 and CC7 |

Mapping a control is not passing an audit. It records why a requirement exists so a reviewer
can trace it, and so a team can tell which requirements are negotiable for their profile and
which are not. Regulated platforms need qualified human security and compliance review beyond
this skill.

## Privileged actions

Classify each command:

- **Low:** reversible, narrow impact, no sensitive disclosure
- **Moderate:** material workflow or customer impact
- **High:** money, access, publication, deletion, security, compliance, broad scope, or production impact
- **Critical:** systemic, difficult to recover, legally controlled, or capable of catastrophic impact

Scale safeguards to risk:

| Safeguard | Use when |
|---|---|
| Clear action label and consequence | Every mutation |
| Server validation and authorization | Every mutation |
| Reason capture | Policy, financial, access, moderation, override, or exception actions |
| Preview/impact count | Bulk, cascading, configuration, migration, or destructive actions |
| Typed confirmation | Rare, high-impact, hard-to-reverse actions |
| Step-up authentication | Sensitive data, credentials, large value, security, or production impact |
| Approval/dual control | Fraud-prone, regulated, critical, or separation-of-duties actions |
| Idempotency | Money, messaging, provisioning, integrations, bulk commands |
| Delayed execution/cancel window | Broad destructive actions where recovery is otherwise weak |
| Break-glass process | Emergency access with strong monitoring and review |

Confirmation dialogs do not replace authorization, validation, recovery, or audit.

## Audit records

Record enough to reconstruct privileged activity:

- Timestamp in a consistent time standard
- Authenticated actor and effective/delegated identity
- Tenant, environment, and source context
- Action/command and target type/identifier
- Reason, ticket, or approval reference where required
- Before and after values or a safe change summary
- Request, trace, job, and idempotency identifiers
- Authorization decision/policy version when useful
- Result, failure code, and downstream provider reference

Protect audit records against unauthorized mutation or deletion. Define retention and access. Redact secrets and minimize sensitive payloads. Make timestamps, actor display, and exported evidence unambiguous.

## Impersonation and support access

Prefer scoped support tools over impersonation. If impersonation is necessary:

- Restrict eligible roles and target accounts
- Require reason/ticket and step-up authentication
- Make the impersonated state unmistakable and persistent
- Set short expiry and prohibit privilege escalation
- Block or separately approve the most dangerous operations
- Preserve both real actor and effective user in audit events
- Notify or expose history according to policy
- Provide immediate exit and automatic termination
- Never reveal user credentials or bypass authentication by copying tokens

## Sensitive data and exports

- Classify fields and minimize collection and display.
- Mask by default; reveal only with permission, purpose, and audit when appropriate.
- Prevent sensitive values in URLs, browser storage, logs, analytics, screenshots, and error messages.
- Apply row- and field-level policy to exports.
- Use asynchronous generation, size limits, expiry, encryption, watermarking, and download audit according to sensitivity.
- Respect consent, residency, purpose limitation, and contractual boundaries.
- Treat search and autocomplete as disclosure surfaces.
- Never display secrets after creation; support rotation and revocation instead.

## Deletion, retention, and recovery

Distinguish archive, deactivate, revoke, redact, anonymize, soft delete, hard delete, and legal hold.

Before destructive execution:

- Show target scope and downstream impact
- Validate authority, ownership, retention, and legal hold
- Define effect on related records, billing, access, backups, and integrations
- Provide export or recovery when policy allows
- Use delayed or approved execution for broad impact
- Track job progress and partial failure
- Audit the request and completion separately

Do not promise immediate physical deletion when architecture or policy cannot guarantee it.

## Security testing

Test at minimum:

- Unauthenticated and expired-session access
- Role and scope matrix, including horizontal and vertical escalation
- Direct object access with guessed identifiers
- Hidden/disabled control bypass through direct API calls
- Cross-tenant search, export, cache, job, and event isolation
- Self-role changes, delegation, approval separation, and stale privileges
- CSRF, injection, unsafe file handling, and mass assignment where applicable
- Replay and duplicate execution of high-risk commands
- Sensitive data in logs, URLs, errors, analytics, and browser storage
- Impersonation start, restrictions, expiry, exit, and audit
- Audit completeness and tamper permissions

Use established security standards appropriate to the project, such as OWASP ASVS and organization-specific controls. High-risk or regulated platforms need qualified security and compliance review beyond this skill.

## Release evidence

Require evidence paths in the manifest for:

- Authentication and session tests
- Authorization matrix tests
- Tenant-isolation tests
- High-risk command safeguards
- Audit-event assertions
- Sensitive-data and export controls
- Dependency and static analysis results
- Threat model and unresolved accepted risks
- Incident and recovery procedures where required

