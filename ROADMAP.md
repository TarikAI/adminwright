# Roadmap

What is planned, in order. Each item lands with tests and a changelog entry; guidance
changes go through the promotion bar in
[references/skill-evolution.md](references/skill-evolution.md). Issues and pull requests
against any of these are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md).

## 1. Close the remaining audit findings

Three independent adversarial reviews ran before first release; most findings were fixed,
these remain:

- Enforce `platform.researchSources[].appliedTo` and `declaredStatic[].value` /
  `.evidence` — the schema requires them, the validator does not yet check them.
- Record reviewer identity: an optional `reviewedBy` on capabilities, checked against
  `owner` at the `regulated` profile, so "reviewed by someone other than the implementer"
  is verifiable rather than asserted.
- Extend the evidence-token check (test file must mention its subject) from capabilities to
  screens at `regulated`.
- Document the exit-code semantics of refused `add`/`set` writes explicitly.

## 2. Learning v2 — multi-device sync and opt-in community contribution

- `store init|sync|status`: the cross-project observation store (`~/.adminwright`) becomes
  a git repository the user can wire to their own private remote, giving multi-device sync
  with no server and no new dependencies beyond git itself.
- `promote --export`: a sanitized bundle of promotion candidates — project names replaced
  by fingerprints, free text scrubbed of emails, hosts, and paths — suitable for sharing.
- An opt-in community lane: contributed bundles live under `community/observations/` in
  this repository, added by pull request so review is the trust gate.
  `promote --include-community` lets community evidence corroborate a local observation
  across the two-project promotion bar. Community data never adopts guidance by itself.
- A `PRIVACY.md` stating exactly what the store holds and what leaves the machine: nothing,
  unless you export and open a pull request.
- A weekly scheduled workflow that surfaces current promotion candidates in a standing
  issue. Adoption remains a deliberate, reviewed edit — never automatic.

## 3. Eval suite

Fixture mini-projects with golden manifests and expected findings, run in CI at every
profile. This is the regression floor for the learning loop: a guidance or validator change
that flips a golden fixture must justify itself or be scoped. Self-improvement without a
fitness function is drift.

## 4. Field test and case study

Run the skill end-to-end on a real platform, capture friction as `feedback[]` in the
moment, hold the first genuine promotion session, and publish the build as a sanitized case
study. The first real lessons should come from a real build, not from review.

## 5. Release and distribution

Tag the release, add repository topics, submit to the community skill lists, enable
Discussions.

## Later, demand permitting

- Compliance mapping: `emit --format control-map` and references mapping manifest fields to
  common control frameworks, for teams using the `regulated` profile ahead of an audit.
- Hosted federation for teams, as a service layered beside the skill. The skill itself
  stays complete and free; nothing in it will be crippled to sell something else.
