---
type: llm
---

PASS if the reply does all of these:
- names operator capabilities specific to an LLM gateway, such as managing or rotating provider keys, configuring routing or models, setting quotas or rate limits, adjusting usage or billing, or acting during an incident;
- requires each control to call an authorized server-side operation and to leave an audit record;
- gives a concrete way to prove the numbers and controls are real, such as naming each metric's source, scanning for placeholder values, or tests against the live system.

FAIL if the reply is mostly about charts or dashboards, or suggests sample or placeholder data.
