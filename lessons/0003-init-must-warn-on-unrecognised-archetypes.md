---
id: "0003"
title: "Init must warn on unrecognised archetypes"
date: "2026-08-08"
category: tooling
scope: "scripts/admin_console_manifest.py"
status: adopted
confidence: observed-once
platforms: ["nextjs-fintech"]
---

## Trigger

A trading platform ran init --archetype financial at the regulated profile. The word resolved to no known archetype, so archetype-coverage checking was silently disabled for the whole build, and nothing ever said so.

## Rule

init warns when an archetype does not resolve and lists the known keys; common money words (financial, finance, trading, crypto, investing) alias to fintech.

## Evidence

- Project C (nextjs-fintech), field test 2026-08-08: the manifest's archetype `financial` resolved to no known archetype.

## Proposed edit

Target: `scripts/admin_console_manifest.py`, `cmd_init` and `ARCHETYPE_ALIASES`.

Add: a warning naming the unrecognised archetype and the known keys, and the aliases `financial`, `finance`, `trading`, `crypto`, `investing`. Applied in commit `9a0a571`.

## Review notes

- 2026-08-09 maintainer (TarikAI): adopted in commit `9a0a571`, whose message calls the archetype miss "a correction-grade tooling defect". One project.
- 2026-09-23 audit backfill (an agent, not the maintainer): brought the file into the format by removing the frontmatter copies of trigger, rule and evidence and replacing the Promotion section with Proposed edit (written from commit `9a0a571`) and Review notes, changed confidence `high` (not in the vocabulary) to `observed-once` for one project, and replaced the project name with a neutral label. The adopting agent was not recorded at the time.
- 2026-09-24 audit round 2 (an agent): the adoption line above had been dated 2026-08-08 and gave a reason in the maintainer's name; it now carries the commit's date and quotes the commit. The correction route in references/skill-evolution.md is written for guidance that states something wrong, not for a tool defect seen once, so this adoption rests on the maintainer's judgement in that commit rather than on a route in the table.
