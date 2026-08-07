# Privacy

Short version: **nothing leaves your machine unless you explicitly export it and choose to
share the result.** There is no telemetry, no analytics, no phone-home, and no network call
of any kind in normal operation.

## What runs locally

Every command — `init`, `validate`, `coverage`, `emit`, `add`, `set`, `claim`, `harvest`,
`promote`, `lesson` — reads and writes files on your machine only. The validator is Python
standard library with no network access.

`store sync` is the single exception, and only when you configure it: it runs `git` against
a remote **you** choose. Point it at a private repository.

## What the observation store holds

`~/.adminwright` (or `$ADMINWRIGHT_HOME`) accumulates observations harvested from your
projects. Each record can contain the observation text you wrote, the project name, its
archetypes and stack, and evidence paths.

Treat it as you would your source code: it quotes your engineering notes. It is private by
default and never transmitted.

## What leaves your machine, and only if you ask

`promote --export <file>` writes a sanitised bundle intended for sharing. Before writing, it:

- replaces emails, URLs, file paths, IP addresses, hostnames, domains, API tokens and long
  hashes with markers
- replaces project names with one-way fingerprints (SHA-256, truncated) so the same project
  is countable across bundles but not identifiable
- coarsens stack strings to family names
- drops evidence paths entirely

**Its limits, stated plainly:** no pattern can detect a company, product, customer or
person's name written as an ordinary word. The exporter prints this warning and the file is
yours to read before you share it. If you contribute without reading it, you are the only
person who could have caught what the patterns missed.

Writing the file shares nothing. Sharing happens only when you open a pull request.

## Contributions

See [community/README.md](community/README.md). Contribution is opt-in, contents are limited
to sanitised bundles, and withdrawal is a pull request that deletes your file.

## Third parties

None. The project has no dependencies, no hosted service, and no accounts.

If a hosted service is ever offered, it will be separate software with its own policy, and
the skill will keep working fully offline. Nothing in this repository will be crippled to
make a paid tier attractive.

## Questions

Open an issue, or use the
[security advisory form](https://github.com/TarikAI/adminwright/security/advisories/new) for
anything you would rather not post publicly.
