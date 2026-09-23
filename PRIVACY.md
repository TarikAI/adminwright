# Privacy

Short version: **the skill's own commands make no network calls and send nothing anywhere.
Data leaves your machine only through a command that is defined to send it: syncing your
private observation store, sharing a sanitised export, or one of the optional integrations
below.** There is no telemetry, no analytics, and no phone-home.

## What runs locally

Every command of `scripts/admin_console_manifest.py` (`init`, `migrate`, `validate`,
`report`, `coverage`, `emit`, `add`, `set`, `claim`, `release-claim`, `harvest`, `promote`,
`store`, `lesson`) and of `scripts/install_agents.py` reads and writes files on your machine
only. The validator is Python standard library with no network access.

`store sync` is the one exception among them, and only when you configure it: it runs `git`
against a remote **you** choose and pushes `observations.jsonl`, the store's `README.md` and
`.gitignore`. Point it at a private repository, because observations quote your engineering
notes. The store's salt, lock and outbox are never pushed.

## Optional integrations

Neither is required, and neither is used unless you invoke it.

- **`scripts/code_review.py`, delegate mode (the default):** sends nothing. It selects files
  and resolves rules locally; the agent that reads the bundle is your harness's model, under
  that harness's own data policy.
- **`scripts/code_review.py`, endpoint mode:** the `ocr` CLI posts the contents of the files
  under review to the LLM provider you configured with `ocr config provider`. Treat that as
  sending those files to that provider.
- **The `ocr` npm launcher** contacts registry.npmjs.org and may run `npm i -g` to update
  itself on every call unless `OCR_NO_UPDATE=1` is set. The bridge sets it in every process it
  starts; if you run `ocr` yourself, set it yourself.
- **`.github/workflows/ocr-advisory.yml`,** if you copy it into your repository, sends pull
  request contents and the `OCR_LLM_TOKEN` secret to the endpoint you configure.

## What the observation store holds

`~/.adminwright` (or `$ADMINWRIGHT_HOME`) accumulates observations harvested from your
projects. Each record can contain the observation text you wrote, the project name, its
archetypes and stack, and evidence paths.

Treat it as you would your source code: it quotes your engineering notes. It is private by
default and never transmitted.

## What leaves your machine, and only if you ask

`promote --export <file>` writes a sanitised bundle intended for sharing. Before writing, it:

- replaces emails, URLs, absolute and relative file paths, IP addresses, hostnames, any
  dotted domain, and cloud, source-host, JWT and API tokens and long hashes with markers
- replaces project names with one-way fingerprints (SHA-256, truncated, salted with a random
  per-store salt created by `store init` and never synced or exported) so the same project
  is countable across bundles but a fingerprint cannot be checked against a list of names
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

None in the skill's own code: no dependencies, no hosted service, no accounts. The optional
integrations above involve only the providers and registries you configure or run yourself.

If a hosted service is ever offered, it will be separate software with its own policy, and
the skill will keep working fully offline. Nothing in this repository will be crippled to
make a paid tier attractive.

## Questions

Open an issue. For anything you would rather not post publicly, follow the private-contact
route in [SECURITY.md](SECURITY.md).
