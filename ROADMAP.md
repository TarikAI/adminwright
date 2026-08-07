# Roadmap

What is planned, in order. Each item lands with tests and a changelog entry; guidance
changes go through the promotion bar in
[references/skill-evolution.md](references/skill-evolution.md). Issues and pull requests
against any of these are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md).

## Done

Items 1 to 3 below have shipped; see [CHANGELOG.md](CHANGELOG.md) for detail.

- ~~Close the remaining audit findings~~ — schema parity, reviewer identity, screen evidence
  tokens, exit-code semantics.
- ~~Learning v2~~ — `store init|sync|status` for multi-device sync, `promote --export` for
  sanitised sharing, the opt-in community lane, `PRIVACY.md`, and a weekly candidate digest.
- ~~Eval suite~~ — golden fixtures in CI as the regression floor for guidance changes.

## Next: field test and case study

Run the skill end-to-end on a real platform, capture friction as `feedback[]` in the moment,
hold the first genuine promotion session, and publish the build as a sanitised case study.

This is the highest-information step remaining and the only one that cannot be done by review.
Everything so far has been verified against fixtures and adversarial audits; none of it has met
a real codebase's mess. Where an agent following `SKILL.md` is tempted to deviate is exactly
the data the learning loop was built to capture.

## Then: release and distribution

Tag the release, add repository topics, submit to the community skill lists, enable
Discussions.

## Later, demand permitting

- Compliance mapping: `emit --format control-map` and references mapping manifest fields to
  common control frameworks, for teams using the `regulated` profile ahead of an audit.
- Hosted federation for teams, as a service layered beside the skill. The skill itself stays
  complete and free; nothing in it will be crippled to sell something else.
