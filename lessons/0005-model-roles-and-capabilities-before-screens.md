---
id: "0005"
title: "Model roles and capabilities before screens"
date: "2026-08-20"
category: gap
scope: "SKILL.md"
status: adopted
confidence: repeated
platforms: ["nextjs-ai-saas", "nextjs-fintech"]
---

## Trigger

Two field projects modelled entities and screens but zero roles and zero capabilities, so nothing traced to a server operation and repair mode had no targets. Both sessions also ended without harvest.

## Rule

Phase 3 derives capabilities and Phase 2 records roles before any screen is added. A screen without a capability, or an entity whose lifecycle no command reaches, is an unfinished model rather than a design choice; validate --phase plan is the checkpoint that says so.

## Evidence

Reconstructed after adoption from commit `d4e6f27` and the CHANGELOG; no artifact paths (manifests, `feedback[]` ids, store records) were preserved, so neither line names one as this format requires.

- Project B (nextjs-ai-saas): modelled entities and screens with zero roles and zero capabilities across two rounds; the observation was banked in the cross-project store.
- Project C (nextjs-fintech): the same shape, reported in different words; `promote` grouped the two reports into one candidate seen on two projects (commit `d4e6f27`; CHANGELOG, `promote` entry).

## Proposed edit

Target: `SKILL.md`, Phase 3.

Add: the paragraph beginning "Finish roles and capabilities before adding a single screen", naming `validate --phase plan` as the checkpoint. Applied in commit `d4e6f27`.

## Review notes

- 2026-08-26 maintainer (TarikAI): adopted in commit `d4e6f27`, whose message records that `promote` now reported the finding as one candidate seen on two projects.
- 2026-09-23 audit backfill (an agent, not the maintainer): brought the file into the format by removing the frontmatter copies of trigger, rule and evidence and replacing the Promotion section with Proposed edit (written from commit `d4e6f27`) and Review notes, and changed confidence `high` (not in the vocabulary) to `repeated` for two projects. Evidence was empty at adoption ("none recorded"); the two lines above were reconstructed from the commit and the CHANGELOG, without artifacts. The adopting agent was not recorded at the time.
- 2026-09-24 audit round 2 (an agent): the adoption line above had been merged with the backfill; the backfill is now its own line, and Evidence says that it was reconstructed without artifacts. An adopted lesson cannot return to `proposed` (README, section 7), so it stays adopted with that gap stated.
