---
id: "0001"
title: Authorize every target row in a bulk action
date: 2026-03-02
category: incorrect-guidance
scope: references/security-governance.md
status: adopted
confidence: repeated
platforms: [b2b-saas-multi-tenant, marketplace, next.js+postgres+prisma, laravel+mysql+queues]
---

## Trigger

A screen offers a multi-select or filter-driven command — suspend, refund, hold, reassign,
delete, export — and at least one role that can reach it is scoped rather than global
(tenant, region, team, account portfolio, assigned queue).

## Rule

Authorize bulk commands per target, not per request.

- Resolve the target set server-side. Accept explicit identifiers, or re-run the filter under
  the actor's scope. Never trust a client-supplied filter, count, or "select all matching".
- Evaluate the same object-level policy for every target that the single-record command uses.
  Partition the set into permitted and denied before executing anything.
- Execute only the permitted partition. Return the denied count and reason to the operator;
  a silent drop teaches the operator the wrong mental model of their own scope.
- Treat each target as an independently recoverable unit. A failure on one target must not
  roll back or conceal the others, and re-running the batch under the same idempotency key
  must not apply it twice.
- Write one audit event per target, each carrying the shared batch identifier, plus one event
  for the batch request itself.
- The authorization matrix test must cover a scoped role whose filter results span its scope
  boundary, not only a role denied the action outright.

## Evidence

- Project A, b2b-saas multi-tenant, next.js + postgres + prisma. Regional support role
  suspended seven accounts outside its region through the bulk action on the accounts list;
  the policy was evaluated once, against the first row of the batch. Manifest gap `G-014`;
  failing-then-passing test `tests/authz/bulk-suspend.scope.spec.ts`; audit assertion
  `tests/audit/bulk-suspend.events.spec.ts`. Cost: one release blocked, one data-repair
  script, one customer notification.
- Project B, marketplace, laravel + mysql + queues. Same shape on a bulk payout hold: the
  authorization call was made against the collection, not its members. Manifest gap `G-031`;
  test `tests/Feature/Admin/BulkPayoutHoldScopeTest.php`. Different stack, different
  archetype, identical defect.
- OWASP, Insecure Direct Object Reference Prevention Cheat Sheet: access control must be
  checked for each object a user attempts to access, and identifier design is not a
  substitute for that check.
  https://cheatsheetseries.owasp.org/cheatsheets/Insecure_Direct_Object_Reference_Prevention_Cheat_Sheet.html
  (fetched 2026-08-06).

Both defects passed the existing single-record authorization tests. Neither was caught by the
UI, because the list query was correctly scoped — only the command was not.

## Proposed edit

Target: `references/security-governance.md`, Authorization section.

Replace:

```text
- Validate both action permission and object/tenant scope on every request.
```

With:

```text
- Validate both action permission and object/tenant scope on every request, and separately
  for every object that request touches. A bulk command authorizes each target
  independently and executes only the permitted partition.
```

Add one row to the safeguard table in the Privileged actions section:

```text
| Per-target authorization with denied-count reported | Any multi-select or filter-driven command |
```

Trims: none required. The existing single-record wording stays correct; it was incomplete,
not wrong. Net +3 lines, file 177 -> 195, budget 400.

Not scoped to a profile, archetype, or stack. Scoped roles exist at every profile, and both
sightings were on different archetypes and stacks. Scoping this rule to `regulated` would
have left the two observed defects unguarded.

## Review notes

- 2026-03-02 architect (project A): opened as `gap`, `observed-once`, after the release-gate
  authorization matrix test caught seven out-of-scope suspensions. Held: one project.
- 2026-04-18 qa: applied the "was it the skill or was it me?" test. Q1 pass, the reference was
  read before implementation. Q2 pass, no existing rule covers per-target authorization. Q3
  pass, the licensing sentence is quoted above. Q4 pass, the implementer followed the existing
  sentence literally and still produced the defect. Q5 pass, two test paths and a gap id.
  Held, awaiting a second sighting.
- 2026-05-14 qa (project B): second sighting, different codebase, archetype, and stack.
  Re-classified `gap` -> `incorrect-guidance`: the existing wording licensed the defect rather
  than merely omitting the rule, because "every request" reads as satisfied by one check per
  request. Confidence `observed-once` -> `repeated`. Bar met on both the correction and
  repeated-gap routes.
- 2026-05-14 security: reviewed the proposed edit and the scope argument. Regression check
  against two shipped manifests: neither had a bulk command this rule would have blocked
  incorrectly; both had only global-scope roles on their bulk surfaces. Adopted. The project A
  architect who opened this lesson did not adopt it.
- 2026-08-06 consolidation: cited URL re-fetched and resolving. Retained as the worked example
  for this format. The rule is real guidance; the project identifiers and file paths under
  `Evidence` are illustrative.
