---
# The main session never edits the manifest with its file tools. This sees the main
# session's calls only, not the ones a dispatched agent makes in its own context.
type: tool_used
tool: Edit
input_match: 'manifest\.json'
min: 0
max: 0
arm: both
---
