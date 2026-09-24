#!/usr/bin/env bash
# The architect's pass is done: tenant.list is planned, and the build order names staff
# sign-in (already in src/server/auth.ts) as the spine it depends on.
source "$(dirname "${BASH_SOURCE[0]}")/../../_fixtures/scaffold-lib.sh"
copy_base
init_manifest
acm add --manifest "$MANIFEST" --kind capability --entity tenant --json '{"id":"tenant.list","outcome":"Find a tenant by name and see its plan and status","kind":"query","roles":["support","admin"],"risk":"low","entityStates":{"from":["ACTIVE","SUSPENDED","CLOSED"],"to":[]},"uiRoutes":["/admin/tenants"]}' >/dev/null
acm set --manifest "$MANIFEST" --path 'entities[tenant].capabilities[tenant.list].status' --value planned >/dev/null
acm add --manifest "$MANIFEST" --kind screen --json '{"id":"tenants","route":"/admin/tenants","purpose":"Find a tenant by name","roles":["support","admin"],"capabilities":["tenant.list"],"status":"planned"}' >/dev/null
acm add --manifest "$MANIFEST" --kind decision --json '{"id":"dec.build-order","decision":"Walking skeleton: staff sign-in (NextAuth, src/server/auth.ts) is the spine and is in place; tenant.list is the first slice, then tenant.suspend","reason":"Every admin slice depends on the staff session, which already exists","status":"confirmed","appliesTo":["tenant.list"],"kind":"other"}' >/dev/null
commit_all "Architect pass: tenant.list planned"
