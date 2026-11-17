---
# The STATUS line must start a returned message or a line of one. The protocol docs quote
# the same words after a backtick, so a Read of them never satisfies this.
type: regex
target: trace
pattern: '(?:"|\\n|\n)STATUS: (?:DONE|DONE_WITH_CONCERNS)\b'
arm: with-only
---
