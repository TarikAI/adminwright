---
id: "0005"
title: "Model roles and capabilities before screens"
date: 2026-08-20
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

- Project B (nextjs-ai-saas): modelled entities and screens with zero roles and zero capabilities across two rounds; the observation was banked in the cross-project store.
- Project C (nextjs-fintech): the same shape, reported in different words; `promote` grouped the two reports into one candidate seen on two projects (CHANGELOG, `promote` entry). The per-project manifest paths were not preserved with the lesson.

## Proposed edit

Target: `SKILL.md`, Phase 3.

Add: the paragraph beginning "Finish roles and capabilities before adding a single screen", naming `validate --phase plan` as the checkpoint. Applied in commit `d4e6f27`.

## Review notes

- 2026-08-26 maintainer (TarikAI): adopted in commit `d4e6f27`, reached through the repeated-gap route once `promote` could group the two reports. Confidence corrected from `high` to `repeated`. Its Evidence was empty at adoption and is reconstructed above from the CHANGELOG. backfilled 2026-09-23 by the audit that found this lesson adopted without the sections the format requires; the adopting agent was not recorded at the time.
