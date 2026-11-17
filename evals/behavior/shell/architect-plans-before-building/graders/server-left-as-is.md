---
# The architect writes no feature code. file_exists sees only files a run creates, so an
# edit to this scaffolded file is caught here: it must match the fixture exactly.
type: regex
target: { source: file, path: src/server/tenants.ts }
pattern: '^import \{ db \} from "\.\/db";\r?\n\r?\n\/\/ fixture:tenants-v1\r?\nexport async function listTenants\(query: string\) \{\r?\n  return db\.tenant\.findMany\(\{\r?\n    where: \{ name: \{ contains: query, mode: "insensitive" \} \},\r?\n    include: \{ subscription: true \},\r?\n    orderBy: \{ createdAt: "desc" \},\r?\n    take: 50,\r?\n  \}\);\r?\n\}\r?\n\r?\nexport async function suspendTenant\(tenantId: string\) \{\r?\n  return db\.tenant\.update\(\{ where: \{ id: tenantId \}, data: \{ status: "SUSPENDED" \} \}\);\r?\n\}\r?\n$'
---
