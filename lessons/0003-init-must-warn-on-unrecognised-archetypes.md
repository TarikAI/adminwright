---
id: 0003
title: "Init must warn on unrecognised archetypes"
date: 2026-08-08
category: tooling
scope: "scripts/admin_console_manifest.py"
status: adopted
confidence: high
platforms: ["nextjs-fintech"]
trigger: "A trading platform ran init --archetype financial at the regulated profile. The word resolved to no known archetype, so archetype-coverage checking was silently disabled for the whole build, and nothing ever said so."
rule: "init warns when an archetype does not resolve and lists the known keys; common money words (financial, finance, trading, crypto, investing) alias to fintech."
evidence: ["field test 2026-08-08: ZenithAPP manifest, archetype financial resolved to None"]
---

# Init must warn on unrecognised archetypes

## Trigger

A trading platform ran init --archetype financial at the regulated profile. The word resolved to no known archetype, so archetype-coverage checking was silently disabled for the whole build, and nothing ever said so.

## Rule

init warns when an archetype does not resolve and lists the known keys; common money words (financial, finance, trading, crypto, investing) alias to fintech.

## Evidence

- field test 2026-08-08: ZenithAPP manifest, archetype financial resolved to None

## Promotion

Status is `adopted`. Promote into a reference file only under the promotion bar,
and state what the adoption replaces or trims.
