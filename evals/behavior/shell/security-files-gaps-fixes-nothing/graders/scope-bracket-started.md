---
# The orchestrator records the reviewer's starting point before it dispatches the pass, so
# review-scope can check afterwards what the reviewer changed.
type: tool_used
tool: Bash
input_match: 'review-scope[^"]*--start'
arm: with-only
---
