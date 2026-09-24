# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TarikAI
"""Round-2 regressions: the profile and project-root rules, headless consoles, monorepo
layouts and closed readers.

Each test pins a defect an independent verification reproduced on the first round
of fixes. Rules that the gate-matrix fixture cannot fire (its profile is invalid on
purpose) are pinned here and listed in tests/test_rule_coverage.py.
"""

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

from cli_helpers import SCRIPT, TempProject, acm, run

PROFILES = ("internal", "standard", "regulated")


class RoundTwoCase(TempProject):
    profile = "regulated"

    def setUp(self):
        super().setUp()
        self.data = self.build_honest_manifest()

    def findings(self, profile="regulated", phase="release"):
        result = acm.validate_manifest(self.data, phase, self.root, profile, manifest_dir=self.manifest)
        return {item["rule"]: item["severity"] for item in result.items}


class TestProfileDecision(RoundTwoCase):
    def test_a_profile_changed_without_its_decision_is_refused(self):
        self.data["profile"] = "internal"
        self.write(self.data)
        code, out, _err = self.validate()
        self.assertEqual(code, 1)
        self.assertIn("[profile-decision-mismatch]", out)

    def test_set_profile_updates_the_decision_and_warns_when_lowering(self):
        code, _out, err = self.cli("set", "--path", "profile", "--value", "internal")
        self.assertEqual(code, 0, err)
        self.assertIn("lowered from regulated to internal", err)
        decision = [d for d in self.read()["decisions"] if d["id"] == "dec.profile"][0]
        self.assertEqual(decision["decision"], "Run at the internal profile")
        self.assertEqual(decision["status"], "assumed")
        self.assertNotIn("profile-decision-mismatch", self.rules())

    def test_a_removed_profile_decision_blocks_regulated(self):
        self.data["decisions"] = [d for d in self.data["decisions"] if d["id"] != "dec.profile"]
        self.write(self.data)
        code, out, _err = self.validate()
        self.assertEqual(code, 1)
        self.assertIn("[profile-decision-missing]", out)
        self.assertEqual(acm.RULES["profile-decision-missing"], acm.STRICT_ONLY)

    def test_regulated_data_rules_out_the_internal_profile(self):
        self.data["platform"]["regulatedData"] = ["billing"]
        self.data["profile"] = "internal"
        for decision in self.data["decisions"]:
            if decision["id"] == "dec.profile":
                decision["decision"] = "Run at the internal profile"
        self.write(self.data)
        code, out, _err = self.validate()
        self.assertEqual(code, 1)
        self.assertIn("[profile-below-regulated-data]", out)


class TestRequiredGates(RoundTwoCase):
    def set_gate(self, gid, **fields):
        for gate in self.data["qualityGates"]:
            if gate["id"] == gid:
                gate.update(fields)

    def test_tests_and_security_can_never_be_not_applicable(self):
        for gid in ("tests", "security"):
            self.set_gate(gid, status="not-applicable", rationale="covered by the platform CI", evidence=[])
        self.data["decisions"].append({
            "id": "dec.gates", "kind": "not-applicable", "decision": "Skip them",
            "reason": "platform CI", "evidence": [], "status": "confirmed",
            "appliesTo": ["tests", "security"],
        })
        self.assertEqual(self.findings("standard").get("gate-always-required"), "error")

    def test_other_required_gates_need_a_not_applicable_decision(self):
        self.set_gate("typecheck", status="not-applicable", rationale="Ruby app", evidence=[])
        self.assertEqual(self.findings().get("gate-not-applicable-undecided"), "error")
        self.data["decisions"].append({
            "id": "dec.typecheck", "kind": "not-applicable", "decision": "No type checker",
            "reason": "Ruby without Sorbet", "evidence": [], "status": "confirmed",
            "appliesTo": ["typecheck"],
        })
        self.assertNotIn("gate-not-applicable-undecided", self.findings())
        self.assertNotIn("decision-applies-to-unknown", self.findings())


class TestProjectRoot(RoundTwoCase):
    def test_an_absolute_project_root_is_refused(self):
        self.data["projectRoot"] = str(self.root.resolve())
        self.write(self.data)
        code, out, _err = self.validate()
        self.assertEqual(code, 1)
        self.assertIn("[project-root-outside]", out)

    def test_a_project_root_outside_the_repository_is_refused(self):
        (self.root / ".git").mkdir()
        elsewhere = self.tmp / "elsewhere"
        elsewhere.mkdir()
        self.data["projectRoot"] = "../../elsewhere"
        self.write(self.data)
        _code, out, _err = run("validate", "--manifest", self.manifest, "--phase", "release")
        self.assertIn("[project-root-outside]", out)

    def test_the_default_project_root_is_fine(self):
        (self.root / ".git").mkdir()
        self.assertNotIn("project-root-outside", self.findings())


class TestHeadlessConsole(RoundTwoCase):
    """R6: an API-only role, or a console with no screens at all, must be able to pass."""

    def make_api_capability(self, cid="user.suspend", role="automation"):
        return {
            "id": cid, "outcome": "Suspend an account from the ops CLI", "kind": "command",
            "roles": [role], "risk": "moderate", "status": "implemented", "rationale": "",
            "entityStates": {"from": ["active"], "to": ["active"]}, "uiRoutes": [],
            "serverOperations": ["UserService.suspend"], "authorizationPolicies": ["policy/user.suspend"],
            "auditEvents": ["user.suspended"], "safeguards": ["reason required"],
            "dataBinding": "postgres:users via UserService.suspend", "idempotency": "request id",
            "tests": ["tests/suspend.spec.ts"], "evidence": ["evidence/tests.log"],
            "owner": "impl", "implementedBy": "impl", "reviewStatus": "reviewed", "reviewedBy": "qa",
            "surface": ["cli"], "entrypoints": ["admin users suspend <id>"],
            "policyTests": ["tests/authz.spec.ts"],
        }

    def write_suspend_test(self):
        (self.root / "tests" / "suspend.spec.ts").write_text(
            "test('user.suspend via admin users suspend', async () => {\n"
            "  expect(await suspend('u-1', 'abuse')).toEqual({ status: 'suspended' });\n"
            "});\n", encoding="utf-8")

    def test_an_api_only_role_in_a_ui_console_passes(self):
        self.write_suspend_test()
        self.data["roles"].append({"id": "automation", "name": "Automation", "responsibilities": ["Bulk ops"],
                                   "scopes": ["all accounts"], "mfaRequired": True, "separationOfDuties": []})
        self.data["entities"][0]["capabilities"].append(self.make_api_capability())
        self.write(self.data)
        for profile in PROFILES:
            code, out, err = self.validate(profile=profile)
            self.assertEqual(code, 0, profile + "\n" + out + err)

    def test_a_console_with_no_screens_at_all_passes(self):
        self.write_suspend_test()
        query = self.data["entities"][0]["capabilities"][0]
        query.update({"surface": ["api"], "uiRoutes": [], "entrypoints": ["GET /admin/api/users"],
                      "policyTests": ["tests/authz.spec.ts"]})
        self.data["entities"][0]["capabilities"].append(self.make_api_capability(role="ops"))
        self.data["screens"] = []
        for gate in self.data["qualityGates"]:
            if gate["id"] in ("browser", "accessibility"):
                gate.update({"status": "not-applicable", "rationale": "no interface", "evidence": []})
        experience = self.data["crossCutting"]["experience"]
        experience["keyboardCriticalFlowsTested"] = False
        experience["responsiveCriticalFlowsTested"] = False
        experience["evidence"] = []
        self.write(self.data)
        for profile in PROFILES:
            code, out, err = self.validate(profile=profile)
            self.assertEqual(code, 0, profile + "\n" + out + err)


class TestMonorepoLayout(unittest.TestCase):
    """R5: a manifest placed beside its app must accept the app's evidence."""

    def setUp(self):
        import shutil
        import tempfile
        self.tmp = Path(tempfile.mkdtemp(prefix="adminwright-mono-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_init_manifest_beside_the_app_accepts_its_tests(self):
        app = self.tmp / "apps" / "admin"
        (app / "tests").mkdir(parents=True)
        (app / "evidence").mkdir()
        (app / "tests" / "user.spec.ts").write_text(
            "test('user.list', () => { expect(list()).toHaveLength(3); });\n", encoding="utf-8")
        (app / "evidence" / "tests.log").write_text("vitest run\n  Tests  6 passed (6)\n", encoding="utf-8")
        (app / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
        manifest = app / "console.json"
        code, out, err = run("init", "--project-root", self.tmp, "--manifest", manifest, "--name", "Mono")
        self.assertEqual(code, 0, err)
        ignore = (app / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("node_modules/", ignore, "an existing .gitignore is extended, not replaced")
        self.assertIn("/locks/", ignore)
        self.assertNotIn("\nlocks/", ignore, "patterns are anchored to the manifest's folder")
        in_dir = acm.inside_manifest_dir
        self.assertFalse(in_dir(app / "tests" / "user.spec.ts", app, manifest))
        self.assertFalse(in_dir(app / "evidence" / "tests.log", app, manifest))
        self.assertTrue(in_dir(manifest, app, manifest))
        self.assertTrue(in_dir(app / "locks" / "x.lock", app, manifest))
        self.assertTrue(in_dir(app / "ocr-record-1.json", app, manifest))


class TestClosedReaders(TempProject):
    """A reader that leaves early (`| head -1`) is the end of output, not a failure."""

    def test_a_closed_pipe_leaves_the_exit_code_alone(self):
        self.build_honest_manifest()
        for fmt in ("gap-report", "operator-handbook"):
            proc = subprocess.Popen(
                [sys.executable, str(SCRIPT), "emit", "--manifest", str(self.manifest), "--format", fmt],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                env=dict(os.environ, PYTHONIOENCODING="cp1252"),
            )
            proc.stdout.read(1)
            proc.stdout.close()
            self.assertEqual(proc.wait(timeout=60), 0, fmt)


class TestMalformedInputs(TempProject):
    def test_a_bom_followed_by_invalid_bytes_is_a_usage_error(self):
        self.manifest.write_bytes(b"\xef\xbb\xbf{\"a\": \"\xfc\"}")
        code, _out, err = self.validate(phase="plan")
        self.assertEqual(code, 2)
        self.assertNotIn("Traceback", err)
        self.assertIn("not valid UTF-8", err)

    def test_report_survives_non_string_archetypes(self):
        data = self.read()
        data["platform"]["archetypes"] = [7, "b2b-saas"]
        self.write(data)
        code, out, err = self.cli("report")
        self.assertEqual(code, 0, err)
        self.assertIn("7, b2b-saas", out)

    def test_adding_to_a_broken_capability_list_is_a_usage_error(self):
        data = self.read()
        data["entities"] = [{"id": "user", "name": "User", "sourceOfTruth": "db", "sensitivity": "internal",
                             "tenantScoped": False, "lifecycleStates": [], "retention": "1y",
                             "capabilities": "oops"}]
        self.write(data)
        code, _out, err = self.cli("add", "--kind", "capability", "--entity", "user",
                                   "--json", json.dumps({"id": "user.list"}))
        self.assertEqual(code, 2)
        self.assertNotIn("Traceback", err)

    def test_claiming_with_a_broken_agents_value_is_a_usage_error(self):
        data = self.build_honest_manifest()
        data["agents"] = {}
        self.write(data)
        code, _out, err = self.cli("claim", "--agent", "a1", "--role", "implementer", "--capability", "user.list")
        self.assertEqual(code, 2)
        self.assertNotIn("Traceback", err)


if __name__ == "__main__":
    unittest.main()
