---
description: Auditing a decorative admin page names the hard-coded data, the browser-only permission check and the button that calls nothing.
tags: [smoke, audit]
max_turns: 25
timeout_seconds: 900
allowed_tools: [Read, Glob, Grep, Skill]
---

Before we give our support team access, audit the admin area of this repository: the pages under src/app/admin and the API routes under src/app/api/admin. Is it safe to hand to them? Don't change any code; just tell me what you found.
