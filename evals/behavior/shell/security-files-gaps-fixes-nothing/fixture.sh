#!/usr/bin/env bash
# tenant.suspend handed off as implemented; its server code checks nothing.
source "$(dirname "${BASH_SOURCE[0]}")/../../_fixtures/scaffold-lib.sh"
copy_base
init_manifest
add_suspend_implemented
commit_all "Implementer handoff: tenant.suspend"
