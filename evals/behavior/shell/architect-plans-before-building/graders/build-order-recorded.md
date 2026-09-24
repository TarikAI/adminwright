---
type: regex
target: { source: file, path: .admin-console/manifest.json }
pattern: '"decisions"\s*:\s*\[[\s\S]*(?:build order|walking skeleton|spine)'
flags: i
---
