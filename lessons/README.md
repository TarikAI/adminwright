# Lesson format

## Contents

1. What a lesson is
2. File name and id
3. Frontmatter
4. Body sections
5. Writing the rule
6. Review and promotion
7. Status transitions
8. Rejection
9. Index maintenance

## What a lesson is

A lesson is a proposed change to this skill's guidance, backed by evidence from a real
build. It is not a build log, not a project note, and not a place to record how one stack
behaves.

The loop that produces lessons, the promotion bar, and the rules against drift are in
[../references/skill-evolution.md](../references/skill-evolution.md). Read that before
opening one. This file defines only the format.

Every lesson stays on disk forever, whatever its status. Rejected lessons are the record of
ideas already considered and refused; deleting them lets the same idea return every build.

## File name and id

```text
lessons/NNNN-<slug>.md
```

`NNNN` is zero-padded, allocated by `lesson add` as the highest existing id plus one. Ids are
never reused and never deleted. `<slug>` is the title, lowercased, non-alphanumerics
collapsed to hyphens.

## Frontmatter

Written by `lesson add`:

```yaml
---
id: "0001"
title: Authorize every target row in a bulk action
date: 2026-03-02
category: incorrect-guidance
scope: references/security-governance.md
status: proposed
confidence: observed-once
platforms: []
---
```

| Field | Values | Rule |
|---|---|---|
| `id` | `"NNNN"` | quoted, matches the file name |
| `title` | one line | the rule in imperative form, not the symptom |
| `date` | `YYYY-MM-DD` | the date the lesson was opened, never edited afterwards |
| `category` | `gap` \| `friction` \| `incorrect-guidance` \| `new-pattern` \| `tooling` | same vocabulary as `feedback[].category` in the manifest; may be re-classified at review, with a `Review notes` line |
| `scope` | a reference path, skill-root-relative, e.g. `references/architecture.md` | exactly one file, and it must exist. A rule that needs two files is two lessons or one badly placed rule |
| `status` | `proposed` \| `adopted` \| `rejected` \| `superseded` | see section 7 |
| `confidence` | `observed-once` \| `repeated` \| `confirmed` | see below |
| `platforms` | list of archetypes and stack seams seen | written empty; fill from the manifest's `platform.archetypes` and `platform.stack`. Must be non-empty before adoption |

Confidence:

| Value | Requires |
|---|---|
| `observed-once` | one project |
| `repeated` | two or more distinct codebases, each listed separately under `Evidence` |
| `confirmed` | the human explicitly confirmed the rule is durable; quote it under `Evidence` and set `confirmed-by` |

Optional fields, written at review time, never by `lesson add`:

| Field | When |
|---|---|
| `confirmed-by` | confidence is `confirmed`; who confirmed and where |
| `rejected-reason` | status is `rejected`; one value from section 8 |
| `superseded-by` | status is `superseded`; the surviving lesson id |
| `supersedes` | this lesson absorbed others; their ids |

## Body sections

Exactly these five headings, in this order. `lesson add` fills the first three from its
arguments and creates the last two empty.

| Section | Contains |
|---|---|
| `## Trigger` | the situation in which the rule applies, written so an agent can match it. Conditions, present tense, one or two sentences. Not the story of what went wrong |
| `## Rule` | the guidance as it would read inside the reference: imperative, provider-neutral, scoped. If you cannot write it as a rule, the lesson is not ready |
| `## Evidence` | one line per distinct project, each naming artifacts by path: tests, `gaps[]` ids, manifest entries, diffs. External citations carry a URL you fetched and the fetch date. No secrets, customer names, hostnames, or credentials. The number of distinct projects here sets `confidence` |
| `## Proposed edit` | the target file, the exact text replaced or trimmed, the replacement, and the net line change against the 400-line budget. Required before `adopted` |
| `## Review notes` | append-only, one dated line per review: date, role, decision, reason. Never rewrite an earlier line |

## Writing the rule

- Imperative, addressed to the agent. No narration, no "we found that".
- Name the scope in the sentence: profile, archetype, or stack seam. See the scoping table
  in [../references/skill-evolution.md](../references/skill-evolution.md).
- No harness-specific tool names, no product names, no version pins.
- Short enough to land in a reference without a new subsection: roughly eight lines.
- Says what to do and what proves it was done. A rule with no observable evidence cannot be
  gated and will not be enforced.

## Review and promotion

1. `lesson list --status proposed` and read each lesson in full.
2. Check the promotion bar. If no route applies, leave it `proposed` and append a review
   note saying what is missing.
3. Apply the "was it the skill or was it me?" test. Any failure closes the lesson
   `rejected`.
4. Fill `Proposed edit`: the exact replaced text, the replacement, the net line change.
   Confirm the target reference stays under 400 lines.
5. Run the regression check: would this rule have blocked a build that shipped correctly?
   If yes, scope it or reject it.
6. Edit the one reference named in `scope`. One lesson, one file, one edit.
7. Set `status: adopted`, append a dated review note, and update the row in
   [index.md](index.md) in the same edit.
8. Close the originating manifest entry with
   `set --manifest <path> --path 'feedback[FB-003].status' --value promoted`.

The agent that opened a lesson does not adopt it. In single-agent operation, adoption is a
separate declared pass with the reference file re-read rather than recalled.

## Status transitions

| From | To | Condition |
|---|---|---|
| `proposed` | `adopted` | a promotion route applies, `Proposed edit` is filled and applied, `platforms` is non-empty, reviewer is not the author |
| `proposed` | `rejected` | the bar or the test fails; `rejected-reason` set |
| `proposed` | `superseded` | merged into another lesson; `superseded-by` set |
| `adopted` | `superseded` | a later lesson replaces the rule; body collapsed to a stub naming the survivor |
| `rejected` | `proposed` | only with new evidence from a distinct codebase; append a review note stating what changed |

No other transition is valid. An `adopted` lesson is never reverted to `proposed`; correct
it with a new lesson that supersedes it, so the history of the guidance stays readable.

## Rejection

Rejected lessons are kept. Record the reason from this vocabulary:

| `rejected-reason` | Means |
|---|---|
| `already-covered` | the guidance exists; the failure was discoverability |
| `stack-specific` | true only for one stack or version; belongs in the project's agent contract file |
| `style-preference` | a team or personal preference |
| `unverified-cause` | the causal link to the outcome was never shown |
| `agent-error` | the run failed through the agent's own mistake, not missing guidance |
| `not-reproduced` | no second sighting after two further builds |
| `out-of-scope` | not about building or auditing an admin control plane |
| `provider-specific` | names a harness's tools or model and cannot be rewritten neutrally |

Before opening a lesson, read the `rejected` entries. If the idea matches one, either add new
evidence to that lesson or drop it. Never open a second file for the same idea.

## Index maintenance

Every status, confidence, or scope change updates the lesson's row in [index.md](index.md) in
the same edit. The index carries two header lines that the consolidation pass maintains:
`Last consolidation` and `Guidance level`, the highest adopted lesson id.

Lesson `0001` is the worked example for this format. Its rule is real guidance; its project
identifiers and file paths under `Evidence` are illustrative.
