#!/usr/bin/env bash
# tenant.suspend handed off as implemented, and security has filed a high gap against it.
source "$(dirname "${BASH_SOURCE[0]}")/../../_fixtures/scaffold-lib.sh"
copy_base
init_manifest
add_suspend_implemented
acm add --manifest "$MANIFEST" --kind gap --json '{"id":"gap.suspend-unguarded","severity":"high","description":"tenant.suspend: suspendTenant checks no staff session or role on the server and records no audit event, although the manifest lists both","status":"open","rationale":"","evidence":[],"capability":"tenant.suspend"}' >/dev/null
commit_all "Security review: gap.suspend-unguarded"
