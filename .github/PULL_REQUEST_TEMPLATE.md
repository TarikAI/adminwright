## What this changes

<!-- One or two sentences. -->

## Why

<!-- If this changes guidance: which of the promotion criteria does it meet?
     Corrections of wrong guidance are accepted immediately; anything else needs
     to have been observed on two or more distinct projects. -->

## What it replaces or trims

<!-- References have a 400-line target and a 450 ceiling. An addition that only
     appends is rejected regardless of how good the rule is. Say what pays for it. -->

## Checklist

- [ ] `python -m unittest discover -s tests` passes
- [ ] If this fixes a bug, a test that would have caught it is included
- [ ] If this loosens a check, the PR says which legitimate build it was blocking
- [ ] A truthful manifest still passes at every profile
- [ ] A mock-backed manifest still fails at every profile
- [ ] Any new external URL resolves and says what the text claims
- [ ] `CHANGELOG.md` updated under `Unreleased`
