# Multi-agent operation

## Contents

Part 1, the protocol. Every agent needs these sections; load them by name.

1. Coordination model
2. Roles
3. The review state machine
4. Claiming work
5. Handoff and return

Part 2, orchestration. The session that dispatches agents needs these.

6. Dispatch and verify
7. Fan-out patterns
8. The contest loop and conflict resolution
9. When a run stops
10. Rationalizations
11. Single-agent degradation

Commands are abbreviated to their subcommand; the full forms, shell quoting and lock details are in [manifest-cli.md](manifest-cli.md).

## Coordination model

Coordinate through files. Never through chat memory, a shared context window, a harness's messaging (`SendMessage`, team mailboxes), or an assumption about what another agent already did.

| Artifact | Path | Written by |
|---|---|---|
| Manifest | `<project-root>/.admin-console/manifest.json` | the script only (`add`, `set`, `remove`, `rename`, `claim`, `release-claim`, `harvest`, `migrate --write`); every write stamps `validatedWith` |
| Locks | `.admin-console/.lock` and `.admin-console/locks/` | the script only, never by hand |
| Worklogs | `.admin-console/worklog/<agentId>.md` | the owning agent, with the harness's file tools |

Before touching code, every agent:

1. reads the manifest, which is the current truth, not your recollection of it;
2. reads the worklog of every agent whose `agents[].status` is `handed-off` (a reviewer forms its own findings first, then reads the implementers' worklogs and checks their `verified` claims);
3. claims what it will work on, then works only inside that claim.

Never rewrite the manifest as a whole file. A whole-file write serializes the copy you read minutes ago, and every `add` and `set` another agent made since then disappears without an error. The same holds for one agent across passes: your in-context copy is stale the moment the script writes.

## Roles

Five claimable roles, in pass order: `architect`, `implementer`, `ux-reviewer`, `security`, `qa`. Each is a distinct pass, whether five agents run them or one agent runs them in sequence. `qa` runs last because it owns the release gate.

The `harvester` is a sixth pass, not a role. It never registers in `agents[]` and never claims a capability (`claim --role harvester` is refused, and `add --kind agent` rejects the role). It runs after `qa` and writes only `feedback[]`, the cross-project store and, in a checkout of the skill, lessons. See [skill-evolution.md](skill-evolution.md).

| Role | Owns | Must not |
|---|---|---|
| `architect` | discovery, modeling, capability derivation, IA, build order | write feature code |
| `implementer` | vertical slices: data, service, policy, UI, audit, tests | mark its own work reviewed |
| `ux-reviewer` | IA, tables, forms, states, accessibility, responsiveness, copy | change product code |
| `security` | threat model, authz matrix, audit completeness, data exposure | accept a risk itself |
| `qa` | adversarial audit, permission matrix, negative tests, browser evidence, `reviewStatus`, the release gate | fix its own findings |

### architect

- **Entry:** the repository is readable. See [discovery.md](discovery.md).
- **Exit:**
  - every entity carries capabilities at `status: discovered` or better;
  - every capability has `outcome`, `kind`, `roles`, `risk`, `entityStates`;
  - the capabilities the build order schedules are `planned` (nobody else moves them there);
  - the build order, with its file-ownership table, is a `decisions[]` entry;
  - `validate --phase plan` exits 0.
- **Writes:**
  - `platform`, `roles[]`, `entities[]`, capability skeletons, `screens[]`, `workQueues[]`, `integrations[]`, `qualityGates[]` ids and thresholds, `decisions[]`;
  - the empty `crossCutting` structure, whose sections belong to other roles.
- **First pass:** it claims nothing, because nothing exists yet and it runs alone. In extend or repair mode it claims the existing capabilities it changes.
- **File ownership:** the build-order decision lists each fan-out slice's owned directories and files. Shared items are marked as serialized: the migrations directory, lockfiles, design-system primitives, the navigation registry, `crossCutting`. There is one row for each pair of slices that touch the same file.

### implementer

- **Entry:** the capability is `planned`, its spine dependencies are `implemented`, and `claim` succeeded.
- **Exit:**
  - `dataBinding`, `serverOperations`, `authorizationPolicies`, `auditEvents`, `safeguards`, `tests` and `evidence` are real;
  - every referenced file exists and is non-empty;
  - `status: implemented`, with `reviewStatus` left `unreviewed`.
- **Writes:** capability implementation fields, `screens[].status`, `declaredStatic[]`, `decisions[]` for choices made, `gaps[]` it cannot close. It changes only the paths its slice owns; for anything else it files a gap and returns `NEEDS_CONTEXT`.

### ux-reviewer

- **Entry:**
  - at least one screen is `implemented` and the application runs;
  - an operator account exists for each role;
  - a browser tool is available.

  Without all three, file a `blocked` gap and return `BLOCKED`: a review from source is not a UX review.
- **Exit:**
  - reviewed screens declare their states per [verification.md](verification.md#state-and-data-coverage);
  - `accessibilityStatus` and `responsive` reflect real checks;
  - `crossCutting.experience.evidence` names what was exercised;
  - findings are filed as gaps;
  - `review-scope` is clean.
- **Writes:** `screens[].states`, `screens[].accessibilityStatus`, `screens[].responsive`, `crossCutting.experience` (with `set --append`), `qualityGates[accessibility]`, `gaps[]`, `feedback[]`. See [experience-design.md](experience-design.md).

### security

- **Entry:** the authentication, authorization, tenancy and audit spine is `implemented`, and `emit --format authz-matrix` produces output.
- **Exit:**
  - `crossCutting.authentication`, `.authorization`, `.audit` and `.safety` carry evidence;
  - every `high` or `critical` capability has safeguards, audit events and a negative test;
  - every risk that needs acceptance is proposed, not accepted;
  - `review-scope` is clean.
- **Writes:** those four sections, `roles[].separationOfDuties`, `gaps[]`, and proposed risk acceptances. A proposed acceptance is a `decisions[]` entry with `status: assumed` (naming the risk, the mitigations and the approving role) plus a `blocked` gap asking for a human decision. A dispatched agent cannot ask the human, so only the main session, having asked, sets a decision to `confirmed`. See [security-governance.md](security-governance.md).

### qa

- **Entry:** the target domain's capabilities are `implemented` and the build passes. For the release gate, the `ux-reviewer` and `security` passes are finished.
- **Exit:**
  - `coverage` has been run and its findings filed;
  - role-matrix and negative tests exist and were run against the built system;
  - every negative test on a high or critical capability was shown failing with its guard disabled and passing with it in place;
  - `qualityGates[]` are `passed` or `failed` with evidence;
  - `reviewStatus` is set;
  - `review-scope` is clean for every review pass since the last gate;
  - for a release, `validate --phase release` and `coverage` exit 0 and the gap report is emitted.
- **Writes:** `qualityGates[]`, `crossCutting.observability` and `.data`, `gaps[]`, `reviewedBy` then `reviewStatus`, and `tests[]` and `evidence[]` entries for new test files it wrote. It adds tests and never edits an existing one; a test that must change is a gap for an implementer. See [verification.md](verification.md) and [test-data.md](test-data.md).

Each `crossCutting` section has one owner: `security` owns `authentication`, `authorization`, `audit` and `safety`; `ux-reviewer` owns `experience`; `qa` owns `observability` and `data`. Add to evidence lists with `set --append`, which takes a union and never drops another entry.

The security pass is required at every profile for the authorization matrix and audit completeness. The formal data-exposure review is required at `regulated`, and at `standard` when `platform.regulatedData` is non-empty.

## The review state machine

The agent that implements a capability may not be the agent that marks it reviewed. Self-review fails for four reasons, and trying harder fixes none of them:

- You review the intent you held, not the artifact you produced.
- The blind spot that caused the defect is still active.
- Recall confirms cheaply: "I handled the empty state" is memory, and memory does not fail the way a file read does.
- A diff cannot show what was never written: the absent policy test, the unemitted audit event.

Honest review is a fresh read of the artifact from disk, checked against a list derived from the manifest rather than from the change.

`reviewStatus` has one writer, `qa`. The other reviewers file findings as gaps.

| Transition | Who | Enforced |
|---|---|---|
| `unreviewed` → `reviewed` | `qa`, with `reviewedBy` set first to an id that never implemented the capability | refused while an open blocking gap targets it (`reviewed-with-blocking-gap`) |
| `unreviewed` or `reviewed` → `contested` | `qa`, after filing the gap | refused without an open gap naming the capability (`contested-needs-gap`) |
| `contested` → `unreviewed` | the implementer, once the gap is `fixed` or disputed with evidence on the gap | – |
| `contested` → `reviewed` | nobody | refused (`review-transition`) |
| gap `fixed` → confirmed or reopened | a fresh agent of the role that filed it (scoped re-review) | – |

A **blocking** finding is critical or high, and either violates a manifest requirement or comes with a reproduction. Anything else is advisory: filed, but it never holds up a review. Calibrate severity by impact:

| Finding | Severity |
|---|---|
| Cross-tenant read or write, authorization bypass, privileged action without a server-side check | critical |
| Unaudited privileged mutation, a missing state on a critical flow, destructive action without recovery | high |
| A missing state elsewhere, an accessibility failure on a secondary screen, a misleading error | medium |
| Copy, polish, layout | low |

When reviewers report the same issue at the same place, the findings merge into one gap and the higher severity wins.

## Claiming work

Claim before reading the code you intend to change, not after writing it. The first claim registers the agent.

- A builder's claim (`implementer`, `architect`) excludes everyone: nothing is reviewed while it is being built.
- Reviewers of different roles may review one capability in parallel; two reviewers of the same role may not.
- An id that has built something cannot claim as a reviewer, so use a fresh id for each review pass.
- The claim stamps `owner` and `implementedBy` for builders only, and `release-claim` never clears `implementedBy`, so reviewer identity stays checkable after handoff.

When a claim conflicts, the command exits 3 and names the holder:

1. Re-read `agents[]` to confirm the holder.
2. Claim a different unclaimed capability and continue.
3. If nothing else is claimable, file a gap naming the contended capability and its holder, set your `agents[].status` to `handed-off`, write the worklog, and stop.

Do not retry in a loop, do not proceed without the claim, and do not edit files under another agent's claim. Release on finish or on stop. `claim --force-steal --reason` exists for an agent that stalled; never use it on one that may still be writing. The kinds of lock, and what frees each, are in [manifest-cli.md](manifest-cli.md#claims-and-locks).

## Handoff and return

The next agent receives the manifest and your worklog and nothing else. Assume it has no conversation history and cannot ask you a question. Before you stop, whether finished, blocked or out of budget:

1. `set` the true `status` of every capability you touched. Half-done is `in-progress`, not `implemented`.
2. `add --kind decision` for every non-obvious choice, with `status: assumed` when unconfirmed.
3. `add --kind gap` for every defect you know about, including ones you caused.
4. `add --kind feedback` for friction that belongs to the skill rather than the project; see [skill-evolution.md](skill-evolution.md).
5. `release-claim` everything you are not deliberately still holding.
6. `set` your `agents[<id>].status` to `done` or `handed-off`.
7. Write the worklog.

```text
# worklog <agentId>
role: architect|implementer|ux-reviewer|security|qa
mode: build|extend|audit|repair    profile: internal|standard|regulated
status: DONE|DONE_WITH_CONCERNS|BLOCKED|NEEDS_CONTEXT
base: <commit at dispatch>    started: <ISO-8601>    ended: <ISO-8601>
touched: <every path you changed>

claims-held: <capabilityId>, ...        # still locked, each with a reason
claims-released: <capabilityId>, ...

did
- <capabilityId>  <status old -> new>  <what changed>
verified
- <capabilityId>  <command run>  exit <code>  <evidence path>
decided
- <decisionId>  <one line>  assumed|confirmed
found
- <gapId>  critical|high|medium|low  blocking|advisory  <one line>
next
- <the single first action the following agent should take>
blocked-on
- <external dependency, credential, or human decision, or "none">
```

Keep it to one page. It points into the manifest and does not copy it; when the two disagree, the manifest wins.

Return to the dispatcher in this shape: a first line `STATUS: DONE`, `DONE_WITH_CONCERNS`, `BLOCKED` or `NEEDS_CONTEXT`, then at most twelve lines with the plain-language outcome first, then the worklog path. Name the gap ids behind a concern or a block, and exactly what context is missing.

## Dispatch and verify

Dispatch each pass with everything it needs, because it sees nothing else:

```text
Agent: adminwright:adminwright-<role> (Claude Code) | adminwright-<role> (elsewhere)
Project root: <absolute path>   Manifest: <path>   Mode: build|extend|audit|repair   Profile: <p>
Agent id: <role>-<slug>, fresh for each pass   Scope: <capability ids or domain> and the paths it owns
Base: <git rev-parse HEAD>   Plan of record: <decision id or file>
Harvester only: a digest of the conversation (user corrections, wrong guidance, skipped phases)
Return: the STATUS line, at most 12 lines, then the worklog path
```

For a reviewer, run `review-scope --agent <id> --start` before dispatching it. When a pass returns, do not relay its report; check it yourself before starting the next pass:

| After | Run |
|---|---|
| architect | `validate --phase plan`, which must exit 0 |
| implementer | the commands in its worklog's `verified` lines; compare the exit codes |
| any reviewer | `review-scope --agent <id>`, which must exit 0 |
| qa, for a release | `validate --phase release` and `coverage`, which must exit 0 |

The orchestrating session never edits product code: its fixes would skip review. A decision only a human can make comes back as an `assumed` decision and a `blocked` gap. Ask the human, then record the answer yourself.

## Fan-out patterns

Serialize the spine, fan out on slices, converge for review, and serialize the release gate. The spine is built to `implemented` by one agent before entity slices fan out. See [build-order.md](build-order.md).

| Safe in parallel | Why |
|---|---|
| Capabilities on different entities with no shared migration in flight | disjoint files and tables |
| Screens in different navigation domains | disjoint routes and components |
| Per-role authorization suites | additive test files, one role each |
| Integration adapters with distinct credential boundaries | disjoint adapters and secrets |
| `ux-reviewer` and `security` over one finished domain | reviewer claims of different roles, different manifest sections |
| `qa` on a finished domain while implementation continues elsewhere | one reads, the other writes |

| Serialize | Failure if run in parallel |
|---|---|
| The auth, authorization, tenancy and audit spine | two policy layers and two audit writers that diverge silently |
| Schema migrations | colliding version numbers, an ordering that will not run |
| Shared design-system components and the navigation registry | two variants of one control; duplicate or orphaned routes |
| An implementer and a reviewer on one capability | the reviewer reads a moving artifact and reviews nothing |
| The release-phase validation pass | partial state read as final |

Do not give adminwright agents their own worktrees (`isolation: worktree`, `/batch`, team worktrees). A worktree branches from the default branch and `locks/` is not committed, so the claims and the manifest split. Do not let an agent dispatch agents: a self-spawned "reviewer" under a new id defeats the independence rule, and nothing in the manifest can see it.

## The contest loop and conflict resolution

1. `qa` files the gap, with a reproduction or a file reference, and contests the capability.
2. The implementer fixes only the paired gaps, logs the covering test with its command and output, marks each gap `fixed`, and returns the capability to `unreviewed`. If it disputes a finding instead, it records the evidence on the gap.
3. A fresh re-review marks each paired gap ADDRESSED or NOT ADDRESSED and checks only the fix for new breakage. "Attempted" is not addressed.

Out-of-scope observations become new advisory gaps and never open a new round. A round ends in a reopened gap, and two rounds end the loop: the gap goes to `blocked` with both positions stated, and the human decides. Nobody adjudicates before that cap, and every ruling is a `decisions[]` entry. A gap becomes `accepted` only on the human's word, recorded as a confirmed decision naming the approver; an agent never accepts a gap it filed or caused.

**Divergent decisions.** Both entries stay. Add yours; the tie-breaker sets the losing entry's `status` to `superseded` and names the replacement in the winner's `reason`.

| Dispute | Decided by |
|---|---|
| Model, scope, whether a capability exists, build order | `architect` |
| Authorization, audit, tenancy, sensitive-data exposure, risk classification | `security`, overriding `architect` |
| Operator workflow, IA, screen states, copy | `ux-reviewer` |
| Whether evidence is sufficient | `qa` |
| Pricing, legal interpretation, irreversible production action, an ambiguous business rule | the human: file a `blocked` gap and stop |

Resolution is additive: a new decision that supersedes, or a gap that records the disagreement, never a rewrite of another agent's entry. If two agents already edited the same files, the later one re-reads both versions from disk and reconciles them under its own claim.

## When a run stops

Stop when any of these holds:

- the release gate passes;
- every open gap is blocked on a human or accepted;
- a fix wave did not reduce the number of open critical and high gaps;
- three fix waves have run on one domain.

In the last two cases, escalate to the human with the open gaps and the list of rulings made.

## Rationalizations

| The thought | What to do instead |
|---|---|
| "I'll fix this one-liner while reviewing." | A reviewer's fix is unreviewed code, and `review-scope` flags it. File the gap. |
| "The implementer's log says the tests pass." | A log is a claim. Run the command and cite your own output. |
| "One more round will converge." | Two rounds is the cap; the third disagreement goes to the human. |
| "It is only style, so I'll contest it." | Contest only a blocking finding; style is an advisory gap. |
| "I'll accept this risk myself." | Propose an `assumed` decision and a `blocked` gap; a human accepts. |
| "The subagent reported success." | Run the pass's check from [Dispatch and verify](#dispatch-and-verify). |

## Single-agent degradation

One agent runs all five roles in sequence. The separation is the value; compressing the passes produces one pass wearing five labels. Pass order is `architect`, `implementer`, `ux-reviewer`, `security`, `qa`, then the harvester pass. Cycle implementation and review per domain rather than reviewing everything once at the end.

Minimum honest separation, all required:

1. **A distinct agent id per pass**, such as `solo-architect`, `solo-impl-billing`, `solo-ux`, `solo-sec`, `solo-qa`, so the manifest still records who did what.
2. **An announced pass switch**, in the conversation and the worklog, naming the exit condition you intend to satisfy.
3. **A fresh read of the artifact under review**, from disk, at the start of every review pass: open the files the manifest names and check them against its requirements, not against recall or the diff you just produced.
4. **A `qa` pass against the built system**: the build, the test commands, the running application, the emitted authorization matrix.
5. **`review-scope --role <role> --agent <id>`** around every review pass (`--start` before it, the check after).

Further constraints:

- Do not set `status: implemented` and `reviewStatus: reviewed` in the same pass.
- A review pass files gaps; fixes happen in a later implementer pass under a new claim.
- Re-read the manifest at the start of every pass.
- Claim and release exactly as a multi-agent run does. The lock costs nothing and preserves the record.
- If you skip a pass, record why in `decisions[]`. Being a single agent is not a reason; scope and budget may be, and they must be stated.

Persist these rules into the repository so they survive the session: [../assets/agent-contract.template.md](../assets/agent-contract.template.md).
