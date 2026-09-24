---
description: A superadmin request for an LLM gateway loads the skill and gets operational controls that are real, not a dashboard of invented numbers.
tags: [smoke, trigger]
max_turns: 12
timeout_seconds: 600
allowed_tools: [Read, Glob, Grep, Skill]
---

We run an LLM gateway: customers bring their provider keys, we route their requests, meter usage and bill them. Every AI tool I've tried builds our superadmin area as a dashboard full of made-up numbers. What should the superadmin area actually let our operators do, and how do we make sure every control in it is real?
