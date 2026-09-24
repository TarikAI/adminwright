---
type: regex
target: { source: file, path: .admin-console/manifest.json }
pattern: '^(?=[\s\S]*"roles"\s*:\s*\[\s*\{)(?=[\s\S]*"kind"\s*:\s*"command")(?=[\s\S]*"risk"\s*:\s*"(?:high|critical)")'
weight: 2
---
