# Agent contract template

Copy the fenced block below into the project as its agent instruction file — `AGENTS.md`,
`CLAUDE.md`, `GEMINI.md`, `.cursorrules`, or whatever the harness reads — and replace every
`<PLACEHOLDER>`. Delete rows that genuinely do not apply, and record why in the manifest's
`decisions[]` rather than deleting silently.

A skill governs a session. This file governs the repository. It is what stops the next agent
from undoing this one's work.

```markdown
# Admin console delivery rules

## Ownership

You own end-to-end completeness for every capability you implement. Do not build only the
page that was named. Infer the supporting capabilities the platform's domain, entities,
roles, workflows, risks, and existing architecture obviously require.

The console under these rules is `<ADMIN ROUTE PREFIX>`, built at profile `<internal |
standard | regulated>`, modeled in `.admin-console/manifest.json`.

## Decide these yourself, document the assumption, and continue

- Loading, empty, filtered-empty, error, forbidden, conflict, stale, and success states
- Field validation, and preserving operator input when a request fails
- Search, filtering, sorting, and pagination once the data volume requires them
- Audit records on privileged reads and every mutation
- Safeguards on destructive actions: preview, confirmation, reason capture, recovery
- Server-side authorization and tenant scoping on every query and command
- Responsive layout and keyboard operability
- Timezone, locale, and currency handling for displayed values

## Ask a human before proceeding

- Pricing, monetization, or anything that changes the business model
- Legal or regulatory interpretation, including retention and erasure obligations
- Destructive data migrations, and anything irreversible in production
- External credentials, and which environment a credential belongs to
- Ambiguous business rules where two readings produce materially different outcomes
- Whether operators are permitted to view a specific class of sensitive data
- Which system is authoritative when two records disagree

## Read before implementing

- `.admin-console/manifest.json` — the current truth about this console
- `<PRODUCT DOC>`, `<DOMAIN DOC>`, `<ARCHITECTURE DOC>`, `<DESIGN DOC>`

If one of these is missing or stale, create or correct it before writing feature code.

Read the authoritative documentation for this stack before implementing against it:
`<STACK>`, `<VERSIONS>`. Record what you consulted in the manifest's
`platform.researchSources[]`. Do not guess a framework's authorization, policy, or job idiom.

## No mock data, no hard-coded values

No mock, placeholder, stub, sample, random, or hard-coded value may reach the release path.
A value that is genuinely static by design must be registered in the manifest's
`declaredStatic[]` with a reason and an approver. That registry is the only exception.

Fixtures, seeds, and story files are legitimate and must stay out of the paths named in
`dataBinding`, `sourceOfTruth`, and `dataSources`.

## Per-entity capability review

For every managed entity, decide explicitly whether operators need: list and search; filters,
sorting, and saved views; detail and history; creation and editing; status transitions;
assignment; bulk operations; import; export; archive, deletion, and restoration; audit
history; and permission or scope restrictions.

Do not add these mechanically. Record in the manifest why each is required, not required, or
deferred. A transition is a business command with preconditions and effects, never a status
dropdown.

## Authorization rules

- Every decision is enforced server-side. Hiding a control is not authorization.
- Default deny. Validate both the action and the object scope on every request.
- Bulk actions authorize per target row, not once for the batch, and report per-item results.
- Exports and search obey the same row and field policy as detail views.
- Every role has negative tests proving forbidden operations are rejected by the server.

## Review independence

The agent that implements a capability does not mark it reviewed. Review is a separate pass
that re-reads the code rather than recalling it.

## Completion gate

Do not report completion until all of these hold:

- `python <SKILL DIR>/scripts/admin_console_manifest.py validate --manifest .admin-console/manifest.json --project-root . --phase release` exits 0
- `python <SKILL DIR>/scripts/admin_console_manifest.py coverage --manifest .admin-console/manifest.json --project-root .` exits 0
- `<BUILD COMMAND>` succeeds
- `<TYPECHECK COMMAND>` succeeds
- `<LINT COMMAND>` succeeds
- `<TEST COMMAND>` succeeds
- `<E2E COMMAND>` passes the critical workflow for every supported role
- No unexplained TODO, placeholder route, or disconnected control remains
- No browser console errors on any admin route
- Every destructive operation has a safeguard and a recovery path
- Every privileged operation emits an audit event
- The adversarial gap audit found no unresolved critical or high omission

## Reporting

Report by operational domain, not page by page. State the profile, what an operator can now
do without engineering involvement, what remains blocked or deferred and why, and the
validation and coverage results.
```

## Filling it in

| Placeholder | Source |
|---|---|
| `<ADMIN ROUTE PREFIX>` | The route group you established in the walking skeleton |
| `<internal / standard / regulated>` | The profile recorded in the manifest |
| `<PRODUCT DOC>` and the rest | Whichever of these the project has; create the missing ones |
| `<STACK>`, `<VERSIONS>` | `platform.stack`, read from lockfiles rather than assumed |
| `<SKILL DIR>` | Where this skill is installed, as an absolute or repo-relative path |
| `<BUILD COMMAND>` and the rest | The project's real commands, verified by running them once |

Do not ship the template with placeholders intact. An instruction file containing
`<TEST COMMAND>` teaches the next agent that the rules here are decorative.
