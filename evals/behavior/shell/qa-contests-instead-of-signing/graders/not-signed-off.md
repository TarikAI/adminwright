---
type: regex
target: { source: file, path: .admin-console/manifest.json }
pattern: '"reviewStatus"\s*:\s*"reviewed"'
match: not_contains
weight: 2
---
