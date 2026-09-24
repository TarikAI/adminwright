---
type: regex
target: { source: file, path: src/app/admin/tenants/page.tsx }
pattern: 'Acme Corp|Globex|Initech'
match: not_contains
weight: 2
---
