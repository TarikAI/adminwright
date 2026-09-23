# Security policy

## Reporting a vulnerability

GitHub's private vulnerability reporting is not enabled for this repository, so there is no
private form to link to. Until it is, open a public issue titled **"Security: private contact
request"** that contains **no details** of the problem (not the manifest, not the input, not
the impact). The maintainers will answer in that issue with a private channel, and the details
go there, not in the issue. A validator bypass that needs nothing but a crafted manifest is
low-risk to discuss openly -- the tool runs locally -- and may be reported with the
[Validator bypass](https://github.com/TarikAI/adminwright/issues/new?template=validator_bypass.yml)
template; use the private route for anything about code execution or writes outside the
project.

(Maintainers: turning on *Settings, Code security, Private vulnerability reporting* lets this
section link straight to GitHub's advisory form; [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) and
[PRIVACY.md](PRIVACY.md) point here, so this is the one place to change.)

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
