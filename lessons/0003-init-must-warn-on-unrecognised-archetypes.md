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

- 2026-08-08 maintainer (TarikAI): adopted in commit `9a0a571` on the correction route: the behaviour was a defect in the tool, so one sighting sufficed. Confidence corrected from `high` to `observed-once` (one project). Project name replaced with a neutral label. backfilled 2026-09-23 by the audit that found this lesson adopted without the sections the format requires; the adopting agent was not recorded at the time.
