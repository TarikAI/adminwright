# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TarikAI
"""The JSON Schema and the validator must agree about what a manifest is.

The schema types every field; until the validator grew a schema-driven type walker,
nothing checked the two against each other, and `"tests": "tests/x.spec.ts"` was
schema-invalid yet passed the release gate. This test validates everything the tool
itself produces -- the template, a fresh `init`, both golden fixtures that are meant to
be well formed, a migrated 1.0 manifest and a manifest after a stream of `add`/`set`/
`claim` writes -- against the schema with an independent implementation.

Needs `jsonschema` (CI installs it); skipped, not failed, where it is absent, so the
standard-library-only contract of the suite holds.
"""

import copy
import json
import unittest
from pathlib import Path

from cli_helpers import SKILL_ROOT, TempProject, run

try:
    import jsonschema
except ImportError:  # pragma: no cover - exercised only where the dependency is missing
    jsonschema = None

SCHEMA_PATH = SKILL_ROOT / "assets" / "admin-console.manifest.schema.json"
TEMPLATE_PATH = SKILL_ROOT / "assets" / "admin-console.manifest.template.json"


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


@unittest.skipIf(jsonschema is None, "jsonschema is not installed")
class TestSchemaConformance(TempProject):
    def setUp(self):
        super().setUp()
        self.schema = load(SCHEMA_PATH)
        jsonschema.Draft202012Validator.check_schema(self.schema)
        self.validator = jsonschema.Draft202012Validator(self.schema)

    def assert_conforms(self, manifest, label):
        errors = sorted(self.validator.iter_errors(manifest), key=lambda e: list(e.path))
        self.assertEqual(
            [], ["%s: %s" % ("/".join(str(p) for p in e.path), e.message) for e in errors[:8]],
            label + " does not conform to the schema",
        )

    def test_the_template(self):
        template = load(TEMPLATE_PATH)
        # the template is the one document allowed to hold the empty placeholder id
        template = copy.deepcopy(template)
        template["platform"]["id"] = "template-000000"
        self.assert_conforms(template, "template")

    def test_a_fresh_init(self):
        self.assert_conforms(self.read(), "fresh init output")

    def test_the_well_formed_golden_fixtures(self):
        for name in ("saas-clean", "logistics-gaps"):
            path = SKILL_ROOT / "evals" / "fixtures" / name / ".admin-console" / "manifest.json"
            self.assert_conforms(load(path), name)

    def test_a_manifest_after_add_set_and_claim_writes(self):
        self.build_honest_manifest()
        self.assert_conforms(self.read(), "honest manifest")
        code, _o, err = self.cli("add", "--kind", "role", "--json", json.dumps(
            {"id": "support", "name": "Support", "responsibilities": ["Help"], "scopes": ["own"]}))
        self.assertEqual(code, 0, err)
        code, _o, err = self.cli("set", "--path", "roles[support].mfaRequired", "--value", "true")
        self.assertEqual(code, 0, err)
        code, _o, err = self.cli("claim", "--agent", "a1", "--role", "implementer",
                                 "--capability", "user.list")
        self.assertEqual(code, 0, err)
        self.cli("release-claim", "--agent", "a1")
        self.assert_conforms(self.read(), "manifest after writes")

    def test_a_migrated_1_0_manifest(self):
        old = self.read()
        old["manifestVersion"] = "1.0"
        for key in ("declaredStatic", "feedback", "agents", "decisions", "projectRoot", "validatedWith"):
            old.pop(key, None)
        old["platform"].pop("stack", None)
        old["platform"].pop("id", None)
        old["platform"].pop("researchSources", None)
        old["platform"].pop("volumes", None)
        path = self.tmp / "old.json"
        path.write_text(json.dumps(old), encoding="utf-8")
        code, _o, err = run("migrate", "--manifest", path, "--write")
        self.assertEqual(code, 0, err)
        migrated = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(migrated["manifestVersion"], "2.0")
        self.assert_conforms(migrated, "migrated manifest")

    def test_the_two_agree_on_the_type_bypass(self):
        """The bypass that started this: schema-invalid AND rejected by the validator."""
        manifest = self.build_honest_manifest()
        self.capability_of(manifest)["tests"] = "tests/user.spec.ts"
        self.assertTrue(list(self.validator.iter_errors(manifest)))
        self.write(manifest)
        code, out, _err = self.validate()
        self.assertEqual(code, 1)
        self.assertIn("manifest-type", out)


if __name__ == "__main__":
    unittest.main()
