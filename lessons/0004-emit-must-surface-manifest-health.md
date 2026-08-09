---
id: 0004
title: "Emit must surface manifest health"
date: 2026-08-09
category: tooling
scope: "scripts/admin_console_manifest.py"
status: adopted
confidence: high
platforms: ["nextjs-ai-saas"]
trigger: "Round-2 field test: an audit session hand-wrote manifest JSON, ran only emit, and shipped a gap report while 83 plan-phase validation errors sat invisible - invalid entity ids, missing required fields, gap severities error/warning outside the enum. The release gate would have caught all of it, but nothing the session actually ran ever said so."
rule: "Every emit prints a manifest-health warning to stderr when plan validation finds errors, the gap-report format carries a health line in the document itself, and emit nudges when feedback[] is empty. Guidance: build the manifest through add/set, never hand-written JSON."
evidence: ["field test 2026-08-09: AIVORA manifest, 83 errors; docs/admin-gap-report.md shipped regardless"]
---

# Emit must surface manifest health

## Trigger

Round-2 field test: an audit session hand-wrote manifest JSON, ran only emit, and shipped a gap report while 83 plan-phase validation errors sat invisible - invalid entity ids, missing required fields, gap severities error/warning outside the enum. The release gate would have caught all of it, but nothing the session actually ran ever said so.

## Rule

Every emit prints a manifest-health warning to stderr when plan validation finds errors, the gap-report format carries a health line in the document itself, and emit nudges when feedback[] is empty. Guidance: build the manifest through add/set, never hand-written JSON.

## Evidence

- field test 2026-08-09: AIVORA manifest, 83 errors; docs/admin-gap-report.md shipped regardless

## Promotion

Status is `adopted`. Promote into a reference file only under the promotion bar,
and state what the adoption replaces or trims.
