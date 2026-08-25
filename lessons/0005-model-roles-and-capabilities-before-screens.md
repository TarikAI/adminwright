---
id: 0005
title: "Model roles and capabilities before screens"
date: 2026-08-20
category: gap
scope: "SKILL.md"
status: adopted
confidence: high
platforms: ["nextjs-ai-saas", "nextjs-fintech"]
trigger: "Two field projects modelled entities and screens but zero roles and zero capabilities, so nothing traced to a server operation and repair mode had no targets. Both sessions also ended without harvest."
rule: "Phase 3 derives capabilities and Phase 2 records roles before any screen is added. A screen without a capability, or an entity whose lifecycle no command reaches, is an unfinished model rather than a design choice; validate --phase plan is the checkpoint that says so."
evidence: []
---

# Model roles and capabilities before screens

## Trigger

Two field projects modelled entities and screens but zero roles and zero capabilities, so nothing traced to a server operation and repair mode had no targets. Both sessions also ended without harvest.

## Rule

Phase 3 derives capabilities and Phase 2 records roles before any screen is added. A screen without a capability, or an entity whose lifecycle no command reaches, is an unfinished model rather than a design choice; validate --phase plan is the checkpoint that says so.

## Evidence

- none recorded

## Promotion

Status is `adopted`. Promote into a reference file only under the promotion bar,
and state what the adoption replaces or trims.
