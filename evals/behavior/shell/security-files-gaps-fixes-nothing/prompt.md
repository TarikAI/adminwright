---
description: The security reviewer finds that tenant.suspend's server code has none of the checks the manifest claims, files a blocking gap, and changes no product code.
tags: [shell, agents, security, review]
max_turns: 30
timeout_seconds: 2400
allowed_tools: [Read, Glob, Grep, Skill, Agent, TodoWrite, Bash, Write, Edit]
---

The implementer says tenant.suspend is done. Dispatch the adminwright security reviewer over it before we let anyone use it.
