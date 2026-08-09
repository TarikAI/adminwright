---
id: 0002
title: "Audit mode must leave durable artifacts"
date: 2026-08-08
category: gap
scope: "SKILL.md"
status: adopted
confidence: high
platforms: ["supabase-mobile", "nextjs-ai-saas"]
trigger: "First field tests: audit sessions on two real projects (a Supabase mobile app, a Next.js AI platform) produced thorough chat reports and nothing else. No manifest, no gaps[], no feedback[], no harvest. The learning loop never started because its first artifact was never created, and the next agent on either project starts from zero."
rule: "Audit mode initializes the manifest if absent, models findings at status discovered, records gaps in gaps[] and observations in feedback[], and writes the report to a file via emit --format gap-report --out. Every mode ends with harvest."
evidence: ["field test 2026-08-08: Roya and AIVORA audit sessions, transcript analysis"]
---

# Audit mode must leave durable artifacts

## Trigger

First field tests: audit sessions on two real projects (a Supabase mobile app, a Next.js AI platform) produced thorough chat reports and nothing else. No manifest, no gaps[], no feedback[], no harvest. The learning loop never started because its first artifact was never created, and the next agent on either project starts from zero.

## Rule

Audit mode initializes the manifest if absent, models findings at status discovered, records gaps in gaps[] and observations in feedback[], and writes the report to a file via emit --format gap-report --out. Every mode ends with harvest.

## Evidence

- field test 2026-08-08: Roya and AIVORA audit sessions, transcript analysis

## Promotion

Status is `adopted`. Promote into a reference file only under the promotion bar,
and state what the adoption replaces or trims.
