---
# A role exists, and one capability object is both a command and high or critical risk:
# the two keys may not come from different capabilities. Flat nested objects such as
# entityStates may sit between them.
type: regex
target: { source: file, path: .admin-console/manifest.json }
pattern: '^(?=[\s\S]*"roles"\s*:\s*\[\s*\{)(?=[\s\S]*(?:"kind"\s*:\s*"command"(?:[^{}]|\{[^{}]*\})*"risk"\s*:\s*"(?:high|critical)"|"risk"\s*:\s*"(?:high|critical)"(?:[^{}]|\{[^{}]*\})*"kind"\s*:\s*"command"))'
weight: 2
---
