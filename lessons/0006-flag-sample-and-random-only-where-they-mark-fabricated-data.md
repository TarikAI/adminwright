---
id: "0006"
title: "Flag sample and random only where they mark fabricated data"
date: "2026-11-02"
category: incorrect-guidance
scope: "scripts/admin_console_manifest.py"
status: adopted
confidence: repeated
platforms: ["expo-supabase-audio-ml", "fastify-microservices-marketplace"]
---

## Trigger

Two projects lost work to the bare-word ban on sample(s) and random in manifest implementation fields. Project A (an audio ML console, 2026-10-26): the real table public.cry_samples was refused in entities[].sourceOfTruth; the build renamed its operator concept to cry label and registered the table in declaredStatic[], and that registration is now itself an error because a sourceOfTruth can never be declared static, so validate --phase plan could not pass at all. Project B (2026-08-13): an entity sourceOfTruth that accurately described a secret generated with Math.random() was refused on the word random.

## Rule

In manifest implementation fields, treat sample and random as placeholders only where they mark fabricated data: when they modify the word after them (sample data, SAMPLE_USERS, randomMetrics) and that word is not a sampling term or a code noun (sample_rate, random seed, CrySampleRepository), or when random is a number-generator call (Math.random(), random.randint). As a head noun (public.cry_samples, a random sample of refunds, chosen at random) they are domain vocabulary. The strict words (mock, fake, stub, dummy, placeholder, lorem, todo, tbd, fixme, coming soon, not implemented) are unchanged.

## Evidence

- Project A (expo-supabase-audio-ml), 2026-10-26: `entities[cry-label].sourceOfTruth` = `postgres:public.cry_samples` (the table is in `supabase/migrations/001_initial_schema.sql`) failed `placeholder-scan` on `samples`; the workaround `declaredStatic[static.cry-samples-table]` fails `declared-static-forbidden-path`. The manifest's own `feedback[fb.placeholder-scan-domain-words]` records the observation.
- Project B (fastify-microservices-marketplace), 2026-08-13: an entity `sourceOfTruth` describing a client secret generated with `Math.random()` failed `placeholder-scan` on `random` (store observation `fb-placeholder-random-false-positive`).
- Regression cases: `tests/test_honest_work.py` `TestSampleAndRandomAreVocabulary`.

## Proposed edit

Target: `scripts/admin_console_manifest.py`, the placeholder scan.

Replaces: `PLACEHOLDER_PATTERN` matched the bare words `sample(s)` and `random(ly)` in every
scanned manifest field. It now holds the strict words only (it was identical to
`FILE_PLACEHOLDER_PATTERN` minus those two, so that duplicate is removed), and a new
`fabrication_match()` flags `sample`/`random`/`randomly`/`randomized` only as a modifier of a
following word outside `SOFT_COMPOUNDS` (sampling terms and code nouns) and
`SOFT_FUNCTION_WORDS`, or `random` as a generator call (`Math.random`, `np.random`,
`random.<fn>`, `random(`). `getRandomValues`, `randomUUID` and `randomBytes` are masked first:
secure generators make real secrets, not stand-in data. Net +42 lines in the script.

Chosen over a declared `domainVocabulary[]`: no new manifest vocabulary (anti-drift rule), no
edit needed in the affected manifests, and the default stays strict, because an unforeseen
modifier (`sample orders`) still fails. `platform.domainTerms` still covers modifier uses of an
entity noun (a lab's `Sample status`). Not scoped to a profile: `placeholder-scan` is an error at
every profile on purpose, and a profile scope would let `sampleData` through at `internal`
while still refusing the real table at `regulated`. The narrowing is by usage instead.

Documentation: `references/test-data.md`, the sentence "Manifest implementation fields get the
stricter scan: the same words plus `sample` and `random`" is replaced by the head-noun versus
modifier rule. Net 0 lines (179, under the 400 budget).

Not changed:

- Strict words, the substring pass, `declaredStatic[]` and its forbidden fields.
- Evidence-file content scanning. Its two triggers (a cited test containing `vi.mock`, a
  component containing `placeholder=`) were already fixed: tests are never content-scanned and
  framework idioms are skipped. The whole-word scan of run output stays, because it is the only
  check that catches a hand-written `TODO: paste real output` log
  (`TestEvidenceContent.test_a_genuine_placeholder_in_evidence_names_token_and_line`); a log that
  legitimately quotes a word uses the `<path>#content` escape.
- Project B's case still fails, by design: a regex cannot tell an insecure-but-real generator
  from a fabricated series. `sourceOfTruth` names where the data lives; the defect belongs in
  `gaps[]`, which is not scanned.
- Known limit: a table named with `random` as a modifier (`random_draws`) is still flagged.
  Name the entity and add it to `platform.domainTerms`.

## Review notes

- 2026-11-02 adopted by Claude (single-agent adoption pass, at maintainer TarikAI's request); edit in 53619bdd2ca9c6a7610e26032aa6d82ed69fd051
