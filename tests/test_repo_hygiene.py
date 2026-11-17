# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TarikAI
"""Repository hygiene: copies that must stay copies.

`init` puts a copy of the manifest schema beside every manifest it writes, and the golden
eval fixtures carry one each. A schema change that leaves a fixture with a stale copy would
make the fixtures describe a schema nobody ships.
"""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = ROOT / "assets" / "admin-console.manifest.schema.json"
FIXTURE_COPIES = sorted((ROOT / "evals" / "fixtures").glob("*/.admin-console/admin-console.manifest.schema.json"))


class SchemaCopies(unittest.TestCase):
    def test_there_are_fixture_copies_to_compare(self):
        names = [path.parent.parent.name for path in FIXTURE_COPIES]
        self.assertEqual(names, ["gate-matrix", "logistics-gaps", "saas-clean"])

    def test_every_fixture_copy_is_byte_identical_to_the_shipped_schema(self):
        shipped = SCHEMA.read_bytes()
        for path in FIXTURE_COPIES:
            with self.subTest(fixture=path.parent.parent.name):
                self.assertEqual(
                    path.read_bytes(), shipped,
                    "copy the schema over it: cp assets/admin-console.manifest.schema.json " + str(path.relative_to(ROOT)),
                )


if __name__ == "__main__":
    unittest.main()
