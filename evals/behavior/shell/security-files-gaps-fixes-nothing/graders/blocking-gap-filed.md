---
type: regex
target: { source: file, path: .admin-console/manifest.json }
pattern: '"severity"\s*:\s*"(?:critical|high)"'
weight: 2
---
