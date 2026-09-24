---
type: regex
target: { source: file, path: src/server/tenants.ts }
pattern: 'export async function suspendTenant\(tenantId: string\) \{\r?\n  return db\.tenant\.update\(\{ where: \{ id: tenantId \}, data: \{ status: "SUSPENDED" \} \}\);\r?\n\}'
weight: 2
---
