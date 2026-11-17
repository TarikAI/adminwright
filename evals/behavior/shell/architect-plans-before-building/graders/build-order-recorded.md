---
# A decision records the build order: the `dec.build-order` id the references name, or a
# decision whose own text is about the order. A gap or feedback entry that mentions the
# spine does not count.
type: regex
target: { source: file, path: .admin-console/manifest.json }
pattern: '"id"\s*:\s*"dec\.build-order"|"decision"\s*:\s*"[^"]*(?:build order|walking skeleton|spine)'
flags: i
---
