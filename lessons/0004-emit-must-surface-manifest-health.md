---
id: "0004"
title: "Emit must surface manifest health"
date: "2026-08-09"
category: tooling
scope: "scripts/admin_console_manifest.py"
status: adopted
confidence: observed-once
platforms: ["nextjs-ai-saas"]
---

## Trigger

Round-2 field test: an audit session hand-wrote manifest JSON, ran only emit, and shipped a gap report while 83 plan-phase validation errors sat invisible - invalid entity ids, missing required fields, gap severities error/warning outside the enum. The release gate would have caught all of it, but nothing the session actually ran ever said so.

## Rule

Every emit prints a manifest-health warning to stderr when plan validation finds errors, the gap-report format carries a health line in the document itself, and emit nudges when feedback[] is empty. Guidance: build the manifest through add/set, never hand-written JSON.

## Evidence

- Project B (nextjs-ai-saas), field test 2026-08-09: its manifest carried 83 plan-phase errors and `docs/admin-gap-report.md` shipped regardless.

## Proposed edit

Target: `scripts/admin_console_manifest.py`, `cmd_emit`.

Add: a stderr warning when plan validation finds errors, a manifest-health line in the `gap-report` document, and a nudge when `feedback[]` is empty. Applied in commit `b35d956`.

## Review notes

- 2026-08-09 maintainer (TarikAI): adopted in commit `b35d956`. The commit message names no promotion route. One project.
- 2026-09-23 audit backfill (an agent, not the maintainer): brought the file into the format by removing the frontmatter copies of trigger, rule and evidence and replacing the Promotion section with Proposed edit (written from commit `b35d956`) and Review notes, changed confidence `high` (not in the vocabulary) to `observed-once` for one project, and replaced the project name with a neutral label. The adopting agent was not recorded at the time.
- 2026-09-24 audit round 2 (an agent): the adoption line above had named the correction route, which commit `b35d956` does not; it now says only what the commit says. A `tooling` finding seen on one project with no route recorded: adopted outside the promotion bar in references/skill-evolution.md.
