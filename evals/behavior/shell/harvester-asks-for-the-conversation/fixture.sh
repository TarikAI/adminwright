#!/usr/bin/env bash
# A finished run whose agents recorded two observations about the skill.
source "$(dirname "${BASH_SOURCE[0]}")/../../_fixtures/scaffold-lib.sh"
copy_base
init_manifest
acm add --manifest "$MANIFEST" --kind feedback --json '{"id":"fb-001","observation":"discovery.md never mentions auth callbacks, which is where this project assigns the staff role","category":"gap","scope":"references/discovery.md","proposedChange":"List auth-library callbacks among the places roles are defined","evidence":[]}' >/dev/null
acm add --manifest "$MANIFEST" --kind feedback --json '{"id":"fb-002","observation":"The implementer had to guess which paths its slice owned because the build-order decision listed none","category":"friction","scope":"agents","proposedChange":"Make the file-ownership table a required part of the build-order decision","evidence":[]}' >/dev/null
commit_all "Run finished"
