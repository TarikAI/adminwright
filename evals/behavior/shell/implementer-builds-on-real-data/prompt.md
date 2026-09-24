---
description: The implementer claims tenant.list, replaces the hard-coded tenants with the real query, checks the staff session on the server, and leaves review to someone else.
tags: [shell, agents, implementer]
max_turns: 30
timeout_seconds: 3000
allowed_tools: [Read, Glob, Grep, Skill, Agent, TodoWrite, Bash, Write, Edit]
---

Dispatch the adminwright implementer to build tenant.list, the /admin/tenants page. The capability is planned in .admin-console/manifest.json and the build order is recorded there. This slice owns src/app/admin/tenants/, src/app/api/admin/tenants/ and src/server/tenants.ts.
