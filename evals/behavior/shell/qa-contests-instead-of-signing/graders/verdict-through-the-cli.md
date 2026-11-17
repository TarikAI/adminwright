---
# The verdict came through the CLI: a qa pass registered itself in agents[] (claim or
# add --kind agent does that). A manifest rewritten by hand to say `contested` has no qa
# agent record, because the fixture has none.
type: regex
target: { source: file, path: .admin-console/manifest.json }
pattern: '"role"\s*:\s*"qa"'
weight: 2
---
