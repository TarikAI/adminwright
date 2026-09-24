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

- 2026-08-08 maintainer (TarikAI): adopted in commit `9a0a571`. Confidence corrected from `high`, which is not in the format's vocabulary, to `repeated` (two distinct projects). Project names replaced with neutral labels. backfilled 2026-09-23 by the audit that found this lesson adopted without the sections the format requires; the adopting agent was not recorded at the time.
