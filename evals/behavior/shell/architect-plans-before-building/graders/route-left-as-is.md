---
# The architect writes no feature code. file_exists sees only files a run creates, so an
# edit to this scaffolded file is caught here: it must match the fixture exactly.
type: regex
target: { source: file, path: src/app/api/admin/tenants/route.ts }
pattern: '^import \{ listTenants \} from "@\/server\/tenants";\r?\n\r?\nexport async function GET\(request: Request\) \{\r?\n  const query = new URL\(request\.url\)\.searchParams\.get\("q"\) \?\? "";\r?\n  return Response\.json\(await listTenants\(query\)\);\r?\n\}\r?\n$'
---
