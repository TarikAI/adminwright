# Shared by every case's fixture.sh, which sources this file. `claude plugin eval` runs a
# case's scaffold only with --scaffold, as you, in the run's empty workspace; everything
# here writes to the current directory only.
set -euo pipefail

FIXTURES_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ROOT="$(cd "$FIXTURES_DIR/../../.." && pwd)"
ACM_SCRIPT="$PLUGIN_ROOT/scripts/admin_console_manifest.py"
MANIFEST=".admin-console/manifest.json"

find_python() {
  local candidate
  for candidate in python3 python py; do
    if command -v "$candidate" >/dev/null 2>&1 \
      && "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 9))' >/dev/null 2>&1; then
      echo "$candidate"
      return 0
    fi
  done
  echo "scaffold: no Python 3.9+ on PATH (python3, python or py)" >&2
  return 1
}
PYTHON="$(find_python)"

# The Support Desk project: a Next.js + Prisma help desk whose admin page is decorative.
copy_base() {
  cp -R "$FIXTURES_DIR/support-desk/." .
}

acm() {
  "$PYTHON" "$ACM_SCRIPT" "$@"
}

# init, then the roles and the tenant entity every manifest-backed case starts from.
init_manifest() {
  acm init --project-root . --name "Support Desk" --archetype b2b-saas --profile standard >/dev/null
  acm add --manifest "$MANIFEST" --kind role --json '{"id":"support","name":"Support agent","responsibilities":["Find tenants","Answer tickets"],"scopes":["all tenants, read"],"mfaRequired":true}' >/dev/null
  acm add --manifest "$MANIFEST" --kind role --json '{"id":"admin","name":"Support admin","responsibilities":["Suspend abusive tenants","Change plans"],"scopes":["all tenants"],"mfaRequired":true}' >/dev/null
  acm add --manifest "$MANIFEST" --kind entity --json '{"id":"tenant","name":"Tenant","sourceOfTruth":"postgres:Tenant","sensitivity":"confidential","tenantScoped":false,"lifecycleStates":["ACTIVE","SUSPENDED","CLOSED"],"retention":"7 years after closure"}' >/dev/null
}

copy_overlay() {
  cp -R "$FIXTURES_DIR/overlays/$1/." .
}

# tenant.suspend as an implementer handed it off: the manifest claims a server-side
# admin check, an audit event and a confirmation, but src/server/tenants.ts has none of
# them. A reviewer should find that; nothing in the manifest says so.
add_suspend_implemented() {
  copy_overlay suspend-implemented
  acm add --manifest "$MANIFEST" --kind capability --entity tenant --json '{"id":"tenant.suspend","outcome":"Suspend an abusive tenant so its users cannot sign in","kind":"command","roles":["admin"],"risk":"high","entityStates":{"from":["ACTIVE"],"to":["SUSPENDED"]},"uiRoutes":["/admin/tenants"],"serverOperations":["suspendTenant"],"authorizationPolicies":["admin role required"],"auditEvents":["tenant.suspended"],"safeguards":["confirmation with a reason"],"recovery":"reactivate the tenant","idempotency":"suspending a suspended tenant changes nothing","dataBinding":"postgres:Tenant via suspendTenant","tests":["tests/tenants.test.ts"],"evidence":["evidence/tests.log"],"implementedBy":"impl-1"}' >/dev/null
  acm set --manifest "$MANIFEST" --path 'entities[tenant].capabilities[tenant.suspend].status' --value implemented >/dev/null
  acm add --manifest "$MANIFEST" --kind screen --json '{"id":"tenants","route":"/admin/tenants","purpose":"Find a tenant and suspend it","roles":["support","admin"],"capabilities":["tenant.suspend"],"status":"in-progress","rationale":"the list is not wired to the server yet"}' >/dev/null
}

# A git repository, so a reviewer pass can be checked with review-scope.
commit_all() {
  git init -q
  # Graders read files byte for byte; a machine-wide autocrlf must not rewrite them.
  git config core.autocrlf false
  git add -A
  git -c user.email=eval@example.test -c user.name=eval -c commit.gpgsign=false \
    commit -q --no-verify -m "$1"
}
