# Security, privacy, and governance

## Contents

1. Threat model
2. Authentication
3. Rate limiting and lockout
4. Authorization
5. Bulk authorization
6. Machine and agent identity
7. Outbound requests from operator-supplied URLs
8. CSRF by stack family
9. Static values in the release path
10. Standards mapping
11. Privileged actions
12. Break-glass access
13. Audit records
14. Impersonation and support access
15. Provisioning and deprovisioning
16. Sensitive data and exports
17. Deletion, retention, and recovery
18. Security testing
19. Release evidence

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
- Ending access is part of authentication: see Provisioning and deprovisioning.

## Rate limiting and lockout

Every endpoint an attacker can drive at machine speed needs a server-side limit, chosen per class rather than one global number. The limits are configuration: record the values in `decisions[]` and test them.

| Endpoint class | Limit and lockout rule |
|---|---|
| Sign-in | Count failures per account, not only per source address, with a threshold, an observation window and a lockout duration that backs off exponentially. Answer identically for an unknown account and a wrong password. Keep recovery reachable while the account is locked, or lockout becomes a way to lock operators out |
| MFA verification | Bound attempts per challenge and per account across all factor types; a six-digit code is a small space. Requesting a fresh challenge must not reset the count |
| Recovery and invitation | Limit per account and per requester; tokens are single-use and short-lived; the flow is no weaker than sign-in |
| API keys and service credentials | Limit per key, so one leaked key cannot be replayed at speed or used to enumerate |
| Search, export and bulk endpoints | Limit volume and concurrency per actor and per tenant; enumeration through search is a disclosure path. Reaching the limit is an audited `denied` event, not a silent slowdown |

Enforce limits in a store shared by every instance (per-process counters multiply with the instance count), return a rate-limited status with a retry hint, and key them on the authenticated actor, account and tenant. Lockout and its release are privileged operations: an authorized operator can see who is locked out and why, clearing one is a command with reason capture and audit, and repeated lockouts of a privileged account raise an alert. Source: [OWASP Authentication Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html) (lockout parameters, uniform responses, recovery as strong as sign-in).

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

## Outbound requests from operator-supplied URLs

An admin console lets operators register endpoints that the server then calls: provider base URLs, webhook targets, import-from-URL, "test connection", OAuth token endpoints, link and file fetchers. Each is a server-side request forgery (SSRF) primitive, and the credentials the server attaches make it worse: an operator, or an attacker holding an operator session, can aim the server at the cloud metadata service, an internal admin port or a host they control.

- **Allowlist first.** Prefer choosing from registered providers or an allowlist of hosts the organization owns. Where an arbitrary URL is the product (a custom provider), permit `https` only, validate scheme, port and host, and treat a denylist as a last resort, never the only control.
- **Resolve, check, then connect to the address you checked.** Resolve the name server-side, reject every resolved address that is loopback (`127.0.0.0/8`, `::1`), private (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`), link-local (`169.254.0.0/16`, which holds the cloud metadata address `169.254.169.254`) or multicast, and reject `metadata.google.internal`. Also reject IPv4-mapped IPv6 and decimal, octal or hex spellings of the same addresses. Pin the connection to the checked address: resolving again between check and connect is the DNS-rebinding hole. Re-validate on every request, because DNS answers change after the URL was saved.
- **Do not follow redirects** automatically; if a flow needs them, apply every rule above to each hop.
- **Egress control outside the application.** Send outbound calls through an egress proxy or a network policy that reaches only intended destinations, and run the fetcher where the metadata service is unreachable (on AWS, require IMDSv2).
- **Return the failure class, not the response.** "Test connection" reports DNS failure, refused, timeout, TLS or authentication error; it never echoes the upstream body, headers or redirect target, and it caps time and size. An echo turns a fetcher into a read primitive.
- **Changing a URL that carries a credential is a privileged command.** Editing the base URL of a provider that holds an API key redirects the key to a new host. Require step-up authentication, reason capture and an audit event for that edit, and never attach a stored credential to a URL changed since the credential was last authorized.

Source: [OWASP SSRF Prevention Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html) (allowlist preferred, redirects off, egress filtering, minimum denylist ranges, IMDSv2). Test with base URLs of `http://169.254.169.254/`, `http://localhost:<admin port>`, a name that resolves to a private address, a name that flips between a public and a private address, and a public URL that redirects to a private one: each must be refused and audited.

## CSRF by stack family

A browser sends a session cookie on a forged cross-site request; a bearer token in an `Authorization` header is not attached that way. So an endpoint that accepts the session cookie needs a CSRF defense and a bearer-only API does not (ASP.NET Core's guidance says the same). The defense the framework ships is usually enough, provided it was not switched off:

| Family | Built-in defense | Verify |
|---|---|---|
| Django | `CsrfViewMiddleware`, on by default; `X-CSRFToken` header for AJAX | still in `MIDDLEWARE`; each `csrf_exempt` view is a webhook, not an admin action |
| Laravel | the `web` group's request-forgery middleware (`PreventRequestForgery` in the 13.x docs, which checks `Sec-Fetch-Site` and falls back to a token; earlier versions call it `VerifyCsrfToken`); `X-CSRF-TOKEN` and `X-XSRF-TOKEN` headers; SPAs use Sanctum | admin routes sit inside the `web` group; the exclusion list holds only webhooks |
| Rails | `protect_from_forgery`, on in new apps through `default_protect_from_forgery`; `csrf_meta_tags` and the `X-CSRF-Token` header (Turbo sends it) | no admin controller skips forgery protection; custom JavaScript sends the header |
| ASP.NET Core | Razor Pages automatic; MVC `[AutoValidateAntiforgeryToken]`; minimal APIs `UseAntiforgery()`; `X-XSRF-TOKEN` for SPAs | `[IgnoreAntiforgeryToken]` and `DisableAntiforgery()` only on bearer-token endpoints |
| Next.js | Server Actions accept `POST` only and compare `Origin` with `Host` (`serverActions.allowedOrigins` behind a proxy) | the guide documents this for Server Actions only: give every cookie-authenticated Route Handler that mutates its own `Origin` check or token |
| Express, NestJS, Go, others with none built in | nothing by default | add a maintained CSRF-token library or a strict `Origin` and `Sec-Fetch-Site` check on every non-safe method; confirm the library is still maintained |

Across families: state changes never ride on `GET`; prefer a synchronizer token or a signed double-submit cookie (the naive double-submit cookie is discouraged); treat `SameSite` cookies and `Origin` or `Sec-Fetch-Site` checks as defense in depth, not the only control; and remember that XSS defeats every CSRF defense, so output encoding and a content security policy stay in scope. Sources: [OWASP CSRF Prevention Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html) and each framework's own CSRF documentation, read at the pinned version. Test: a forged cross-site `POST` that carries a valid session cookie and no token is rejected on every state-changing route, including export, bulk, impersonation start and break-glass grant.

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
| Idempotency | Money, messaging, provisioning, integrations, bulk commands. A command whose retry is naturally safe says so as `idempotency: not-required: <reason>`; an empty field, or a bare `not-required`, fails release |
| Delayed execution/cancel window | Broad destructive actions where recovery is otherwise weak |
| Break-glass process | Emergency access with strong monitoring and review; see the next section |

Confirmation dialogs do not replace authorization, validation, recovery, or audit.

## Break-glass access

Break-glass is emergency elevation beyond a role's normal scope. It exists so that an outage is not blocked on a permission request, and it is safe only when it is loud, short and reviewed.

- **Who.** A short, named list of eligible operators, never a standing role and never a shared account. Changing the eligibility list is itself a privileged, audited command.
- **How.** A separate command, not a role toggle. It requires a reason and an incident or ticket reference, step-up authentication, a stated scope (tenant, resource, action) and a duration. Where a second person is available, they approve; where approval would defeat the purpose, review afterwards is mandatory instead.
- **Expiry.** A short mandatory lifetime, hours not days, with automatic revocation and no renewal without a new request. The elevated state is visible in the interface for its whole duration. In the reference schema this is an `admin_actor_role` grant whose `expires_at` is set and whose `granted_reason` and `ticket_ref` are filled.
- **Audit.** The request, the grant, every action taken under it (each carrying the grant's identifier) and the end (`expired` or `revoked`) are events. Break-glass never bypasses the audit trail, the append-only protections or the redaction of secrets.
- **Alerting.** Notify the security owner when the grant is made, not when it is reviewed; alert again on a grant outside working hours or above a count threshold.
- **Review.** Someone other than the grantee reviews every grant within a stated window. An unreviewed grant is a `gaps[]` entry. Routine use means a missing capability or role: model it instead.
- **Test.** An expired grant is rejected; an action outside the granted scope is denied; a grant without a reason is refused; the alert fires.

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

**Tamper-evident means the hash chain, not a slogan.** The release gate requires `crossCutting.audit.tamperProtected`; where it is claimed, it is implemented as the reference schema does. `audit_event` carries `chain_id`, `chain_seq`, `prev_hash`, `row_hash` and `hash_algorithm`; the `audit_event_chain_link` trigger assigns them, so writers never supply them; and `verify_audit_chain()` recomputes every row and lists gaps, broken links, edited rows and payload-hash mismatches (empty output means internally consistent). Append-only permissions prevent tampering, the chain detects it, and only a head hash anchored outside the database keeps detection meaningful against an attacker with write access. Run the verifier on a schedule and after every restore, and cite its output and the anchor's location in `crossCutting.audit.evidence`. What the chain proves and does not prove, and how to anchor it, is in [admin-data-model.md](admin-data-model.md#hash-chaining-what-it-proves-and-what-it-does-not).

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

## Provisioning and deprovisioning

Leaving the identity provider is not leaving the console. Single sign-on authenticates at sign-in; an operator disabled at the provider keeps every session, refresh token, API key and grant they already hold until something revokes them.

- **What deprovisioning ends:** active sessions and refresh tokens, personal API keys and service credentials the person owns, role grants and delegations, pending approvals they requested, live impersonation or break-glass grants, and their queue claims and assignments (reassign the work). The actor row stays, deactivated, because audit events reference it.
- **SCIM has two exits, and the console decides what each means.** In RFC 7643 `active` is a service-provider-defined administrative status whose typical meaning is that `false` suspends the account. In RFC 7644 a `DELETE` may be a soft delete, but the provider must then answer `404` for every operation on that resource and omit it from queries. Map `active: false` to immediate deactivation plus revocation of everything above (reversible), and `DELETE` to the same plus retirement of the identifier. Reactivation restores the ability to sign in and nothing else: revoked tokens and grants stay revoked.
- **Bound the delay.** State the maximum time from provider disable to lost access, process SCIM events within it, and monitor failed deprovisioning calls as incidents, not log noise.
- **Just-in-time provisioning grants no privilege.** A first SSO sign-in creates an unprivileged actor. Roles that derive from provider group claims are re-evaluated at every sign-in and on every SCIM group update, so a stale group is not a stale privilege.
- **Local exceptions.** The few accounts that bypass SSO for a provider outage are MFA-protected, monitored and treated as break-glass.
- **Review.** Periodically list who holds which role, since when, and when they last used it; grants expire by default.
- **Audit.** Provisioning and deprovisioning events are audited with the provider or SCIM client as the machine identity that acted.
- **Test.** Disable an operator at the provider and assert that the existing session, the API key and an open export are rejected within the stated window, that queue items are released, that grants are revoked, and that audit events were written.

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
- CSRF on every cookie-authenticated state-changing route, injection, unsafe file handling, and mass assignment where applicable
- Outbound-request abuse: metadata, loopback, private and rebinding targets, redirects, and credential redirection through an edited base URL
- Rate-limit and lockout behavior for sign-in, MFA, recovery, export and bulk endpoints
- Deprovisioning: sessions, keys and grants end within the stated window
- Break-glass expiry, scope, alerting, and review
- Replay and duplicate execution of high-risk commands
- Sensitive data in logs, URLs, errors, analytics, and browser storage
- Impersonation start, restrictions, expiry, exit, and audit
- Audit completeness, tamper permissions, and `verify_audit_chain()` output after an attempted edit

Use established security standards appropriate to the project, such as OWASP ASVS and organization-specific controls. High-risk or regulated platforms need qualified security and compliance review beyond this skill.

## Release evidence

Require evidence paths in the manifest for:

- Authentication and session tests
- Authorization matrix tests
- Tenant-isolation tests
- High-risk command safeguards
- Audit-event assertions and chain verification output
- Sensitive-data and export controls
- Outbound-request, rate-limit, CSRF, deprovisioning, and break-glass tests
- Dependency and static analysis results
- Threat model and unresolved accepted risks
- Incident and recovery procedures where required

Each item is a repository path in the field that owns it (`crossCutting.<section>.evidence`, a gate's `evidence`, a capability's `tests`, `policyTests` and `evidence`). A path is opened and judged; an `http(s)` address or a `command:` line is supplemental and cannot satisfy a required list alone, and a `manual:` observation warns and, at `regulated`, fails. What each entry kind satisfies is in [verification.md](verification.md).

