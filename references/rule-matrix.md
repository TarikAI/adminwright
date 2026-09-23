# Rule severity matrix

This table is generated from the validator's `RULES`, not written by hand:
`python scripts/admin_console_manifest.py emit --format rule-matrix --out references/rule-matrix.md`
regenerates it, and `tests/test_cli_correctness.py` fails if it drifts from the code.
[verification.md](verification.md) explains what each gate means; this file states exactly
how severe each one is at each profile.

Each cell is the severity at that profile: `error` blocks (exit 1), `warn` is printed and
does not block, `off` is silent. A `*` marks a completeness rule: it describes an
unfinished model rather than a malformed one, so outside `--phase release` an `error` is
reported as a `warn` (an incremental build is not blocked by a role nothing uses yet). Every
finding prints its rule id in brackets, so a line of `validate` output can be looked up here.

| Rule | internal | standard | regulated |
|---|---|---|---|
| `archetype-coverage` | warn | warn | warn |
| `audit-events-missing`* | error | error | error |
| `authorization-policy-tests` | warn | error | error |
| `capability-no-role`* | error | error | error |
| `capability-route-unknown`* | error | error | error |
| `capability-screen-link`* | error | error | error |
| `capability-unreviewed`* | warn | warn | error |
| `contested-needs-gap`* | warn | warn | error |
| `crosscutting-evidence` | warn | error | error |
| `crosscutting-flag` | error | error | error |
| `crosscutting-section-missing` | error | error | error |
| `decision-applies-to-unknown` | error | error | error |
| `decision-assumed` | off | warn | error |
| `decision-reason-missing` | error | error | error |
| `declared-static-forbidden-path` | error | error | error |
| `declared-static-incomplete` | error | error | error |
| `declared-static-self-approved` | off | warn | error |
| `declared-static-unused` | warn | warn | warn |
| `entity-query-missing`* | warn | error | error |
| `enum-invalid` | error | error | error |
| `evidence-file-empty` | warn | error | error |
| `evidence-inside-manifest-dir` | error | error | error |
| `evidence-is-test-file` | warn | error | error |
| `evidence-not-resolvable` | warn | error | error |
| `evidence-path-missing` | error | error | error |
| `evidence-shared` | off | warn | error |
| `evidence-token-match` | off | warn | error |
| `evidence-uninformative` | warn | error | error |
| `feedback-scope` | warn | warn | warn |
| `gap-rationale-missing` | error | error | error |
| `gap-unresolved` | error | error | error |
| `gate-evidence-missing` | error | error | error |
| `gate-evidence-path` | warn | error | error |
| `gate-evidence-unrelated` | off | warn | error |
| `gate-missing` | error | error | error |
| `gate-not-passed` | error | error | error |
| `gate-rationale-missing` | error | error | error |
| `high-risk-controls`* | error | error | error |
| `id-duplicate` | error | error | error |
| `id-invalid` | error | error | error |
| `idempotency-missing`* | error | error | error |
| `implementer-unrecorded`* | warn | warn | error |
| `integration-controls`* | error | error | error |
| `lifecycle-observable`* | warn | error | error |
| `lifecycle-reachable`* | warn | error | error |
| `manifest-key-missing` | error | error | error |
| `manifest-type` | error | error | error |
| `manifest-version` | error | error | error |
| `manual-evidence` | warn | warn | error |
| `nonfunctional-gate-required` | off | error | error |
| `not-applicable-undecided`* | off | warn | error |
| `placeholder-scan` | error | error | error |
| `plan-note` | warn | warn | warn |
| `privileged-read-audit` | off | error | error |
| `profile-invalid` | error | error | error |
| `rationale-required` | error | error | error |
| `release-status-unresolved` | error | error | error |
| `required-field-empty` | error | error | error |
| `research-source-incomplete` | warn | error | error |
| `research-sources-missing`* | warn | error | error |
| `reviewer-identity` | warn | warn | error |
| `role-unused`* | warn | error | error |
| `screen-accessibility` | error | error | error |
| `screen-incomplete`* | error | error | error |
| `screen-no-capability`* | warn | error | error |
| `screen-responsive` | error | error | error |
| `screen-state-missing` | error | error | error |
| `separation-of-duties`* | warn | warn | error |
| `stack-incomplete`* | warn | error | error |
| `test-asserts-nothing` | warn | error | error |
| `test-file-shape` | warn | error | error |
| `test-file-shared` | off | warn | error |
| `transition-command-unknown`* | error | error | error |
| `unknown-capability-reference` | error | error | error |
| `unknown-field` | warn | error | error |
| `unknown-role-reference` | error | error | error |
| `unregistered-static` | error | error | error |
