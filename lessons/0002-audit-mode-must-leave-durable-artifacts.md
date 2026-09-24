---
id: "0002"
title: "Audit mode must leave durable artifacts"
date: "2026-08-08"
category: gap
scope: "SKILL.md"
status: adopted
confidence: repeated
platforms: ["supabase-mobile", "nextjs-ai-saas"]
---

## Trigger

First field tests: audit sessions on two real projects (a Supabase mobile app, a Next.js AI platform) produced thorough chat reports and nothing else. No manifest, no gaps[], no feedback[], no harvest. The learning loop never started because its first artifact was never created, and the next agent on either project starts from zero.

## Rule

Audit mode initializes the manifest if absent, models findings at status discovered, records gaps in gaps[] and observations in feedback[], and writes the report to a file via emit --format gap-report --out. Every mode ends with harvest.

## Evidence

- Project A (supabase-mobile), field test 2026-08-08: audit session transcript analysis; a chat report and no manifest.
- Project B (nextjs-ai-saas), field test 2026-08-08: audit session transcript analysis; a chat report and no manifest.

## Proposed edit

Target: `SKILL.md`, "Select the operating mode", the Audit bullet.

Replace: an audit described as reporting gaps.

With: the requirement to initialize the manifest, model findings at `discovered`, record `gaps[]` and `feedback[]`, and write `emit --format gap-report --out`. Applied in commit `9a0a571`.

## Review notes

- 2026-08-09 maintainer (TarikAI): adopted in commit `9a0a571`. The commit message gives the route: the audit gap was seen on two distinct projects.
- 2026-09-23 audit backfill (an agent, not the maintainer): brought the file into the format by removing the frontmatter copies of trigger, rule and evidence and replacing the Promotion section with Proposed edit (written from commit `9a0a571`) and Review notes, changed confidence `high` (not in the vocabulary) to `repeated` for two projects, and replaced the project names with neutral labels. The adopting agent was not recorded at the time.
- 2026-09-24 audit round 2 (an agent): the adoption line above had been dated 2026-08-08 and merged with the backfill; it now carries the commit's date and only what the commit says, and the backfill is its own line.
