---
# The route both reads the staff session (a call, not the word in a comment) and refuses
# with 401 or 403, or Next's unauthorized() / forbidden().
type: regex
target: { source: file, path: src/app/api/admin/tenants/route.ts }
pattern: '^(?=[\s\S]*\bawait\s+(?:auth|getServerSession)\s*\()(?=[\s\S]*(?:\b40[13]\b|\b(?:unauthorized|forbidden)\s*\(\s*\)))'
weight: 2
---
