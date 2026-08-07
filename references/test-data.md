# Test data and fixtures

## Contents

1. Why fixtures decide the outcome
2. Required fixture dimensions
3. Deriving the seed plan from the manifest
4. Generating fixtures
5. Seed-data safety
6. Which fixture proves which screen state
7. Which fixture enables which negative test
8. Keeping seed data in sync

## Why fixtures decide the outcome

An admin console that was only ever seen with twelve tidy rows fails on contact with production. The failures are predictable, and every one of them is a fixture problem before it is a code problem:

- Pagination, virtualization, and server-side search were never exercised, because twelve rows fit on one screen. The first real list times out.
- The N+1 query was invisible at twelve rows and takes forty seconds at forty thousand.
- Sort order was stable by accident. With ties in the sort key, rows shuffle between pages and operators act on the wrong record.
- Terminal and exceptional lifecycle states were never rendered, so `charged_back`, `payout_reversed`, and `deletion_pending` show a blank chip, an untranslated key, or a crash.
- Every seeded name was short and Latin. The real table breaks at a 90-character organization name, and the RTL name inverts the row layout.
- Optional fields were always present. The detail page throws on the first record with no billing address.
- Every record was created today. The timezone bug, the relative-time formatter, and the retention filter all pass.
- The bulk action was never tested past one page, so select-all-filtered silently means select-this-page.
- Nobody ever saw the empty state, the filtered-empty state, or the forbidden state, because the data never produced them.

Fixtures are not a test convenience. They are the only way the required screen states and the negative authorization tests in [verification.md](verification.md) can be reached at all. A state you cannot seed is a state you did not test.

Build the fixtures before the screens, not after the bugs.

## Required fixture dimensions

Cover every dimension that applies to the entity. Each row is a fixture requirement, not a suggestion.

| Dimension | Seed | Proves |
|---|---|---|
| Volume: none | Zero rows for the tenant, and zero rows matching a filter | Empty state, filtered-empty state, and that they differ |
| Volume: typical | A working day's worth per queue | Default view, sort, and density are usable |
| Volume: large | At least an order of magnitude above the expected peak page, plus one entity at production cardinality | Pagination, virtualization, server-side search, index coverage, export limits, performance budget |
| Every lifecycle state | One record per state in `entities[].lifecycleStates`, including terminal, failed, reversed, expired, and abandoned states | Every state renders, filters, sorts, and exposes only its legal commands |
| Long text | Values at and past the column limit in every displayed field | Truncation, wrapping, tooltips, and that no layout collapses |
| Localized text | Non-Latin scripts, combining characters, and locale-specific number, date, currency, address, and name formats | Encoding, collation, search, and formatting are locale-aware |
| RTL text | Arabic or Hebrew values in name, note, and address fields | Bidirectional layout, mixed-direction strings, and alignment of identifiers next to translated labels |
| Missing optional fields | Records with every optional field null, and one with only required fields | No crash, no "undefined", and a deliberate empty representation |
| Deep relationship graphs | A record with many children, a chain several levels deep, and a circular or self-referential link if the model permits one | Related-record loading, expansion limits, cascade previews, and recursion guards |
| Long histories | A record with hundreds of audit and event entries spanning years | History pagination, event grouping, and that old entries remain readable |
| Sensitive fields | Records carrying every field class in the sensitivity model, masked and unmasked | Field-level policy, masking by default, reveal-with-audit, and export redaction |
| Redacted records | A record already redacted or anonymized | Redacted rendering, and that no shadow copy survives in history, search, or export |
| Stale versions | A record with a version token older than the stored one | Optimistic-concurrency conflict path and recoverable input |
| Concurrent modification | Two operators' pending edits against one record, and a record mutated between list render and command submission | Conflict detection instead of last-write-wins |
| Failed jobs | Jobs in failed, retrying, and dead-letter states with error payloads | Job visibility, retry command, and that failures are not silent |
| Partially completed jobs | A bulk operation with a mix of succeeded, failed, and skipped items | Per-item result reporting and safe re-run of only the failures |
| Provider mismatches | Local records disagreeing with the provider's state, in both directions, plus one orphan on each side | Reconciliation queue, mismatch display, and the resolution command |
| Archived | Archived records inside and outside the default filter | Archive is excluded by default, findable deliberately, and restorable |
| Soft-deleted | Soft-deleted records still referenced by live records | Deleted rows never leak into lists, search, exports, counts, or foreign-key displays |
| Legally held | A record under legal hold that also matches a deletion or retention rule | Hold blocks deletion, states why, and is audited |
| Restored | A record restored from archive or soft delete | Restore is complete, and history shows both the removal and the restoration |
| Cross-tenant neighbours | A second tenant holding records with adjacent IDs, similar names, and matching search terms | Every negative isolation test in [verification.md](verification.md). Without a neighbour tenant, isolation is untested, not passing |

Sensitivity, tenancy, and lifecycle definitions come from the manifest entities; see [admin-data-model.md](admin-data-model.md).

## Deriving the seed plan from the manifest

Do not invent the fixture list. Derive it, so it stays correct as the model changes.

```text
python <skill-dir>/scripts/admin_console_manifest.py emit --manifest <project-root>/.admin-console/manifest.json --format seed-plan --out <project-root>/.admin-console/seed-plan.md
```

Use `py -3` or `python3` if `python` is not on PATH.

The plan is derived from what the manifest already declares:

| Manifest source | Fixture requirement produced |
|---|---|
| `entities[].lifecycleStates` | One record per state, per tenant scope |
| `entities[].lifecycleTransitions[].from` | A record sitting in every precondition state a command needs |
| `entities[].tenantScoped: true` | A second tenant with colliding names and adjacent identifiers |
| `entities[].sensitivity` | A record carrying each sensitivity class, plus a redacted instance |
| `roles[]` | One operator account per role, including a role with no access to the surface |
| `screens[].states` | A fixture that reaches each declared state |
| `capability.entityStates.from` | A record eligible for the command, and one deliberately ineligible |
| `capability.risk: high\|critical` | A record reserved for the negative and replay tests |
| `integrations[]` | A mismatch on each side of the boundary awaiting reconciliation |
| `workQueues[].sla` | Records inside, near, and past the SLA threshold |

The plan names required fixtures. It does not write them. Implement each one in the project's own fixture mechanism, then record the fixture path against the capability so the mapping in section 6 is checkable.

## Generating fixtures

**Deterministic by default.** Fix the random seed, the base timestamp, and the identifier sequence in one place. Derive every relative date from the base timestamp rather than from `now`. A fixture set that differs between runs produces failures nobody can reproduce and diffs nobody can read. Where a test needs a genuinely random value, generate it in the test and pass it in.

**Factories for volume, static fixtures for the ugly cases.** Factories generate the large sets and the typical sets. The exceptional records — the charged-back order, the legally-held account, the 90-character RTL name, the record with the circular reference — are written explicitly and pinned, because their value is that they never drift.

**Layer, do not accumulate.** A base layer of reference data, dimension overlays that add one condition each, and per-test overrides. One monolithic seeder becomes unmaintainable within weeks and cannot produce the empty state at all.

**Re-runnable.** Seeding must be idempotent or destructive-then-rebuild, never additive-on-top. Prefer transactional rollback per test where the stack supports it.

Common mechanisms; see [stack-adapters.md](stack-adapters.md) for the project's stack and [resource-index.md](resource-index.md) for further documentation:

| Stack | Mechanism | Documentation |
|---|---|---|
| Ruby on Rails | `factory_bot`, plus fixtures for pinned cases | https://github.com/thoughtbot/factory_bot , https://guides.rubyonrails.org/testing.html |
| Django | `factory_boy`, plus fixture loading in test cases | https://factoryboy.readthedocs.io/en/stable/ , https://docs.djangoproject.com/en/stable/topics/testing/tools/ |
| Laravel | Model factories and seeder classes | https://laravel.com/docs/seeding |
| Prisma | The integrated seed command | https://www.prisma.io/docs/orm/prisma-migrate/workflows/seeding |
| Other stacks | Use the stack's canonical fixture or seeding mechanism. Research it and record the source in `platform.researchSources[]` rather than inventing one. | |

**The no-mock rule and fixtures do not conflict.** The prohibition on mock, placeholder, and hard-coded values governs the release path. Fixture files are test-path artifacts and legitimately contain fabricated values. The boundary is enforced by where the path appears: a fixture path must never be named in `entities[].sourceOfTruth`, `capability.dataBinding`, `screens[].dataSources`, or `integrations[].sourceOfTruth`. If one is, the placeholder scan is working correctly and the binding is the defect.

## Seed-data safety

**Never seed from a production dump without an explicit, recorded anonymization step.** An unrecorded production copy in a non-production environment is a critical gap, not a shortcut. When a production-shaped dataset is genuinely required, record a `decisions[]` entry naming the transform, the fields it covers, the approver, and an evidence path to the anonymization script, and treat the script as reviewed code.

Anonymization is not display masking. It must defeat re-identification: direct identifiers, quasi-identifiers that combine into an identity, free-text fields, attachments, historical and audit tables, external provider references, and search indexes. If the transform cannot be shown to do that, generate synthetic data instead.

**Never let seeded credentials, tokens, or real personal data reach a shared environment.**

- Seeded account secrets must be unusable outside the test environment. Do not reuse a value that also exists in a real system.
- No live API keys, provider tokens, webhook secrets, or signing keys in fixtures. Use provider sandbox credentials supplied from the environment, never checked in.
- Use reserved names for contact data: `example.com`, `example.net`, `example.org`, and the `.test`, `.example`, `.invalid`, and `.localhost` top-level domains are reserved for exactly this purpose by RFC 2606 (https://www.rfc-editor.org/rfc/rfc2606.html). Seeded phone numbers, payment instruments, and addresses must be the provider's designated test values.
- Never seed a real person's data, including your own team's.

**Guard the target.** A seeder must refuse to run when the connection target is not the expected test database or environment. Fail closed on an unrecognized target rather than prompting.

**Scope to a test tenant.** Seed into a dedicated tenant with a known identifier prefix, and put the cross-tenant neighbour in a second dedicated tenant. Never use a real tenant as the neighbour. Label seeded rows so they can be identified and purged, and so a row that escapes into a shared environment is traceable.

Seeded data inherits the platform's classification rules. If the fixture carries a sensitivity class, the environment holding it must satisfy the controls for that class in [security-governance.md](security-governance.md).

## Which fixture proves which screen state

Record this mapping per screen. A screen state with no fixture is unverified.

| Screen state | Fixture that produces it |
|---|---|
| Loading | Large-volume fixture plus a throttled or delayed response |
| Empty | Zero-row fixture for the tenant |
| Populated | Typical-volume fixture spanning several lifecycle states |
| Filtered-empty | Typical-volume fixture plus a filter no seeded row matches |
| Error | A fixture the query cannot satisfy, or a forced failure at the data boundary |
| Forbidden | An operator account whose role lacks the scope, against an existing record |
| Conflict | Stale-version fixture submitted against a record modified since load |
| Stale/partial | Provider-mismatch fixture, or a reconciliation timestamp beyond the freshness threshold |
| Degraded | Failed-job and dead-letter fixtures, or an integration marked unavailable |
| Success | An eligible record in a `capability.entityStates.from` state |
| Async in progress | A partially completed bulk job with items still queued |

State definitions and required coverage are in [experience-design.md](experience-design.md).

## Which fixture enables which negative test

Every attack in the adversarial pass needs data behind it. Without the fixture, the test passes vacuously.

| Fixture dimension | Negative test it enables |
|---|---|
| Cross-tenant neighbour with adjacent identifiers | Guessed-identifier access; forbidden must be denied by object scope, not by absence |
| Cross-tenant neighbour with colliding names and search terms | Cross-tenant leakage through search, autocomplete, counts, and aggregates |
| Cross-tenant rows matching a broad filter | Export scope, and bulk select-all-filtered spanning out-of-scope rows |
| Soft-deleted and archived records | Deleted rows leaking into lists, search, exports, counts, and foreign-key displays |
| Sensitive and redacted fields | Field-level policy, masking by default, and redaction in exports and logs |
| Records in ineligible lifecycle states | Command precondition enforcement on the server, not only in the UI |
| High-risk command target reserved for replay | Duplicate submission, reused idempotency key, and replayed webhook producing exactly one effect |
| Operator account with a revoked or changed role | Stale-permission execution after revocation |
| Legally held record matching a deletion rule | Hold blocking destructive commands, with a stated reason and an audit record |
| Long history plus a completed high-risk command | Audit tampering, actor forgery, and append-only enforcement |
| Partially completed bulk job | Out-of-scope rows excluded and reported rather than silently processed |

The attack techniques and pass conditions are in [verification.md](verification.md); role independence is in [multi-agent.md](multi-agent.md).

## Keeping seed data in sync

Seed data rots faster than code. A lifecycle state added in a migration and not added to the fixtures produces a state that no test and no screen ever renders, while the manifest still claims coverage.

- Regenerate the seed plan whenever `entities[]`, `roles[]`, `screens[].states`, or `integrations[]` change, and diff it against the previous plan. Treat a new line in the plan as a required fixture, not a note.
- Add the regeneration and diff to the build so drift fails a check rather than waiting to be noticed.
- When a lifecycle state, role, or sensitivity class is added, the fixture is part of the same change. A state with no fixture cannot satisfy the observability and reachability rules at `standard` and `regulated`.
- When a state is removed, remove its fixture and its screen state together. Orphan fixtures hide dead code.
- Verify the fixtures still produce the states they claim after any schema migration; a migration that backfills a default can quietly collapse several dimensions into one.
- Record fixture gaps found during verification as manifest `feedback[]` entries, and promote recurring ones under [skill-evolution.md](skill-evolution.md).
