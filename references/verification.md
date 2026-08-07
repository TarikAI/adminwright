# Verification and definition of done

## Contents

1. Evidence-first completion
2. The completeness test
3. Profiles and the gate matrix
4. Test layers
5. Role and permission coverage
6. State and data coverage
7. Browser verification
8. Non-functional gates
9. Adversarial QA pass
10. Gap audit
11. Release gate commands
12. What the validator cannot check
13. Feedback capture
14. Reporting

## Evidence-first completion

A route existing is not evidence that an operator outcome works. A screenshot is not evidence that data and commands are real. A passing happy-path test is not evidence of authorization or recovery.

Trace every implemented capability:

```text
operator need
-> manifest capability
-> route/screen
-> query or command
-> server-side policy
-> authoritative source or persisted result
-> audit/telemetry when required
-> automated test
-> browser evidence
```

Mark a capability `implemented` only when every required link exists.

Evidence is a repository path that exists, is non-empty, and contains the assertion it claims. A boolean set to `true` is not evidence. A status string is not evidence. An `manual:` entry records a human observation rather than a checkable artifact: it warns at `internal` and `standard`, and fails at `regulated`.

## The completeness test

Apply this per workflow, not per screen. A workflow passes only when an authorized operator can perform all seven steps unaided.

```text
detect      Does the operator learn the condition exists without being told?
find        Can they locate the exact records from an entry point they really use?
understand  Can they see state, history, provenance, and why the state is what it is?
act         Can they change it through an authorized server-side operation?
verify      Does the authoritative state confirm the change after a reload?
recover     Can they undo, compensate, retry, or escalate when it goes wrong?
prove       Can a third party reconstruct who did what, when, and why?
```

Binding constraint: no step may require an engineer to run a query, edit a row, run a script against the database, or ship a deploy. If a step does, the workflow is incomplete. "Supported by engineering escalation" is a gap, not a design.

Record the failing step in `gaps[]` with the workflow name. A workflow that fails only at `recover` or `prove` is still a failing workflow.

## Profiles and the gate matrix

`profile` is set at `init` and stored at the top of the manifest. It scales which rules warn and which fail. This is the published matrix; the validator implements exactly these severities.

| Rule | internal | standard | regulated |
|---|---|---|---|
| placeholder scan on implementation fields | error | error | error |
| unregistered static/hardcoded value | error | error | error |
| `crossCutting.<section>.evidence` non-empty | warn | error | error |
| `authorization.policyTests` non-empty | warn | error | error |
| quality-gate evidence path must exist | warn | error | error |
| test/evidence file must be non-empty | warn | error | error |
| test file must reference the capability token | off | warn | error |
| `manual:` evidence accepted | warn | warn | error |
| every lifecycle state observable by a query capability | warn | error | error |
| every non-initial lifecycle state reachable by a command capability | warn | error | error |
| every declared role used by ≥1 screen and ≥1 capability | warn | error | error |
| `decisions[]` still `assumed` at release | allow | warn | error |
| separation of duties declared for critical commands | warn | warn | error |
| accessibility + performance gates | may be `not-applicable` w/ rationale | required | required |
| privileged-read audit | n/a | required if `regulatedData` | required |

Choosing a profile is a recorded decision, not a default. Write it into `decisions[]` with `status: confirmed` and a reason drawn from evidence: data sensitivity, `platform.regulatedData`, tenancy and whether tenants are external customers, blast radius of the highest-risk command, operator population, and any contractual or audit obligation. An unexamined `init` default is an assumed decision and fails the release gate at `regulated`.

Honest tiering is the point. A team must be able to pass truthfully at its tier rather than falsify the top tier. A console used by six internal staff over non-regulated data cannot produce genuine dual-control and privileged-read-audit evidence; demanding it produces invented evidence files, which is strictly worse than an accurate `internal`. Choose the tier the platform actually occupies.

Two rules keep lower tiers honest:

- A lower profile lowers the gate, never the truth requirement. Every rule that is `warn` at your profile is a real finding. Triage each one and either fix it or record it in `gaps[]` with `status: accepted` and a rationale.
- The placeholder scan and the unregistered-static rule are `error` at every profile. There is no tier at which fake data in the release path is acceptable. The only escape is `declaredStatic[]` with a reason and an approver.

Escalate the profile when any of these becomes true: `regulatedData` is non-empty, tenancy is multi-tenant with external customers, a command moves money or grants access, or an external audit obligation applies.

`validate --profile OVERRIDE` previews a stricter tier before committing to it. Never use it at release to evaluate a profile lower than `manifest.profile`.

## Test layers

Use the smallest set that provides credible coverage:

- **Domain/unit:** state transitions, calculations, limits, invariants, policy helpers
- **Integration:** database constraints, transactions, services, queues, provider adapters, audit emission
- **API/contract:** request and response shape, validation, error codes, idempotency, pagination, field policy
- **Authorization matrix:** roles, actions, scopes, object access, tenant isolation, sensitive fields
- **Component:** complex stateful components where browser tests alone are inefficient
- **Browser/E2E:** real operator workflows through UI and backend
- **Security:** escalation, direct-object access, injection/file risks, sensitive leakage, dependency/static analysis
- **Resilience:** timeouts, retries, duplicate delivery, partial failure, stale state, provider outage
- **Performance:** production-like data volume and high-frequency workflows
- **Accessibility:** automated checks plus keyboard and assistive-technology review for critical paths

Avoid mocks at boundaries where the real integration behavior is the risk. Use provider sandboxes, contract fixtures, or controlled fakes with explicit limitations.

Every layer needs data in the right shape. Build the fixtures first; see [test-data.md](test-data.md).

## Role and permission coverage

Generate the test plan from the manifest rather than from the implementation:

```text
python <skill-dir>/scripts/admin_console_manifest.py emit --manifest <project-root>/.admin-console/manifest.json --format authz-matrix --out <project-root>/.admin-console/authz-matrix.md
```

For every role verify:

- Permitted navigation, queries, fields, commands, exports, and bulk scope
- Forbidden routes and direct API operations
- Object-level and tenant-level isolation
- Self-service versus administration boundaries
- Delegation, temporary access, approval, and separation of duties
- Stale or revoked permission behavior
- Impersonation restrictions where applicable

Include negative tests. Hiding a control is not sufficient. Policy requirements are in [security-governance.md](security-governance.md).

## State and data coverage

For every data surface cover the applicable UI states: loading, empty, populated, filtered-empty, error, forbidden, conflict, stale/partial, degraded, and success.

Each of those states requires a fixture that actually produces it. The required fixture dimensions, how to generate them, and which fixture proves which state are in [test-data.md](test-data.md). A state you cannot reach with seeded data is untested, whatever the manifest says.

## Browser verification

Use a real browser against the running application and real application services or a production-faithful test environment.

For each critical workflow:

1. Sign in as the intended role.
2. Navigate from a realistic entry point.
3. Locate the target through search, filter, queue, or deep link.
4. Inspect data provenance and relevant history.
5. Execute the action, including validation and confirmation.
6. Verify the authoritative result after reload.
7. Verify related records, side effects, notification/job state, and audit event.
8. Attempt the same action as a forbidden or out-of-scope role.
9. Exercise failure, retry, conflict, or recovery behavior.
10. Repeat at required viewports and with keyboard-only interaction.

Capture screenshots only as supplemental visual evidence. Prefer assertions and traceable logs/results.

## Non-functional gates

Define project-specific thresholds and evidence for:

- Build, type checking, linting, and test pass
- Accessibility conformance and keyboard workflows
- Page/query/command performance at production-like volume
- Error rate, timeout, retry, queue age, and integration lag
- Browser console and unhandled network errors
- Responsive layouts and supported browsers
- Localization, timezone, and currency behavior
- Security scans, threat-model review, and accepted risk
- Backup, recovery, rollback, and migration rehearsal where relevant
- Observability dashboards/alerts for critical admin operations

Do not use "reasonable," "fast," or "secure" as a gate. Record a measurable threshold or review artifact. Accessibility conformance targets WCAG 2.2 AA (https://www.w3.org/TR/WCAG22/) unless a stricter requirement applies.

## Adversarial QA pass

The adversarial pass is a separate role, not a phase of implementation. The `qa` agent runs it and must not be the agent that implemented the capability. In single-agent operation this is an explicitly declared pass that re-reads the code and the policies from disk rather than recalling what was written. See [multi-agent.md](multi-agent.md) for claiming, handoff, and the reviewer-independence rule.

The `qa` agent files findings as `gaps[]` entries and does not fix them silently. Fixing is a return trip to `implementer`.

Attack the console; do not exercise it.

| Attack | Technique | Pass condition |
|---|---|---|
| Guessed identifiers | Request records by sequential, adjacent, deleted, and other-tenant IDs harvested from your own responses | Server denies on object scope, not on UI absence; response does not distinguish forbidden from missing where that leaks existence |
| Hidden-control bypass | Call the endpoint directly for every action the UI hides or disables for the current role | Server-side policy denies; the deny is audited where policy requires |
| Cross-tenant read via search | Search, autocomplete, filters, saved views, and sort keys for values belonging to another tenant | No cross-tenant row, count, suggestion, or aggregate is returned |
| Cross-tenant read via export | Request an export with a widened filter, an out-of-scope ID list, or a modified scope parameter | Export applies the same row and field policy as the list; download is authorized and audited |
| Replayed commands | Resubmit high-risk commands: double-click, retried request, reused idempotency key, replayed webhook, resent job message | Exactly one effect; the duplicate is rejected or absorbed and observable in the audit trail |
| Stale permissions | Execute with a session, token, or API key minted before a role change, revocation, or tenant removal; hold a cached policy decision across the change | Execution fails after revocation; cached decisions expire within the declared window |
| Out-of-scope bulk | Select-all-filtered where the filter spans forbidden rows; inject out-of-scope IDs into the explicit-ID list | Preview count matches the authorized subset; forbidden rows are excluded and reported, not silently processed |
| Impersonation limits | While impersonating, attempt escalation, prohibited commands, expiry extension, nested impersonation, and action after exit | Every attempt denied; real actor and effective user both present in audit; session terminates on expiry |
| Audit tampering | Update or delete audit rows through the API, ORM, and admin surfaces; force a downstream failure mid-command; forge the actor field | Audit is append-only to application credentials; a failed command still records the attempt; actor derives from the authenticated session, never from request input |

Extend the list from the platform's own risks. Testing method references: OWASP Web Security Testing Guide (https://owasp.org/www-project-web-security-testing-guide/) and OWASP ASVS (https://owasp.org/www-project-application-security-verification-standard/). High-risk or regulated platforms need qualified security review beyond this skill.

## Gap audit

Perform the audit after tests pass. Search for omissions that tests designed from the implementation may miss.

Compare:

- Customer-facing capabilities against administrative obligations
- Database entities/enums against manifest entities/lifecycles
- Service commands and internal APIs against admin actions
- Events, jobs, queues, webhooks, and integrations against operational views
- Roles and policies against UI navigation and authorization tests
- Feature flags, plans, limits, and configuration against management surfaces
- Support scripts, SQL snippets, spreadsheets, and runbooks against missing console capabilities
- Incident history and recurring tickets against missing observability or recovery tools
- Routes against tests and supported states
- Privileged actions against audit events and safeguards

Classify findings:

- **Critical:** security, tenant isolation, money, data loss, safety, legal, or false-success risk
- **High:** core workflow cannot be completed or recovered without engineering/manual data access
- **Medium:** significant inefficiency, missing state, accessibility, observability, or resilience gap
- **Low:** polish or rare inconvenience with a safe workaround

Fix in-scope critical and high findings before release.

## Release gate commands

Run the project's own build, type check, lint, tests, security checks, and browser tests first. Then run the three manifest gates in this order. Use `py -3` or `python3` if `python` is not on PATH.

```text
python <skill-dir>/scripts/admin_console_manifest.py validate --manifest <project-root>/.admin-console/manifest.json --project-root <project-root> --phase release
python <skill-dir>/scripts/admin_console_manifest.py coverage --manifest <project-root>/.admin-console/manifest.json --project-root <project-root>
python <skill-dir>/scripts/admin_console_manifest.py emit --manifest <project-root>/.admin-console/manifest.json --format gap-report --out <project-root>/.admin-console/gap-report.md
```

| Command | Exit 0 | Non-zero exit means |
|---|---|---|
| `validate --phase release` | No rule at `error` severity for this profile failed. Warnings may still be printed. | At least one gate rule failed at `error` severity. Release is blocked. Fix the finding or change the fact; do not lower the profile to clear it. |
| `coverage` | No structural gap found. | Orphans exist in one direction or the other: a capability with no screen, a screen with no capability, a lifecycle state no query observes, a non-initial state no command reaches, a role no screen or capability uses, an integration with no operational view. |
| `emit --format gap-report` | The report was written. | The manifest could not be read or the output path could not be written. The exit code says nothing about the findings; read the report. |

Add `--json` to `validate` and `report` for machine-readable output when a CI job or another agent consumes the result.

Release validation must fail when any of these apply:

- Required capability is planned, partial, unknown, or missing evidence
- Implemented mutation lacks a server-side command/API or policy
- High/critical action lacks safeguards, audit events, or tests
- A data surface uses mock/placeholder data in the release path
- Required role or tenant boundary lacks negative tests
- Required screen states are not covered
- Build or required quality gate is not passed
- Evidence paths do not exist
- Known critical/high gap is unresolved without an accepted-risk decision

Never report completion with a non-zero gate and a narrative explanation.

## What the validator cannot check

The validator is deterministic and structural. It reads the manifest and the filesystem. It verifies:

- Schema conformance to manifest v2 and internal reference integrity
- Placeholder tokens in the scanned implementation fields, with `declaredStatic[]` as the only escape
- Presence of the required links per capability: server operations, policies, audit events, safeguards, tests, evidence
- Existence and non-emptiness of every evidence and test path
- At `regulated`, that the referenced test file mentions the capability token
- Lifecycle observability and reachability, role usage, and unresolved `assumed` decisions
- Profile-scoped severity for every rule in the matrix

It cannot verify:

| The validator sees | It cannot tell you |
|---|---|
| A test file exists, is non-empty, and names the capability | Whether the test asserts anything, asserts the right thing, or would fail if the guard were removed |
| An authorization policy is referenced | Whether the policy defaults to deny, scopes to tenant, or covers the export and API paths |
| `dataBinding` names a repository method | Whether that method returns authoritative data or a constant |
| An audit event is declared | Whether the running command emits it, or whether it is emitted on failure |
| An evidence path exists | Whether it was regenerated in this build or is stale from three changes ago |
| A `not-applicable` status has a rationale | Whether the rationale is true |
| The manifest lists a capability | Whether the code that implements it still exists |

Residual responsibilities stay with the agent:

1. Read the body of each cited test. Confirm it asserts the outcome, not the absence of an exception.
2. Break the guard locally and confirm the test fails, for at least every `high` and `critical` capability.
3. Follow `dataBinding` to the query and confirm it reaches the declared source of truth.
4. Execute the command and read the audit record it produced, rather than trusting the declaration.
5. Confirm evidence artifacts were produced by this build.
6. Have a second agent, or an explicitly separate pass, review what you implemented. The implementer never sets its own `reviewStatus: reviewed`.

A green `validate` means the structure is sound and nothing is obviously fake. It is a floor, not a proof. Do not report it as one.

## Feedback capture

Verification is where the skill learns. Every friction, wrong instruction, missing pattern, and tooling failure encountered during the passes above is recorded before it is forgotten:

```text
python <skill-dir>/scripts/admin_console_manifest.py add --manifest <project-root>/.admin-console/manifest.json --kind feedback --json "{\"observation\":\"...\",\"category\":\"gap\",\"proposedChange\":\"...\",\"evidence\":[\"...\"]}"
```

Categories: `gap`, `friction`, `incorrect-guidance`, `new-pattern`, `tooling`.

Record the observation against the project first. Promote it into the skill only under the rules in [skill-evolution.md](skill-evolution.md):

```text
python <skill-dir>/scripts/admin_console_manifest.py lesson add --title "..." --category "..." --scope "..." --trigger "..." --rule "..." --evidence "..."
```

Anything factually wrong in the skill promotes immediately. Everything else waits for a second project or explicit human confirmation. Project-specific conventions belong in the project's own agent instructions, not in the skill.

## Reporting

Report:

- Operator outcomes verified
- Profile, and why that profile
- Roles, scopes, and environments tested
- Real data sources and commands exercised
- Dangerous-action safeguards and audit evidence
- Test and quality-gate results
- Manifest validation and coverage exit status
- Adversarial pass performed, by which role, with findings
- Gap-audit findings fixed
- Remaining blocked/deferred/not-applicable items and rationale
- Feedback recorded and lessons proposed

Avoid a page-by-page screenshot dump. Summarize by operational domain and link to evidence.
