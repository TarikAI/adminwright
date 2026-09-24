---
type: regex
target: { source: file, path: src/app/api/admin/tenants/route.ts }
pattern: '\bauth\s*\(|getServerSession|session|\b40[13]\b|forbidden|unauthori[sz]ed'
flags: i
weight: 2
---
