#!/usr/bin/env bash
# The Support Desk project with no manifest: the architect starts from nothing.
source "$(dirname "${BASH_SOURCE[0]}")/../../_fixtures/scaffold-lib.sh"
copy_base
commit_all "Support Desk as it stands"
