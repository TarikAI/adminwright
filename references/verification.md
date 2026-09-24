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
15. External review pass (optional)

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

Evidence is a repository path that exists, is non-empty, and contains the assertion it claims. A boolean set to `true` is not evidence. A status string is not evidence. An entry may instead be an `http://` or `https://` address, `command:<a command line to re-run>` or `manual:<a human observation>`. Only paths are opened, so none of those three can satisfy a required list on its own: a list made only of them fails `evidence-not-resolvable` (a warning at `internal`, an error at `standard` and `regulated`). A `manual:` entry also raises `manual-evidence` (a warning at `internal` and `standard`, an error at `regulated`).

What is content-scanned: test files (`tests[]`, `policyTests`, and the `tests` of screens, queues and integrations) are checked for shape and assertions but never scanned for placeholder words. Run output listed as `evidence` (capability, gate, `crossCutting` and fixed-gap evidence) is scanned by whole word. Manifest implementation fields are scanned hardest, including inside identifiers. The word lists, the skipped framework idioms and the `declaredStatic` escape for a captured log are in [test-data.md](test-data.md).

Evidence paths are repository-relative and resolve against the manifest's `projectRoot` (written by `init`, relative to the folder holding the manifest; absent means that folder's parent), so `validate`, `add`, `set` and `claim` judge a path the same way from any working directory. A path that resolves outside the project is refused. `add` and `set` rewrite backslashes as `/` in `tests`, `evidence` and `policyTests` and say so.

### Capability fields the validator requires

| Field | Required when | Notes |
|---|---|---|
| `outcome`, `roles`, `serverOperations`, `authorizationPolicies`, `dataBinding`, `tests`, `evidence` | `status: implemented` | non-empty; `dataBinding` and the operation lists are placeholder-scanned |
| `surface` | always optional | any of `ui`, `api`, `cli`, `job`, `chat`; absent means `["ui"]` |
| `uiRoutes` | `"ui"` in `surface` | each route must match a screen whose `capabilities` list this capability |
| `entrypoints`, `policyTests` | any non-`ui` surface | the route, command or handler operators invoke, and the test files that prove who may invoke it |
| `idempotency` | release, `kind` is `command`, `job` or `export` | a mechanism, or `not-required: <reason>` (the colon and reason are required) |
| `auditEvents` | `kind` is `command`, `job` or `export` | at least one event |
| `safeguards`, `recovery` | `risk` is `high` or `critical` | non-empty |
| `implementedBy` | `status: implemented` | who built it; stamped by `claim --role implementer`, never cleared by `release-claim` |
| `reviewStatus`, `reviewedBy` | release | `reviewed` needs a `reviewedBy` that is not the implementer (an error at `regulated`, a warning at `standard`); set `reviewedBy` before `reviewStatus`. At every profile, `reviewed` is refused while an open critical or high gap targets the capability, `contested` needs an open gap naming it, and `contested` never becomes `reviewed` directly |
| `contestedBy` | a reviewer contested it | the contester may not also be the `reviewedBy` that clears it (`contest-self-cleared`) |
| `rationale` | `status` is `blocked`, `deferred` or `not-applicable` | a `not-applicable` capability needs nothing else; a command, job or export also needs a confirmed decision that names it |

`implementedBy` is compared with `reviewedBy` together with `owner` and every implementer that ever held the capability (`agents[].previouslyOwned`), so releasing a claim never launders self-review.

Elsewhere in the manifest:

- **`platform.domainTerms`** declares domain nouns that read like placeholders, such as a lab's `Sample`, a task app's `Todo` or an exam product's `Mock exam`. Each term must name an entity by its id or name (`domain-term-unbacked`), so `mock` cannot be declared away without modelling a Mock.
- **`decisions[].kind`** is one of `profile`, `not-applicable`, `risk-acceptance`, `buy-vs-build`, `design` or `other`. A `not-applicable` gate other than `tests` and `security` (which never are), and a `not-applicable` command, job or export, each need a confirmed decision of kind `not-applicable` whose `appliesTo` names it.
- **Keys the schema does not declare** are `unknown-field`, except keys beginning `x-`, which hold project-specific data.

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

`profile` is set at `init` and stored at the top of the manifest. It scales which rules warn and which fail. The exact severity of every rule at every profile is generated from the validator into [rule-matrix.md](rule-matrix.md) (`emit --format rule-matrix`), and a test fails when that file drifts from the code, so no other document repeats the numbers. Every finding prints its rule id in brackets; look the id up there. In outline:

- **Structural and honesty rules fail at every profile:** wrong types (`manifest-type`), ids, unknown references, the placeholder scan, unregistered static values, evidence paths that do not exist or sit inside `.admin-console/`, missing gates, unresolved critical and high gaps, forbidden `declaredStatic` paths.
- **Quality rules warn at `internal` and fail at `standard` and `regulated`:** evidence that resolves, is non-empty and says what ran; `crossCutting` evidence and `policyTests`; test files that look like tests and assert something; lifecycle observability and reachability; unused roles; `unknown-field` (a key the schema does not declare, so a typo in `add` or `set` surfaces instead of vanishing).
- **Late rules warn until `regulated`:** `manual:` evidence, separation of duties for critical commands, an independent `reviewedBy`, a recorded `implementedBy`.
- **Strict rules are off at `internal`, warn at `standard` and fail at `regulated`:** a test file that names the capability, `decisions[]` still `assumed`, evidence shared between claims, gate evidence unrelated to its gate, self-approved static values, an undecided `not-applicable` command.
- **Tiered rules:** the `accessibility` and `performance` gates may be `not-applicable` with a rationale only at `internal`; privileged reads of regulated data must be audited at `regulated`, and at `standard` when `platform.regulatedData` is non-empty.
- **Completeness rules** (starred in the table) describe an unfinished model, not a malformed one, so before `--phase release` they warn whatever the profile says. A half-built manifest is not blocked; a released one is.

Choosing a profile is a recorded decision, not a default. Write it into `decisions[]` with `status: confirmed` and a reason drawn from evidence: data sensitivity, `platform.regulatedData`, tenancy and whether tenants are external customers, blast radius of the highest-risk command, operator population, and any contractual or audit obligation. `init` records the choice as an `assumed` decision, `dec.profile` (`appliesTo: ["*"]`, the whole manifest), so an unexamined default cannot pass unnoticed: `decision-assumed` warns at `standard` and fails the release gate at `regulated` until the decision is confirmed with a real reason.

```text
python <skill-dir>/scripts/admin_console_manifest.py set --manifest <project-root>/.admin-console/manifest.json --path 'decisions[dec.profile].reason' --value 'multi-tenant, external customers, card data in scope; confirmed with the product owner'
python <skill-dir>/scripts/admin_console_manifest.py set --manifest <project-root>/.admin-console/manifest.json --path 'decisions[dec.profile].status' --value confirmed
```

Honest tiering is the point. A team must be able to pass truthfully at its tier rather than falsify the top tier. A console used by six internal staff over non-regulated data cannot produce genuine dual-control and privileged-read-audit evidence; demanding it produces invented evidence files, which is strictly worse than an accurate `internal`. Choose the tier the platform actually occupies.

Two rules keep lower tiers honest:

- A lower profile lowers the gate, never the truth requirement. Every rule that is `warn` at your profile is a real finding. Triage each one and either fix it or record it in `gaps[]` with `status: accepted` and a rationale.
- The placeholder scan and the unregistered-static rule are `error` at every profile. There is no tier at which fake data in the release path is acceptable. The only escape is `declaredStatic[]` with a reason and an approver.

Escalate the profile when any of these becomes true: `regulatedData` is non-empty, tenancy is multi-tenant with external customers, a command moves money or grants access, or an external audit obligation applies.

`validate --profile <profile>` previews a stricter tier before committing to it. At `--phase release` an override lower than `manifest.profile` is refused (exit 2) unless `--allow-lower-profile` is passed, in which case the output says `evaluated at <x>; manifest profile is <y>`. Every summary line names the effective profile.

### What evidence may point at

A gate is only as strong as the file it cites, so the validator judges the whole set of citations, not each file alone:

- `tests`, `evidence` and `policyTests` are arrays of strings. Any other shape is `manifest-type`, an error at every profile.
- Nothing under `.admin-console/` counts as evidence (`evidence-inside-manifest-dir`): the manifest is the claim, not proof of it.
- A file cited as a test must be shaped like one and contain a real assertion. The shape is the runner's own convention for the file's language, such as `*.spec.ts`, `test_*.py`, `*_test.go`, `*_spec.rb` or `*Test.php`, or a runner's marker inside a source file that runner executes. JVM, .NET and Swift files also need a test annotation and a test name or test folder. A log or a note is never a test, whatever its name. Comments, `import { assert }`, `console.assert`, Kotlin `check()` and Rust `.expect()` are not assertions, and a tautology such as `expect(true).toBe(true)` does not count. At `regulated` a spec cited by N capabilities needs at least N assertions, unless it is parametrised (`it.each`, `@pytest.mark.parametrize`, `[Theory]`, a Go table loop).
- Test files are not placeholder-scanned; the boundary is where a path appears, not what the file contains.
- A test file is not gate evidence (`evidence-is-test-file`), one file may not be the evidence for more than two gates or for a gate and a `crossCutting` section (`evidence-shared`), and one spec may not be the test for more than three capabilities, or for capabilities, screens and `policyTests` at once (`test-file-shared`).
- A cited test must name its capability (`evidence-token-match`): the capability id or one of its `serverOperations`, never a `uiRoute`, because every capability on a screen shares the route. A warning at `standard`, an error at `regulated`.
- A gate's evidence must mention the gate id or a word from the gate's optional `command` field (`gate-evidence-unrelated`, a warning at `standard`, an error at `regulated`): record `command: "tsc --noEmit"` and the log of that command qualifies.
- Evidence is the output of a run (`evidence-not-output`): project and configuration files (`package.json`, lockfiles, `*.config.ts`, CI workflows) are never evidence, and source code is never gate evidence.
- A `passed` gate whose log ends with a non-zero `# exit code:` line is `gate-evidence-failed`, an error at every profile: the run the log records failed.
- `declaredStatic[].path` may name a value, never a claim about the implementation: `dataBinding`, `sourceOfTruth`, `serverOperations`, `authorizationPolicies`, `auditEvents`, `tests`, `evidence`, `dataSources`, `policyTests` and `uiRoutes` are refused (`declared-static-forbidden-path`). An `approvedBy` that names an agent, or the capability's own implementer or reviewer, is `declared-static-self-approved`. `emit --format gap-report` and `report` list every exemption with its reason and approver.
- A `not-applicable` command, job or export must be named by a confirmed `decisions[].appliesTo` (`not-applicable-undecided`); the validate summary prints `not-applicable: N`.

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

**Required screen states.** An implemented screen must declare `loading`, `populated`, `error` and `forbidden` in `screens[].states`, plus `success` when the screen has `actions` (`screen-state-missing`, an error at every profile at release). This is the one definition; other documents say "the required states, as verification.md states them". A screen with no `dataSources` or no `states` yet is `screen-incomplete`: a warning while the architect and the ux-reviewer are still filling it in, an error at release.

Those are the floor the validator checks, not the whole job. For every data surface also cover the applicable states the floor does not name: empty, filtered-empty, conflict, stale/partial, degraded and validation, and declare each one that applies.

Each state requires a fixture that actually produces it. The required fixture dimensions, how to generate them, and which fixture proves which state are in [test-data.md](test-data.md). A state you cannot reach with seeded data is untested, whatever the manifest says.

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

Extend the list from the platform's own risks. Testing method references: OWASP Web Security Testing Guide (https://owasp.org/www-project-web-security-testing-guide/) and OWASP ASVS (https://owasp.org/projects/ASVS/). High-risk or regulated platforms need qualified security review beyond this skill.

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
python <skill-dir>/scripts/admin_console_manifest.py emit --manifest <project-root>/.admin-console/manifest.json --format gap-report --out <project-root>/docs/admin-gap-report.md
```

| Command | Exit 0 | Exit 1 | Exit 2 |
|---|---|---|---|
| `validate --phase release` | No rule at `error` severity for this profile failed. Warnings may still be printed. | At least one gate rule failed at `error` severity. Release is blocked. Fix the finding or change the fact; do not lower the profile to clear it. | The manifest could not be judged: unreadable file, bad arguments, or `--profile` below the manifest's own at release without `--allow-lower-profile`. |
| `coverage` | No structural finding at `error` severity. | An orphan in one direction or the other: a capability with no role or (on a UI surface) no route-anchored screen, a screen with no capability, an entity with no query capability, a lifecycle state no capability's `entityStates` mention, a non-initial state no command reaches, a role no screen or capability uses, an implemented integration without its controls. | As above. `--project-root` is accepted so the two commands can share arguments; `coverage` reads no files and ignores it. |
| `emit --format gap-report` | The report was written, at release severities. | not used | The manifest could not be read or the output path could not be written. The exit code says nothing about the findings; read the report. |

Add `--json` to `validate` and `report` for machine-readable output when a CI job or another agent consumes the result. Every `validate` summary line carries the effective profile, the phase, the skill commit and the rule-set hash (`ruleset`), so a transcript says which rules judged it. `--allow-lower-profile` exists for previewing a release at a lower profile and is never a way to certify one.

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

### Capturing gate output

A gate log is accepted on what it records: the command, and the exit code it returned. A phrase such as "0 errors" corroborates it but is not required, because honest success output is often empty or goes to stderr. `tsc --noEmit` and `eslint` print nothing at all; pip-audit, semgrep and Lighthouse CI summarise on stderr. Capture both streams, and frame the output with the command line and the exit code:

```bash
gate_run() {   # gate_run <gate> <command...>: writes evidence/<gate>.log, returns the command's exit code
  local gate=$1 log rc; shift
  mkdir -p evidence; log="evidence/$gate.log"
  { printf '$ %s\n' "$*"; printf '# started: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"; } > "$log"
  NO_COLOR=1 PYTHONUTF8=1 "$@" >> "$log" 2>&1
  rc=$?; printf '\n# exit code: %d\n' "$rc" >> "$log"; return "$rc"
}
```

Windows PowerShell 5.1 has no redirection that keeps a tool's output intact. `>` writes UTF-16. `Out-File -Encoding utf8` writes a byte-order mark, garbles every non-ASCII character (`✓` becomes `Γ£ô`) and wraps each stderr line in `NativeCommandError` records. Let `cmd.exe` append the raw bytes instead:

```powershell
function Invoke-Gate([string]$Gate, [string]$Command) {
    $null = New-Item -ItemType Directory -Force -Path evidence
    $log = Join-Path (Resolve-Path evidence).Path ($Gate + '.log')
    $utf8 = New-Object System.Text.UTF8Encoding $false
    [IO.File]::WriteAllText($log, '$ ' + $Command + "`n", $utf8)
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $env:ComSpec
    $psi.Arguments = '/d /s /c "' + $Command + ' >> "' + $log + '" 2>&1"'
    $psi.UseShellExecute = $false
    $psi.EnvironmentVariables['NO_COLOR'] = '1'
    $p = [Diagnostics.Process]::Start($psi); $p.WaitForExit()
    [IO.File]::AppendAllText($log, "`n# exit code: $($p.ExitCode)`n", $utf8)
    return $p.ExitCode
}
```

`cmd.exe` interprets the command line, so escape `& | < > ^ %`, or put a complex command in a script. Set `NO_COLOR=1`, never `FORCE_COLOR=0`, which turns colour on in picocolors-based tools such as vite.

Some tools exit 0 when they found problems. Record the flag that makes them fail, and read the log rather than trusting the exit code alone:

| Tool | Exit 0 despite findings unless |
|---|---|
| `semgrep scan` | `--error` |
| `@axe-core/cli` | `--exit` |
| `npm audit --audit-level=<level>` | the level is low enough: findings below it are hidden |
| `pytest` | exit 5 means no test was collected, which is not a pass |
| `k6` | exit 99 means a threshold failed |

## What the validator cannot check

The validator is deterministic and structural. It reads the manifest and the filesystem. It verifies:

- Schema conformance to manifest v2 (types, and keys the schema does not declare) and internal reference integrity
- Placeholder tokens in the scanned implementation fields, with `declaredStatic[]` as the only escape
- Presence of the required links per capability: server operations, policies, audit events, safeguards, tests, evidence
- Existence and non-emptiness of every evidence and test path, that a cited test looks like a test, and that no one file stands in for many claims
- That a cited test mentions the capability id or a server operation (`evidence-token-match`)
- Lifecycle observability and reachability, role usage, and unresolved `assumed` decisions
- Profile-scoped severity for every rule, as listed in [rule-matrix.md](rule-matrix.md)

It cannot verify:

| The validator sees | It cannot tell you |
|---|---|
| A test file exists, is shaped like a test, contains an assertion, and names the capability | Whether it asserts the *right* thing, or would fail if the guard were removed |
| Evidence names counts, routes, or files rather than only claiming success | Whether the run it records actually happened, or happened against this build |
| A screen traces to a capability, an operation, a policy, and a test | Whether the screen renders correctly, whether its controls do anything when pressed, or whether the console hangs together as one coherent product |
| An authorization policy is referenced | Whether the policy defaults to deny, scopes to tenant, or covers the export and API paths |
| `dataBinding` names a repository method | Whether that method returns authoritative data or a constant |
| An audit event is declared | Whether the running command emits it, or whether it is emitted on failure |
| An evidence path exists | Whether it was regenerated in this build or is stale from three changes ago |
| A `not-applicable` status has a rationale | Whether the rationale is true |
| The manifest lists a capability | Whether the code that implements it still exists |

Residual responsibilities stay with the agent:

1. Read the body of each cited test. Confirm it asserts the outcome, not the absence of an exception. The validator refuses a cited test that has neither a test-style file name nor a test-runner call, and one with no assertion once comments are stripped (assertion words are matched as whole words, so `import { assert }` and a comment about adding assertions do not count). It refuses evidence only when the whole file is one short line claiming success. All three are floors, and clearing a floor is not the same as being right.
2. Break the guard locally and confirm the test fails, for at least every `high` and `critical` capability.
3. Follow `dataBinding` to the query and confirm it reaches the declared source of truth.
4. Execute the command and read the audit record it produced, rather than trusting the declaration.
5. Confirm evidence artifacts were produced by this build.
6. Have a second agent, or an explicitly separate pass, review what you implemented. The implementer never sets its own `reviewStatus: reviewed`.

A green `validate` means the structure is sound and nothing is obviously fake. It is a floor, not a proof. Do not report it as one.

## Feedback capture

Verification is where the skill learns. Every friction, wrong instruction, missing pattern, and tooling failure encountered during the passes above is recorded before it is forgotten:

```text
python <skill-dir>/scripts/admin_console_manifest.py add --manifest <project-root>/.admin-console/manifest.json --kind feedback --json '{"id":"fb-001","observation":"the release gate never checked idempotency","category":"gap","scope":"references/verification.md","proposedChange":"state the idempotency rule in the capability table","evidence":["evidence/tests.log"]}'
```

Categories: `gap`, `friction`, `incorrect-guidance`, `new-pattern`, `tooling`. `scope` is required: the one thing the entry would change, as a `references/<file>.md` path, `SKILL.md`, `scripts` or `agents` (`add` refuses an entry without one, and clustering across projects groups by it). `harvest` stamps `promotedHash` on what it collects, so an entry edited afterwards is harvested again.

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

## External review pass (optional)

`scripts/code_review.py` bridges Alibaba Open Code Review (OCR) into the manifest when the `ocr` CLI is available. It is optional and advisory: absent, blocked, or failing, it changes no gate above. Two engines: the **delegate** engine (default) computes the reviewable file set and the rules, and the reviewing agent reviews each file itself and returns findings through `record`; the **ocr** engine has OCR's configured LLM review and persists directly. `diff` serves Build and Repair; `scan` serves Audit and needs the ocr engine, because delegate mode has no full-file scan.

Each finding persists as a `gaps[]` entry with `source: "ocr"` (via `add --kind gap`, one all-or-nothing batch), so it surfaces in `emit --format gap-report` like any other finding. The validator ignores OCR-sourced gaps when deciding whether a lifecycle state has an accepted escape, so review text that mentions a state name cannot silence a lifecycle check. Exit codes: 0 clean or nothing reviewable, 1 findings recorded, 2 usage, IO or validation failure, including a review that failed or could not cover every file. Exit 3 is unused: claim conflicts do not apply to this script.

What this pass guarantees and what it does not: file coverage and line anchoring are deterministic; the findings themselves are judgments, the delegate engine's from the reviewing agent and the endpoint engine's from its configured LLM. They are evidence for the gap report, never a replacement for the adversarial pass, the permission matrix, policy tests, or `validate` and `coverage`. A clean run is a floor, not a proof. Setup, environment variables, bundles, coverage accounting and the Windows notes are in [../INTEGRATIONS.md](../INTEGRATIONS.md).
