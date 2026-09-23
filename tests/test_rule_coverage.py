# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TarikAI
"""Every rule the validator advertises must be pinned by something that fails without it.

At the audited commit 48 of 63 rules were asserted by no test and no eval, including
`gate-not-passed`, `evidence-path-missing`, `audit-events-missing`, `placeholder-scan`
and `capability-screen-link`: any of them could be deleted, weakened to `off`, or made
unreachable and CI would stay green. Writing this test also found one that was already
unreachable (`implementer-unrecorded` fired only for contested capabilities).

The `gate-matrix` fixture is one console broken on purpose so that each rule fires; its
`expected-findings.json` pins the finding set at every profile. This test then fails
if a rule exists in `RULES` that no fixture expectation mentions.
"""

import glob
import json
import unittest
from pathlib import Path

from cli_helpers import SKILL_ROOT, TempProject, acm

# Rules that cannot fire at --phase release, which is the phase the eval fixtures run.
# Each names the test that does assert it.
PLAN_ONLY = {"plan-note": "tests/test_rule_coverage.py"}


def fixture_rules():
    found = {}
    for path in sorted(glob.glob(str(SKILL_ROOT / "evals" / "fixtures" / "*" / "expected-findings.json"))):
        expectation = json.loads(Path(path).read_text(encoding="utf-8"))
        for profile in expectation.values():
            for rule in profile.get("findings", {}):
                found.setdefault(rule, Path(path).parent.name)
    return found


class TestEveryRuleIsPinned(unittest.TestCase):
    def test_every_rule_appears_in_a_fixture_expectation(self):
        covered = set(fixture_rules())
        missing = sorted(set(acm.RULES) - covered - set(PLAN_ONLY))
        self.assertEqual(
            missing, [],
            "rules that no eval fixture expects (add a defect to evals/build_fixtures.py's "
            "build_matrix, regenerate, and review `python evals/run.py --update`): "
            + ", ".join(missing),
        )

    def test_fixtures_expect_only_rules_that_exist(self):
        unknown = sorted(set(fixture_rules()) - set(acm.RULES))
        self.assertEqual(unknown, [], "a fixture expects a rule the validator no longer has")

    def test_plan_only_rules_name_a_test_that_asserts_them(self):
        for rule, relative in PLAN_ONLY.items():
            self.assertIn(rule, acm.RULES)
            text = (SKILL_ROOT / relative).read_text(encoding="utf-8")
            self.assertIn('"[' + rule + ']"', text, relative + " must assert " + rule)

    def test_every_rule_is_in_the_published_matrix(self):
        doc = (SKILL_ROOT / "references" / "rule-matrix.md").read_text(encoding="utf-8")
        for rule in acm.RULES:
            self.assertIn("`" + rule + "`", doc, rule + " is missing from references/rule-matrix.md")

    def test_the_matrix_fixture_fires_a_rule_at_every_profile(self):
        expectation = json.loads((SKILL_ROOT / "evals" / "fixtures" / "gate-matrix"
                                  / "expected-findings.json").read_text(encoding="utf-8"))
        for profile in ("internal", "standard", "regulated"):
            self.assertEqual(expectation[profile]["exit"], 1, profile)
            self.assertGreater(len(expectation[profile]["findings"]), 40, profile)


class TestPlanNote(TempProject):
    def test_an_empty_model_reports_plan_notes_before_release(self):
        code, out, _err = self.validate(phase="plan")
        self.assertEqual(code, 0)
        self.assertIn("[plan-note]", out)
        self.assertNotIn("[plan-note]", self.validate(phase="release")[1])


if __name__ == "__main__":
    unittest.main()
