# Multi-agent operation

## Contents

1. Coordination model
2. Roles
3. The independence rule
4. Claiming work
5. Manifest concurrency
6. Handoff records
7. Fan-out patterns
8. Conflict resolution
9. Single-agent degradation

## Coordination model

Coordinate through files. Never through chat memory, a shared context window, or an assumption about what another agent already did.

Three artifacts carry all coordination state:

| Artifact | Path | Written by |
|---|---|---|
| Manifest | `<project-root>/.admin-console/manifest.json` | the script's `add`, `set`, `claim`, `release-claim` subcommands only |
| Claim locks | held by the script next to the manifest | `claim` and `release-claim` only, never by hand |
| Worklogs | `<project-root>/.admin-console/worklog/<agentId>.md` | the owning agent, using the file tools in your harness |

Invocation form used throughout. Use `py -3` or `python3` if `python` is not on PATH:

```text
python <skill-dir>/scripts/admin_console_manifest.py <command> ...
```

Every agent does the same three things before touching code:

1. Read the manifest. It is the current truth, not your recollection of it.
2. Read the worklog of every agent whose `agents[].status` is `handed-off`.
3. Claim what you intend to work on, then work only inside that claim.

Register yourself first:

```text
python <skill-dir>/scripts/admin_console_manifest.py add \
  --manifest <project-root>/.admin-console/manifest.json \
  --kind agent \
  --json '{"id":"impl-billing","role":"implementer","ownsCapabilities":[],"ownsScreens":[],"status":"active","notes":".admin-console/worklog/impl-billing.md"}'
```

Quote the `--json` value so your shell passes it as a single argument.

## Roles

Five roles. Each is a distinct pass, whether five agents run them or one agent runs them in sequence.

| Role | Owns | Must not |
|---|---|---|
| `architect` | discovery, manifest modeling, capability derivation, IA, build order | write feature code |
| `implementer` | vertical slices: data, service, policy, UI, audit, tests | mark its own work verified |
| `ux-reviewer` | IA, tables, forms, states, accessibility, responsiveness, copy | change server logic |
| `qa` | adversarial audit, permission matrix, negative tests, browser evidence, gap filing | fix its own findings silently |
| `security` | threat model, authz matrix, audit completeness, data exposure | approve its own exceptions |

### architect

- **Entry:** the repository is readable and discovery has named entities, roles, and lifecycles. See [discovery.md](discovery.md).
- **Exit:** every entity carries capabilities at `status: discovered` or better; every capability has `outcome`, `kind`, `roles`, `risk`, `entityStates`; build order is recorded in `decisions[]`; `validate --phase plan` exits 0.
- **Writes:** `platform`, `roles[]`, `entities[]`, capability skeletons, `screens[]`, `workQueues[]`, `integrations[]`, `crossCutting`, `qualityGates[]` ids and thresholds, `decisions[]`.

### implementer

- **Entry:** the capability exists at `status: planned`, its spine dependencies are `implemented`, and `claim` succeeded.
- **Exit:** `dataBinding`, `serverOperations`, `authorizationPolicies`, `auditEvents`, `safeguards`, `tests`, `evidence` are real and non-placeholder, and every referenced file exists and is non-empty; `status: implemented`; `reviewStatus` left `unreviewed`.
- **Writes:** capability implementation fields, `screens[].status`, `declaredStatic[]`, `decisions[]` for choices made, `gaps[]` it cannot close.

### ux-reviewer

- **Entry:** at least one screen is at `status: implemented` and the application runs.
- **Exit:** every reviewed screen covers the required states, `accessibilityStatus` is set with an evidence path, `responsive` reflects an actual viewport check, findings are filed as `gaps[]`, and no server code changed. See [experience-design.md](experience-design.md).
- **Writes:** `screens[].states`, `screens[].accessibilityStatus`, `screens[].responsive`, `gaps[]`, `feedback[]`, `reviewStatus` on capabilities it did not implement.

### qa

- **Entry:** the target domain's capabilities are `implemented` and the build passes.
- **Exit:** `coverage` has been run and its findings filed; role-matrix and negative tests exist and were executed against the built system; `qualityGates[]` are `passed` or `failed` with evidence paths; `reviewStatus` is `reviewed` or `contested`. See [verification.md](verification.md) and [test-data.md](test-data.md).
- **Writes:** `qualityGates[]`, `gaps[]`, `reviewStatus`, `tests[]` and `evidence[]` entries for tests it wrote, `feedback[]`.

### security

- **Entry:** the authentication, authorization, tenancy, and audit spine is `implemented` and `emit --format authz-matrix` produces output.
- **Exit:** `crossCutting.authentication`, `.authorization`, `.audit`, and `.data` carry evidence; every `risk: high` or `risk: critical` capability has safeguards, audit events, and a negative test; accepted risks are `decisions[]` with `status: confirmed` and a named human approver. See [security-governance.md](security-governance.md).
- **Writes:** `crossCutting.*`, `roles[].separationOfDuties`, critical and high `gaps[]`, accepted-risk `decisions[]`, `reviewStatus` on privileged commands.

The security pass is required at every profile for the authorization matrix and audit completeness. The formal data-exposure review is required at `regulated`, and at `standard` when `platform.regulatedData` is non-empty.

## The independence rule

The agent that implements a capability may not be the agent that sets its `reviewStatus` to `reviewed`.

Self-review fails for four reasons, and none of them are fixed by trying harder:

- You review the intent you held, not the artifact you produced. Your mental model stands in for the code and conceals the difference between them.
- The blind spot that caused the defect is still active. A missing tenant filter is missing because tenancy was not on your mind; re-reading with the same frame does not surface it.
- Recall confirms cheaply. "I handled the empty state" is memory, and memory does not fail the way a file read does.
- Reviewing the diff cannot see what was never written: the absent policy test, the unemitted audit event, the lifecycle state with no query capability.

What makes review honest is a fresh read of the artifact from disk, checked against a list derived from the manifest rather than from the change.

`reviewStatus` semantics:

| Value | Set by |
|---|---|
| `unreviewed` | the implementer, and only this value |
| `reviewed` | an agent id different from the implementer, after re-reading the files the manifest names |
| `contested` | a reviewer that disputes the work; must be paired with a `gaps[]` entry |

An implementer may never move `contested` to `reviewed`. It fixes the gap, sets `status` to `in-progress` and back to `implemented`, and returns `reviewStatus` to `unreviewed`.

## Claiming work

```text
python <skill-dir>/scripts/admin_console_manifest.py claim \
  --manifest <project-root>/.admin-console/manifest.json \
  --agent <agentId> --role <role> --capability <capabilityId> [--capability <capabilityId> ...]
```

The claim takes an exclusive lock, refuses capabilities another agent holds, and records ownership in `agents[].ownsCapabilities`. Claim before reading the code you intend to change, not after writing it.

Release on finish or on stop. Omitting `--capability` releases everything the agent holds:

```text
python <skill-dir>/scripts/admin_console_manifest.py release-claim \
  --manifest <project-root>/.admin-console/manifest.json --agent <agentId> [--capability <capabilityId>]
```

Safe to claim concurrently:

| Concurrent work | Why it is safe |
|---|---|
| Capabilities on different entities with no shared migration in flight | disjoint files, disjoint tables |
| Screens in different navigation domains | disjoint routes and components |
| Per-role authorization suites | additive test files, one role each |
| Integration adapters with distinct credential boundaries | disjoint adapters and secrets |
| `qa` audit of a finished domain while implementation continues elsewhere | one agent reads, the other writes, no shared file |
| `emit` runs to distinct `--out` paths | read-only against the manifest |

Must serialize under a single claim at a time:

| Serialized work | Failure if run in parallel |
|---|---|
| The auth, authorization, tenancy, and audit spine | two policy layers and two audit writers that diverge silently |
| The manifest's `crossCutting` section | one agent's evidence arrays overwrite the other's |
| Schema migrations and the migration sequence | colliding version numbers, an ordering that will not run |
| Shared design-system components: table shell, form primitives, confirm dialog | two variants of the same control, no baseline for UX review |
| The navigation and IA registry | duplicate or orphaned routes |
| The release-phase validation pass | partial state read as final state |

The spine is built to `implemented` by one agent before entity slices fan out. Slices depend on the spine; the spine depends on nothing. See [build-order.md](build-order.md).

## Manifest concurrency

Mutate the manifest only through `add` and `set`:

```text
python <skill-dir>/scripts/admin_console_manifest.py set \
  --manifest <project-root>/.admin-console/manifest.json \
  --path 'entities[user].capabilities[user.suspend].status' --value implemented
```

Never rewrite the manifest as a whole file. A whole-file write serializes the copy you read minutes ago, and every `add` and `set` another agent made since then disappears. Nothing reports the loss: the result is still well-formed, so validation passes and the deleted work reappears only as a missing capability at release. This is the most damaging failure mode in concurrent operation, and it is silent.

The same rule holds in single-agent mode across passes. Your in-context copy of the manifest is stale the moment the script writes to it.

Locking: `add`, `set`, `claim`, and `release-claim` take an exclusive lock for the duration of the write. Do not create, edit, or delete lock files yourself.

Stale lock: if a lock persists after an agent stops, confirm the holder's `agents[].status` is `done` or `handed-off`, or that its process is gone. Then release by agent id:

```text
python <skill-dir>/scripts/admin_console_manifest.py release-claim \
  --manifest <project-root>/.admin-console/manifest.json --agent <deadAgentId>
```

Do not clear a lock while another agent may be mid-write.

Claim conflict returns exit code 3. The capability is held. Then:

1. Re-read the manifest and identify the holder from `agents[]`.
2. Claim a different unclaimed capability and continue.
3. If nothing else is claimable, file a `gaps[]` entry naming the contended capability and its holder, set your `agents[].status` to `handed-off`, write the worklog, and stop.

Do not retry in a loop, do not proceed without the claim, and do not edit files under another agent's claim.

## Handoff records

Write a handoff before you stop, whether you finished, were blocked, or ran out of budget. The next agent receives the manifest and your worklog and nothing else. Assume it has no conversation history and cannot ask you a question.

Before stopping:

1. `set` the true `status` and `reviewStatus` of every capability you touched. Half-done is `in-progress`, not `implemented`.
2. `add --kind decision` for every non-obvious choice, with `status: assumed` when unconfirmed.
3. `add --kind gap` for every defect you know about, including defects you caused.
4. `add --kind feedback` for friction that belongs to the skill rather than to the project, per [skill-evolution.md](skill-evolution.md).
5. `release-claim` everything you are not deliberately still holding.
6. `set` your `agents[<id>].status` to `done` or `handed-off`.
7. Write the worklog.

Worklog format, at `<project-root>/.admin-console/worklog/<agentId>.md`:

```text
# worklog <agentId>
role: architect|implementer|ux-reviewer|qa|security
mode: build|extend|audit|repair    profile: internal|standard|regulated
started: <ISO-8601>    ended: <ISO-8601>

claims-held: <capabilityId>, ...        # still locked, each with a reason
claims-released: <capabilityId>, ...

did
- <capabilityId>  <status old -> new>  <what changed>  <files touched>
verified
- <capabilityId>  <command run>  <evidence path>  pass|fail
decided
- <decisionId>  <one line>  assumed|confirmed
found
- <gapId>  critical|high|medium|low  <one line>
next
- <the single first action the following agent should take>
blocked-on
- <external dependency, credential, or human decision, or "none">
```

Keep it to one page. It is a pointer into the manifest, not a copy of it. When the worklog and the manifest disagree, the manifest wins.

## Fan-out patterns

| Works | Shape |
|---|---|
| Per-entity slices | one implementer per entity, spine already `implemented`, no shared migration in flight |
| Per-role authorization suites | one agent per role, each writing that role's permitted and forbidden matrix tests |
| Rolling adversarial QA | `qa` audits finished domains while implementers continue on unfinished ones |
| Parallel review | `ux-reviewer` and `security` over the same finished domain; they write different manifest sections |
| Emit and fix | one agent emits `authz-matrix` and `gap-report`, others fix the named items under their own claims |

| Fails | Why |
|---|---|
| Two implementers on one entity | overlapping service, policy, and screen files; last write wins |
| Parallel schema changes | migration numbering and ordering collide; neither branch runs |
| Two agents editing one screen | component-level conflicts that tests do not catch |
| Concurrent `crossCutting` edits | evidence arrays overwritten with no conflict signal |
| An implementer and its reviewer at the same time | the reviewer reads a moving artifact and reviews nothing |
| Fan-out before the spine exists | every slice invents its own auth, tenancy, and audit handling |

Shape of a healthy run: serialize the spine, fan out on slices, converge for review, serialize the release gate.

## Conflict resolution

**Divergent decisions.** Both entries stay in `decisions[]`. Do not delete or edit another agent's decision. Add yours, then have the tie-breaker `set` the losing entry's `status` to `superseded` and name the replacement in the winner's `reason`.

**Contested reviewStatus.** The reviewer sets `contested` and files a paired `gaps[]` entry with a reproduction or a file reference. Only an agent other than the implementer may return it to `reviewed`.

**Tie-breakers**, in this order:

| Dispute | Decided by |
|---|---|
| Model, scope, whether a capability exists, build order | `architect` |
| Authorization, audit, tenancy, sensitive-data exposure, risk classification | `security`, overriding `architect` |
| Operator workflow, IA, screen states, copy | `ux-reviewer` |
| Whether evidence is sufficient | `qa` |
| Pricing, legal interpretation, irreversible production action, an ambiguous business rule with materially different outcomes | the human. File a `gaps[]` entry with `status: blocked` and stop |

Never resolve a conflict by rewriting another agent's manifest entry. Resolution is additive: a new decision that supersedes, or a gap that records the disagreement.

If two agents have already edited the same files, the later agent re-reads both versions from disk and reconciles in a single pass under its own claim. Do not reconcile from memory.

## Single-agent degradation

One agent runs all five roles in sequence. The separation is the value; compressing the passes produces one pass wearing five labels.

Pass order: `architect`, `implementer`, `ux-reviewer`, `qa`, `security`. Cycle implementer and review per domain rather than implementing everything and reviewing once at the end.

Minimum honest separation. All four are required:

1. **A distinct agent id per pass** — `solo-architect`, `solo-impl-billing`, `solo-ux`, `solo-qa`, `solo-sec`. The manifest then still records who did what, and the independence rule stays machine-checkable.
2. **An explicit role declaration in the worklog** before the pass starts, naming the exit condition you intend to satisfy.
3. **A fresh read of the artifact under review**, from disk, at the start of every review pass. Open the files the manifest names and check them against the manifest's requirements. Do not review from recall, and do not review the diff you just produced.
4. **A `qa` pass that runs against the built system** — the build, the test commands, the running application, the emitted authorization matrix. Never against your notes, and never against a description of what the code does.

Further constraints in single-agent mode:

- Do not set `status: implemented` and `reviewStatus: reviewed` in the same pass.
- A review pass files gaps; it does not fix them. Fixes happen in a later implementer pass under a new claim.
- Re-read the manifest at the start of every pass. Your context copy is stale after any `set`.
- Claim and release exactly as a multi-agent run does. The lock costs nothing and preserves the record.
- If you skip a pass, record why in `decisions[]`. Being a single agent is not a reason; scope and budget may be, and they must be stated.

Persist these rules into the repository so they survive the session: [../assets/agent-contract.template.md](../assets/agent-contract.template.md).
