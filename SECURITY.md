# Security policy

## Reporting a vulnerability

Report privately through GitHub's [security advisory form](https://github.com/TarikAI/adminwright/security/advisories/new).
Please do not open a public issue for anything in the first category below.

You can expect an acknowledgement within a few days, and an assessment of whether the report
is accepted along with a fix timeline.

## What counts as a vulnerability here

Adminwright is a skill and a validator, not a running service, so the threat model is narrow.
Two things genuinely matter:

**1. A validator bypass.** Any way to make a manifest backed by mock data, fabricated
evidence, or disconnected controls pass `validate --phase release` cleanly. This is the
highest-severity class of bug in the project. The tool's entire value is that a green result
means something; a bypass makes it worse than nothing, because it manufactures false
confidence.

Past examples, all now closed and covered by tests: Unicode homoglyphs in identifiers, tokens
concatenated without a case boundary, placeholder strings hidden inside nested arrays,
evidence pointing at a directory or a whitespace-only file, evidence paths traversing outside
the project root, and evidence files whose contents said "TODO: replace with real results".

**2. Code execution or unexpected writes.** The script takes a manifest path and JSON
arguments. Anything that makes it write outside the project directory, read a file it was not
pointed at, or execute input would be a real vulnerability.

## What does not count

- Guidance you disagree with. Open an issue; see [CONTRIBUTING.md](CONTRIBUTING.md).
- An admin console built *using* this skill having a vulnerability. The skill raises the floor;
  it does not audit your code. High-risk and regulated platforms need qualified human security
  review, as [references/security-governance.md](references/security-governance.md) states.
- A crash on malformed input, unless it writes something or leaks a path. It should exit 2 with
  a clean message — file it as a normal bug.

## Supported versions

The `main` branch is the supported version. Fixes land there.
