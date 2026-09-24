# Privacy

Short version: **the skill's own commands make no network calls and send nothing anywhere.
Data leaves your machine only through a command that is defined to send it: syncing your
private observation store, sharing a sanitised export, or one of the optional integrations
below.** There is no telemetry, no analytics, and no phone-home.

## What runs locally

Every command of `scripts/admin_console_manifest.py` (`init`, `migrate`, `validate`,
`report`, `coverage`, `emit`, `add`, `set`, `remove`, `rename`, `claim`, `release-claim`,
`harvest`, `promote`, `store`, `lesson`) and of `scripts/install_agents.py` reads and writes
files on your machine only. The validator is Python standard library with no network access.

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
default and leaves this machine only through `store sync`, to the remote you configure.

## What leaves your machine, and only if you ask

`promote --export <file>` writes a sanitised bundle intended for sharing. Before writing, it:

- replaces with markers: emails; URLs of any scheme, including `scheme://user:password@host`
  and `user:password@host`; absolute and relative file paths; IPv4 and IPv6 addresses;
  hostnames and any other dotted name; UUIDs and long hashes; and credentials: AWS key and
  principal ids (`AKIA…`, `ASIA…`, `AIDA…`, `AROA…` and the other IAM prefixes), GitHub
  tokens (`ghp_`, `gho_`, `ghu_`, `ghs_`, `ghr_`, `github_pat_`), Google API keys
  (`AIza…`), `sk-`/`pk-`/`xox?-` keys, JWTs (signed or not), bearer tokens, private-key
  blocks, `password=`-style values, and any other run of 32 or more characters that mixes
  upper case, lower case and digits
- keeps the skill's own vocabulary readable: its file names (`SKILL.md`,
  `references/discovery.md`), stack names (`Next.js`, `package.json`) and manifest ids
  built from words the guidance uses (`tenant.suspend`)
- replaces project names with one-way fingerprints (SHA-256, truncated, salted with a random
  per-store salt created by `store init` or the first export and never synced or exported),
  so a fingerprint cannot be checked against a list of names. Because the salt is per store,
  the same project is countable only within bundles exported from one store: exported from
  two stores (your laptop and your desktop, or a store created again) it gets two different
  fingerprints
- carries no fingerprint of the observation text itself: a hash of the unscrubbed wording
  would let anyone confirm a guess at what was scrubbed
- keeps `scope` only when it names a file or area of the skill; anything else is exported
  as `unassigned`
- coarsens stack strings to family names
- drops evidence paths entirely

**Its limits, stated plainly:** no pattern can detect a company, product, customer or
person's name written as an ordinary word, and a credential in a shape not listed above that
is shorter than 32 characters, or split by spaces, can survive. The exporter prints this
warning and the file is yours to read before you share it. If you contribute without reading
it, you are the only person who could have caught what the patterns missed.

Writing the file shares nothing. Sharing happens only when you open a pull request.

## Contributions

See [community/README.md](community/README.md). Contribution is opt-in, contents are limited
to sanitised bundles, and withdrawal is a pull request that deletes your file. `promote
--export` never re-publishes contributions: with `--include-community` it ignores them.

## Third parties

None in the skill's own code: no dependencies, no hosted service, no accounts. The optional
integrations above involve only the providers and registries you configure or run yourself.

If a hosted service is ever offered, it will be separate software with its own policy, and
the skill will keep working fully offline. Nothing in this repository will be crippled to
make a paid tier attractive.

## Questions

Open an issue. For anything you would rather not post publicly, follow the private-contact
route in [SECURITY.md](SECURITY.md).
