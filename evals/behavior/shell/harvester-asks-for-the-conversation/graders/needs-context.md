---
# The harvester's own STATUS line, at the start of its returned message or of a line. The
# docs, and a dispatch prompt that says "return NEEDS_CONTEXT if...", quote the word
# elsewhere in a line, so neither satisfies this.
type: regex
target: trace
pattern: '(?:"|\\n|\n)STATUS: NEEDS_CONTEXT\b'
weight: 2
---
