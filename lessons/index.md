# Lesson index

Last consolidation: 2026-08-06
Guidance level: 0005

One row per lesson, ordered by id. `lesson add` rebuilds the table from the lesson
files, so the index cannot drift from them. Field meanings, statuses, and the review
procedure are in [README.md](README.md). The promotion bar is in
[../references/skill-evolution.md](../references/skill-evolution.md).

`scope` is skill-root-relative and names the single reference file the rule lands in.

| id | title | category | scope | status | confidence | platforms | date |
|---|---|---|---|---|---|---|---|
| [0001](0001-example-lesson.md) | Authorize every target row in a bulk action | incorrect-guidance | references/security-governance.md | adopted | repeated | b2b-saas-multi-tenant; marketplace; next.js+postgres+prisma; laravel+mysql+queues | 2026-03-02 |
| [0002](0002-audit-mode-must-leave-durable-artifacts.md) | Audit mode must leave durable artifacts | gap | SKILL.md | adopted | repeated | supabase-mobile; nextjs-ai-saas | 2026-08-08 |
| [0003](0003-init-must-warn-on-unrecognised-archetypes.md) | Init must warn on unrecognised archetypes | tooling | scripts/admin_console_manifest.py | adopted | observed-once | nextjs-fintech | 2026-08-08 |
| [0004](0004-emit-must-surface-manifest-health.md) | Emit must surface manifest health | tooling | scripts/admin_console_manifest.py | adopted | observed-once | nextjs-ai-saas | 2026-08-09 |
| [0005](0005-model-roles-and-capabilities-before-screens.md) | Model roles and capabilities before screens | gap | SKILL.md | adopted | repeated | nextjs-ai-saas; nextjs-fintech | 2026-08-20 |
