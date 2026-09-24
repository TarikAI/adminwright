# Roadmap

What is planned, in order. Each item lands with tests and a changelog entry; guidance
changes go through the promotion bar in
[references/skill-evolution.md](references/skill-evolution.md). Issues and pull requests
against any of these are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md).

## Done

Everything below has shipped; see [CHANGELOG.md](CHANGELOG.md) for detail.

- ~~Close the remaining audit findings~~ — schema parity, reviewer identity, screen evidence
  tokens, exit-code semantics.
- ~~Learning v2~~ — `store init|sync|status` for multi-device sync, `promote --export` for
  sanitised sharing, the opt-in community lane, `PRIVACY.md`, and a weekly candidate digest.
- ~~Eval suite~~ — golden fixtures in CI as the regression floor for guidance changes.
- ~~First field tests~~ — two audits and a build across seven projects in August 2026. They
  produced lessons 0002 to 0005 and the `llm-gateway` archetype, and showed that a chat-only
  audit never starts the learning loop (see `SKILL.md` and the changelog).
- ~~Role agents~~ — six dispatchable agents (architect, implementer, ux-reviewer, qa,
  security, harvester) with `scripts/install_agents.py` for any harness.
- ~~Optional integrations~~ — DesignArchitect for UI closure and Open Code Review for
  line-anchored review (`scripts/code_review.py`), both soft: absence blocks nothing.
- ~~The `llm-gateway` archetype~~ — for platforms whose product is managing LLM and
  third-party APIs.
- ~~The 2026-09-23 audit~~ — 289 verified findings across the gates, false blocks, Windows
  and PowerShell, the learning loop, the core schema and the agents; each has a regression
  test, and `gate-matrix` plus `tests/test_rule_coverage.py` keep every rule pinned.
- ~~Round two (2026-09-24)~~ — an independent verification of those fixes, then:
  - an OS-level write lock;
  - the review state machine and `review-scope`;
  - `SKILL.md` inside Claude Code's post-compaction limit;
  - agents with inputs, exit checks and a return contract;
  - Unicode confusables from UTS #39;
  - a verified evidence capture recipe;
  - a test that every documented command parses;
  - core-schema fixes, including a separation-of-duties bypass through a temporary table
    that shadowed `approval_request`.

## Next: field test and case study

Publish one of those field builds as a sanitised case study, and hold the first genuine
promotion session over the store it filled: the two-project bar has been reachable since
clustering was fixed, but no lesson has yet been promoted through it end to end.

The field tests so far were short and mostly audits. What is still missing is one long build
on a real codebase, read closely: where an agent following `SKILL.md` was tempted to deviate
is exactly the data the learning loop was built to capture, and a published case study is
the evidence that the gates certify something a reader can check.

Two recommendations from the round-two research are not built yet:

- **Behavioral evals.** A `claude plugin eval` suite in its own folder (`evals/` holds the
  validator fixtures), run on Sonnet and Opus. It would check that a back-office request
  triggers the skill and a near miss does not, that the implementer claims before it edits,
  that reviewers make no edits, and that the harvester without a digest returns
  `NEEDS_CONTEXT`. This waits on the command being generally available.
- **Plugin hooks for the reviewer write surface.** A `PreToolUse` hook in Claude Code that
  refuses reviewer writes outside their surface, and one on `SubagentStop` that runs each
  role's exit check. It would fail open when Python is missing. `review-scope` already
  covers every harness after the fact.

## Then: release and distribution

Tag the release, add repository topics, submit to the community skill lists, enable
Discussions.

## Later, demand permitting

- Compliance mapping: `emit --format control-map` and references mapping manifest fields to
  common control frameworks, for teams using the `regulated` profile ahead of an audit.
- Hosted federation for teams, as a service layered beside the skill. The skill itself stays
  complete and free; nothing in it will be crippled to sell something else.
