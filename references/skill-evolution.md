# Skill evolution

## Contents

1. The loop
2. Capture an observation
3. Open a lesson
4. The promotion bar
5. Never promote
6. Was it the skill or was it me?
7. Anti-drift rules
8. Consolidation pass
9. Regression safety and scoping
10. Cadence and ownership
11. Versioning the guidance

## The loop

This skill improves from real builds. It degrades if every observation becomes a rule.
The loop below is the only sanctioned path from experience to guidance.

```text
build/audit
-> observation                     something the skill got wrong, omitted, or made expensive
-> manifest feedback[]             add --kind feedback          (status: open)
-> cross-project store             harvest                      (~/.adminwright)
-> promotion candidates            promote                      (bar applied across projects)
-> lessons/NNNN-<slug>.md          lesson add                   (status: proposed)
-> reference file edit             deliberate edit, states what it replaces
-> lessons/index.md adopted        + manifest feedback status: promoted
```

The `harvest` step is what makes the bar in section 4 answerable. "Seen on two or more
distinct projects" cannot be evaluated while each project's copy of the skill only knows
about itself. `harvest` moves a finished project's `feedback[]` into a store outside every
project — `$ADMINWRIGHT_HOME`, or `~/.adminwright` by default — where observations from every
platform you have built accumulate. `promote` then groups observations that say the same
thing in different words and reports only those that clear the bar.

The store is an accumulation buffer, not the record. Adopted lessons live in the skill's own
`lessons/` directory under version control, which is how they reach anyone else.

Commands, and exactly what each writes:

| Stage | Command | Writes |
|---|---|---|
| Record in the project | `add --manifest <path> --kind feedback --json '<obj>'` | one `feedback[]` entry in the project manifest, `status: open` |
| Open a lesson in the skill | `lesson add --title <t> --category <c> --scope <ref> --trigger <s> --rule <s> [--evidence <path> ...]` | `lessons/NNNN-<slug>.md` with `status: proposed`, plus one row in `lessons/index.md` |
| Collect across projects | `harvest --manifest <path> --date <YYYY-MM-DD>` | appends to `observations.jsonl` in the store; sets the harvested entries to `promoted` |
| Apply the bar | `promote [--min-projects 2] [--json]` | nothing; reports candidates and why each qualifies |
| Read the queue | `lesson list --status proposed` | nothing |
| Adopt | edit of the one reference named in `scope` | the guidance change |
| Close the lesson | edit of the lesson frontmatter and its index row to `status: adopted` | the lesson and the index |
| Close the project entry | `set --manifest <path> --path 'feedback[FB-003].status' --value promoted` | the manifest entry |

Invocation form:

```text
python <skill-dir>/scripts/admin_console_manifest.py <command> ...
```

Use `py -3` or `python3` if `python` is not on PATH.

`lesson add` never edits a reference file. Adoption is always a separate, reviewed edit.

`feedback[]` lives in the project manifest and travels with the project. Lessons live in
the skill and travel with the skill. Never carry project-specific detail into a lesson
beyond the evidence needed to judge it, and never carry secrets, customer names, hostnames,
or credentials at all.

## Capture an observation

Capture at the moment it happens, in the pass that hit it. Do not batch to the end of the
build from memory.

An observation must state four things or it is not usable:

1. What you were doing (phase, capability id, entity, screen).
2. What the skill said, or failed to say.
3. What it cost: rework, a defect that reached verification, a wrong build order, a gate
   that could not be answered.
4. Where the proof is: a test path, a `gaps[]` id, a diff, an audit finding.

Categories, shared with `feedback[].category` and lesson frontmatter:

| Category | Means |
|---|---|
| `gap` | the skill was silent and the omission cost work |
| `friction` | the guidance was correct but slow, ambiguous, or ordered badly |
| `incorrect-guidance` | the skill states something factually wrong |
| `new-pattern` | an archetype or stack seam the skill does not model |
| `tooling` | the script, schema, or manifest model blocked correct work |

Most observations stop here. That is the intended outcome. An observation is a record of
what happened; a lesson is a claim about what the skill should say.

## Open a lesson

Open a lesson only after the observation passes the test in section 6.

Before opening, run `lesson list` and read the `rejected` and `adopted` entries. If your
idea matches a rejected lesson, either add new evidence from a distinct project to that
lesson or drop it. Do not open a second file for the same idea.

`lesson add` requires a title, a category, a `scope` (the single reference file the rule
would land in, named by its inventory path), a trigger, and a rule. It writes
`status: proposed`, `confidence: observed-once`, `platforms: []`, and empty
`Proposed edit` and `Review notes` sections. Fill `platforms` from the project manifest's
`platform.archetypes` and `platform.stack` before review. A lesson cannot be adopted with
empty `platforms`, an empty `Proposed edit`, or a `scope` that is not a real reference file.

Field definitions and status transitions are in [../lessons/README.md](../lessons/README.md).

## The promotion bar

A lesson becomes skill guidance through exactly one of four routes.

| Route | Bar | Handling |
|---|---|---|
| Correction | the skill states something factually wrong: a wrong API, a misstated standard, an ordering that cannot work, wording that licenses a defect | promote immediately, ahead of the queue |
| Repeated gap | the skill was silent, the omission cost real rework, and it happened on **two or more distinct projects** | promote |
| Human confirmation | the human explicitly confirmed the rule is durable, not a one-off preference | promote, record who confirmed and quote the confirmation |
| New archetype or seam | a platform archetype or stack seam met on two occasions and not modelled in [capability-catalog.md](capability-catalog.md) or [stack-adapters.md](stack-adapters.md) | promote as a scoped addition |

Distinct means different codebases, not two modules of one product and not two runs against
the same repository.

For the correction route, verify against the authoritative documentation first, cite the URL
you fetched, and record the fetch date. Behaviour observed only in the current stack is a
stack quirk, not a correction.

Everything else stays `proposed`. A lesson still `proposed` after two further builds with no
second sighting is closed `rejected`, reason `not-reproduced`. It stays on disk.

## Never promote

| Do not promote | Reason | Where it belongs |
|---|---|---|
| Single-project stack quirks and version-pinned workarounds | they expire, and they are wrong for the next project's stack | the project's agent contract file, generated from [../assets/agent-contract.template.md](../assets/agent-contract.template.md) |
| Personal or team style preferences | the skill has no basis to judge them, and they conflict across teams | the project's agent contract file |
| Restatements of guidance already present | consumes the line budget and buys nothing; a second phrasing of a rule weakens both | close `rejected`, reason `already-covered` |
| Claims you cannot tie to the outcome | post-hoc rationalization; a rule built on a guess is a rule that misfires | return to observation until causality is shown |
| Findings from a single failed run whose real cause was agent error | writing a rule to prevent your own mistake teaches the next agent nothing and adds a gate that fires on correct work | close `rejected`, reason `agent-error` |
| Anything naming a specific harness's tools or model | breaks provider neutrality; the skill must run on any harness | rewrite neutrally or reject |
| Anything containing secrets, customer identifiers, or private data | lessons ship with the skill | strip to the shape, or reject |

Discoverability failures are a special case. If the rule already exists but you did not find
it, the defect is routing, not content. File `friction` against the load order in
[../SKILL.md](../SKILL.md). Do not add the rule a second time in a second file.

## Was it the skill or was it me?

Apply before every `lesson add`. All five must hold.

1. **Did you read the reference that covers this area before the failure?** If not, this is
   your miss. Read it and close the observation.
2. **Does the guidance already say it in other words?** Search the references for the
   concept, not for your phrasing. If it exists, this is `friction`, not a new rule.
3. **Can you name the exact sentence that was wrong, or the exact decision you could not
   make from the text?** "It should have warned me" is not promotable. Quote the text or
   name the missing decision.
4. **Would a competent agent following the reference literally have reached the same
   outcome?** If the failure needed your specific mistake, misreading, or skipped step to
   trigger, it is agent error.
5. **Can you point to an artifact that proves the cost?** A failing test, a `gaps[]` entry,
   an audit finding, a rework diff. No artifact, no promotion.

Failing any question closes the lesson `rejected` with that reason. Keep the file; it is
what stops the same idea returning every build.

## Anti-drift rules

- **Every adoption states what it replaces, trims, or supersedes.** Write it in the lesson's
  `Proposed edit` before touching the reference. An adoption that only appends is rejected
  by review, regardless of how good the rule is.
- **Line budget per reference: 400 target, 450 ceiling.** Count before and after. Past 400,
  an addition must be paid for by a merge of two rules into one stronger rule, or by a trim
  elsewhere in the file. Past 450, split along a real seam instead. Never a bare append.
  Two references currently sit between the target and the ceiling by deliberate decision:
  `admin-data-model.md` and `experience-design.md` are table-dense and load in one phase each,
  and splitting them would cost more in navigation than it saves in context. Record any
  further exception the same way, or the budget stops meaning anything.
- **A split adds a file, and a file that is not routed does not exist.** Any split updates
  the load list in [../SKILL.md](../SKILL.md) and the file inventory in the same change.
- **One rule, one home.** If a rule fits two references, put it in the one the agent reads
  first for that phase and cross-link from the other.
- **Write the rule, not the story.** Reference text carries a trigger and an imperative. The
  anecdote stays in the lesson file. Delete it from the reference edit.
- **Bounded growth per cycle.** If a consolidation cycle grows a reference by more than
  fifteen lines net, it must include a merge pass on that file in the same change.
- **No new vocabulary without need.** A new status, category, or field name must be added to
  the manifest model and the validator, not only to prose.

## Consolidation pass

Run before any release claim, and at least every fifth build.

1. `lesson list` and read every `proposed` entry.
2. Merge duplicates. Same rule proposed twice becomes one lesson at the higher confidence;
   the others become `superseded` with `superseded-by` pointing at the survivor.
3. Collapse superseded lessons to a stub: keep the id, title, frontmatter, and a single line
   naming the survivor. Delete the body. The id must never be reused or deleted, or the same
   idea returns.
4. Re-fetch every URL cited in the references and in adopted lessons. Replace a dead link
   with the current canonical page, or remove the claim. Never keep a citation you have not
   re-fetched.
5. Verify every reference is reachable from the load list in [../SKILL.md](../SKILL.md), and
   that every relative link inside the references resolves.
6. Re-check line budgets and the provider-neutrality rule across all references.
7. Record the date on the `Last consolidation` line at the top of
   [../lessons/index.md](../lessons/index.md).

## Regression safety and scoping

Before adopting, ask: **would this rule have blocked a build that shipped correctly?**
Check it against at least two past manifests you can still read. If the answer is yes, the
rule is wrong or too broad. Scope it, do not weaken it into advice.

Scope on the narrowest axis that still catches the defect:

| Axis | Values | Use when |
|---|---|---|
| Profile | `internal`, `standard`, `regulated` | the rule's cost is only justified at a higher assurance tier |
| Archetype | an archetype from [capability-catalog.md](capability-catalog.md) | the rule follows from a domain obligation, not from software in general |
| Stack seam | the `platform.stack` field it depends on: auth, jobs, database, adminFramework, designSystem | the rule expresses how a class of framework behaves, per [stack-adapters.md](stack-adapters.md) |

Write the scope into the rule sentence, not into a footnote: "For the `regulated` profile,
..."; "For marketplace and payments archetypes, ...". A rule that cannot name its scope is
either genuinely universal, which is rare, or not ready.

A lesson that raises a gate's severity changes the profile gate matrix in
[verification.md](verification.md) and the validator together. Adopting a gate change in
prose alone produces a rule no build is measured against and audits that disagree with the
tool. Reject docs-only gate changes.

## Cadence and ownership

Roles are defined in [multi-agent.md](multi-agent.md).

| When | Who | What |
|---|---|---|
| End of each capability slice | `implementer` | capture observations into `feedback[]`; do not open lessons |
| End of every build or audit | `qa` | triage `feedback[]`, apply the test in section 6, open the lessons that pass |
| Before any release claim | `qa` | run the consolidation pass; no `feedback[]` entry left `open` and untriaged |
| When the human confirms a rule in conversation | whoever heard it | `lesson add` immediately, confidence `confirmed`, quoting the confirmation |
| Lessons scoped to [security-governance.md](security-governance.md) at `regulated` profile | `security` | review before adoption |
| Single-agent operation | the agent | an explicitly declared final pass that re-reads the references and the `feedback[]` entries, rather than recalling them |

The agent that opened a lesson does not adopt it. This mirrors the rule that an implementer
does not mark its own work reviewed. In single-agent operation, adoption is a separate
declared pass with the reference file re-read, not recalled.

## Versioning the guidance

Guidance changes. A project built under older guidance is not non-compliant because the
skill later learned something.

- The **guidance level** is the highest adopted lesson id, recorded on the
  `Guidance level` line at the top of [../lessons/index.md](../lessons/index.md).
- Every build records the level it was built under as a manifest decision:
  `add --manifest <path> --kind decision --json '{"id":"D-guidance-level","decision":"built under guidance level 0007","reason":"skill version pin for audit","status":"confirmed","appliesTo":["*"]}'`
- When auditing an older console, read that decision first. A finding that exists only
  because of a lesson adopted after that level is reported as a `gaps[]` entry naming the
  lesson id, with severity taken from the rule's real risk. Report it as a delta against
  current guidance, never as a failure of the build's own contract.
- Corrections are the exception. A rule adopted through the correction route applies
  retroactively, because the old guidance was wrong when the project shipped. Say so and
  cite the lesson id.
- After a guidance upgrade, re-run
  `validate --manifest <path> --project-root <root> --phase release`. The validator carries
  structural rules only. A newly adopted prose rule that has no validator check is advisory
  until a `tooling` lesson adds the check.
