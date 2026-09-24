---
description: Dispatched without a digest of the conversation, the harvester still banks the manifest's feedback and then returns NEEDS_CONTEXT, because a subagent cannot see the chat.
tags: [shell, agents, harvester]
max_turns: 20
timeout_seconds: 1800
allowed_tools: [Read, Glob, Grep, Skill, Agent, TodoWrite, Bash, Write, Edit]
---

The build is finished. Dispatch the adminwright harvester for this project, and don't give it any summary of our conversation. In this environment the observation store has to live in the project, so tell it to run harvest with ADMINWRIGHT_HOME=./.adminwright-store.
