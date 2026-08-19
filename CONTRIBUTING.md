# Contributing to Adminwright

The most valuable contribution is a lesson from a real build. The second most valuable is a
test that closes a hole. Both are documented below.

## The bar for changing guidance

Adminwright is guidance an agent will follow literally on someone else's production system.
The bar for adding to it is deliberately high, and it is written down rather than left to
taste — see [references/skill-evolution.md](references/skill-evolution.md).

A change to `references/` is accepted when at least one holds:

- It corrects guidance that is factually wrong. These are accepted immediately.
- It closes a gap observed on **two or more distinct projects**. One project's quirk belongs
  in that project's `AGENTS.md`, not here.
- It documents a new platform archetype or stack seam encountered more than once.

A change is rejected when it restates guidance already present, encodes a style preference,
or comes from a single run where the real cause was agent error rather than missing guidance.
Rejected proposals are kept in `lessons/` with the reason, so the same idea does not return
every few months.

If you have used the skill across several projects, run `promote` and open an issue with the
output. That is exactly the evidence this bar asks for.

## Writing style

Reference files are read by machines that will follow them literally, and by humans under time
pressure. Match the existing voice:

- Terse, declarative, imperative. No marketing language, no emoji.
- Start with `# Title`, then `## Contents` as a numbered list of the `##` headings.
- Prefer a table for anything comparative.
- Say what to do and what breaks if you don't. Delete anything that is true but not actionable.
- Never name a specific agent harness's tools. This skill runs everywhere.
- 400-line target per reference, 450 hard ceiling. Over target, an addition must be paid for
  by a trim or a merge elsewhere in the file.

Every external URL you cite must resolve, and must say what you claim it says. Confidently
stated falsehoods are the worst defect a skill can carry, because agents act on them.

## Code changes

`scripts/admin_console_manifest.py` decides pass/fail for every build that uses this skill.

- **Standard library only.** No dependencies, ever. It must run wherever Python runs.
- **Windows and POSIX.** No `fcntl`, no `os.fork`, no hardcoded `/` joins.
- **No clock, no randomness.** Dates are passed in via `--date`. Output must be reproducible.
- **Never emit a traceback.** Bad input exits 2 with a clear message.
- Exit codes: `0` clean, `1` findings at error severity, `2` usage or IO failure, `3` claim
  conflict.

### Tests are not optional

```bash
python -m unittest discover -s tests -v
```

Every test in `tests/test_manifest.py` exists because something actually broke. If you fix a
bug, add the test that would have caught it, and name it for the behaviour rather than the
function.

Two properties must hold after any change, and both are covered by tests:

1. **A truthful build passes at every profile.** If honest work cannot pass, teams will
   falsify the manifest and the whole design is worthless.
2. **A manifest built on mocks fails at every profile.** Including `internal`.

If your change makes either false, it is wrong regardless of what else it improves.

### Loosening a check

Adding strictness is easy to justify. Removing it is not. A pull request that weakens a rule
must say which real, legitimate build it was blocking, and why scoping it to a profile is not
the better answer.

## Reporting a bypass

If you find a way to make a mock-backed manifest validate cleanly, that is the highest-value
bug report this project can receive. Open an issue with the exact manifest and command that
reproduces it. See [SECURITY.md](SECURITY.md) for anything you would rather not post publicly.

## Advisory OCR review

Pull requests get a non-blocking code review from
[Alibaba Open Code Review](https://github.com/alibaba/open-code-review) via
[ocr-advisory.yml](.github/workflows/ocr-advisory.yml). It posts findings as PR comments
and never gates a merge; no step in it is a required check. The job is skipped entirely
until the repository secrets exist:

- `OCR_LLM_URL` — an OpenAI-compatible endpoint URL (or an Anthropic one, with the repo
  variable `OCR_USE_ANTHROPIC=true`)
- `OCR_LLM_TOKEN` — the endpoint's auth token
- `OCR_LLM_MODEL` (repository variable, optional) — defaults to `agnes-2.0-flash`

The run uses [assets/adminwright-ocr-rules.json](assets/adminwright-ocr-rules.json), the
same rule file `scripts/code_review.py` applies locally — findings are advisory judgments
with guaranteed file coverage, never a substitute for the test suite or the release gates.

To review locally the same way: install the CLI
(`npm install -g @alibaba-group/open-code-review`), configure it
(`ocr config provider`, `ocr config model`), then run `ocr review` from the repo root.

## Do not add a `version` field to the plugin

`.claude-plugin/plugin.json` and `marketplace.json` deliberately declare no `version`.
Claude Code resolves a git-sourced plugin's version from the commit SHA when the field is
absent, so everything merged to `main` reaches installed users on their next background
refresh. Setting `version` pins the plugin instead: users then receive updates only when
someone remembers to bump it, and a forgotten bump is invisible from this side — every
install silently keeps running an old copy.

Adding the field is a breaking change to distribution, not housekeeping.
`tests/test_plugin_packaging.py` fails if either file grows one.

This is also why `main` is the release channel. CI runs on every push to `main` and on
every pull request, but a push lands before its CI finishes — so merge through a green
pull request rather than pushing straight to `main`.

## Pull requests

- One concern per pull request.
- Say what you changed and what you removed to pay for it.
- Run the tests. CI runs them on Linux, macOS, and Windows across supported Python versions.
- Update `CHANGELOG.md` under `Unreleased`.

You do not need to be a maintainer to be right. If you think a rule in here is wrong, say so
in an issue with the case that breaks it.
