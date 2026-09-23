# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TarikAI
"""Regression tests for the false blocks (audit batch 2).

Batch 1 made the gates hard to fake. These tests hold the other side of the bargain:
an honest build must be able to pass, and an honest edit must not be refused. Each
case is something a truthful agent actually wrote and the validator actually rejected.
"""

import json
import unittest

from cli_helpers import TempProject, acm, run

CAP = "entities[user].capabilities[user.list]"


class TestProseIsNotAPlaceholder(unittest.TestCase):
    """2.1 -- words are judged one at a time, never joined across spaces."""

    def test_ordinary_prose_and_ci_names_are_not_flagged(self):
        for text in (
            "Rotation of a key completed in 2s",
            "nothing left to do",
            "Job: test-ubuntu-latest",
            "scenarios that BDD covers",
            "mastodon-sync",
            "autodoc build finished",
            "the stubborn retry policy",
            "Support of a kettle",
        ):
            self.assertIsNone(acm.placeholder_match(text), text)

    def test_identifiers_are_still_flagged(self):
        for text in (
            "gmockRepositoryImpl", "mock_repository_impl", "usemockdata", "FakeService",
            "stub_payment_gateway", "not_implemented_yet", "coming-soon", "to do: wire this",
            "NotImplementedError", "place_holder_text",
        ):
            # 'to do: wire this' is prose; only assert the identifier forms
            if " " in text:
                continue
            self.assertIsNotNone(acm.placeholder_match(text), text)

    def test_the_phrase_not_implemented_is_still_caught(self):
        self.assertIsNotNone(acm.placeholder_match("not implemented yet"))


class TestEvidenceContent(TempProject):
    """2.2 -- test idioms and log vocabulary are not placeholders."""

    profile = "regulated"

    def setUp(self):
        super().setUp()
        self.data = self.build_honest_manifest()

    def put(self, rel, body):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")

    def all_profiles_pass(self):
        for profile in ("internal", "standard", "regulated"):
            code, out, err = self.validate(profile=profile)
            self.assertEqual(code, 0, profile + "\n" + out + err)

    def test_a_spec_using_mocks_and_placeholder_queries_passes(self):
        self.put(
            "tests/authz.spec.ts",
            "jest.mock('../providers/stripe');\n"
            "test('role matrix', async () => {\n"
            "  const box = screen.getByPlaceholderText('Search');\n"
            "  expect(can('support', 'user.list', {})).toBe(false);\n"
            "  expect(box).toBeTruthy();\n"
            "});\n",
        )
        self.all_profiles_pass()

    def test_test_files_are_not_content_scanned_at_all(self):
        self.put(
            "tests/authz.spec.ts",
            "const fakeUser = mockUser(); // stub, dummy, TODO tidy\n"
            "test('role matrix', () => { expect(fakeUser).toBeDefined(); });\n",
        )
        self.all_profiles_pass()

    def test_words_like_samples_and_random_are_fine_in_a_run_log(self):
        self.put("evidence/performance.log",
                 "performance run\n  1200 samples, random seed 42, p95=412ms\n")
        self.all_profiles_pass()

    def test_prose_in_evidence_is_not_a_placeholder(self):
        self.put("evidence/security.log",
                 "security audit\n  Rotation of a key completed in 2s\n  Job: test-ubuntu-latest\n")
        self.all_profiles_pass()

    def test_a_genuine_placeholder_in_evidence_names_token_and_line(self):
        self.put("evidence/build.log", "build run\n  compiled 118 files\n  TODO: paste real output\n")
        code, out, _err = self.validate()
        self.assertEqual(code, 1)
        self.assertIn("placeholder-scan", out)
        self.assertIn("'TODO'", out)
        self.assertIn("line 3", out)

    def test_a_mock_data_binding_still_fails(self):
        self.capability_of(self.data)["dataBinding"] = "mock database"
        self.write(self.data)
        for profile in ("internal", "standard", "regulated"):
            code, out, _err = self.validate(profile=profile)
            self.assertEqual(code, 1, profile)
            self.assertIn("placeholder token 'mock'", out)

    def test_declared_static_content_path_exempts_the_file(self):
        self.put("evidence/build.log", "build run\n  mock server started for the smoke check\n")
        self.data["declaredStatic"] = [{
            "id": "static.buildlog", "path": "qualityGates[build].evidence[0]#content",
            "value": "captured log", "reason": "the smoke check really uses a mock server",
            "approvedBy": "cto", "evidence": [],
        }]
        self.write(self.data)
        code, out, err = self.validate()
        self.assertEqual(code, 0, out + err)
        self.assertNotIn("declared-static-unused", out)


class TestSetCommand(TempProject):
    """2.3 -- `set` had no tests, and the write guard could not resolve evidence."""

    def setUp(self):
        super().setUp()
        self.data = self.build_honest_manifest()

    def stored(self):
        return self.read()

    def test_a_gate_can_be_passed_with_relative_evidence_from_the_project_root(self):
        self.data["qualityGates"][0].update({"status": "pending", "evidence": []})
        self.write(self.data)
        path = "qualityGates[build]"
        code, _o, err = self.cli("set", "--path", path + ".evidence",
                                 "--value", '["evidence/build.log"]', cwd=self.root)
        self.assertEqual(code, 0, err)
        code, _o, err = self.cli("set", "--path", path + ".status", "--value", "passed",
                                 cwd=self.root)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.stored()["qualityGates"][0]["status"], "passed")

    def test_the_same_from_another_working_directory(self):
        self.data["qualityGates"][0].update({"status": "pending", "evidence": []})
        self.write(self.data)
        elsewhere = self.tmp / "elsewhere"
        elsewhere.mkdir()
        path = "qualityGates[build]"
        code, _o, err = self.cli("set", "--path", path + ".evidence",
                                 "--value", '["evidence/build.log"]', cwd=elsewhere)
        self.assertEqual(code, 0, err)
        code, _o, err = self.cli("set", "--path", path + ".status", "--value", "passed",
                                 cwd=elsewhere)
        self.assertEqual(code, 0, err)

    def test_a_passed_gate_with_missing_evidence_is_still_refused(self):
        self.data["qualityGates"][0].update({"status": "pending", "evidence": ["evidence/nope.log"]})
        self.write(self.data)
        code, _o, err = self.cli("set", "--path", "qualityGates[build].status", "--value", "passed")
        self.assertEqual(code, 2)
        self.assertIn("gate-evidence-path", err)

    def test_allow_invalid_reports_what_release_will_actually_do(self):
        self.data["qualityGates"][0].update({"status": "pending", "evidence": ["evidence/nope.log"]})
        self.write(self.data)
        code, _o, err = self.cli("set", "--path", "qualityGates[build].status", "--value", "passed",
                                 "--allow-invalid")
        self.assertEqual(code, 0, err)
        self.assertIn("still fail `validate --phase release`", err)

    def test_dotted_ids_and_nested_paths(self):
        code, out, err = self.cli("set", "--path", CAP + ".risk", "--value", "moderate")
        self.assertEqual(code, 0, err)
        self.assertIn('"low" -> "moderate"', out)
        code, _o, err = self.cli("set", "--path", CAP + ".entityStates.from", "--value", "active,inactive")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.capability_of(self.stored())["entityStates"]["from"], ["active", "inactive"])

    def test_value_types(self):
        code, _o, err = self.cli("set", "--path", "roles[ops].mfaRequired", "--value", "false")
        self.assertEqual(code, 0, err)
        self.assertIs(self.stored()["roles"][0]["mfaRequired"], False)
        code, _o, err = self.cli("set", "--path", CAP + ".outcome", "--value", "Find an account fast")
        self.assertEqual(code, 0, err)
        code, _o, err = self.cli("set", "--path", CAP + ".serverOperations",
                                 "--value", '["A.b", "C.d"]')
        self.assertEqual(code, 0, err)
        self.assertEqual(self.capability_of(self.stored())["serverOperations"], ["A.b", "C.d"])
        code, _o, err = self.cli("set", "--path", "roles[ops].mfaRequired", "--value", "maybe")
        self.assertEqual(code, 2)
        self.assertIn("boolean", err)

    def test_unresolvable_paths_are_usage_errors(self):
        for path in ("entities[nope].name", "roles[ops]", "nothing.here", "roles[ops]..name"):
            code, _o, err = self.cli("set", "--path", path, "--value", "x")
            self.assertEqual(code, 2, path)
            self.assertNotIn("Traceback", err)


class TestReviewerWarning(TempProject):
    """2.6 -- at standard an empty reviewer passes; the write must at least say so."""

    profile = "standard"

    def test_setting_reviewed_without_a_reviewer_warns(self):
        self.build_honest_manifest()
        data = self.read()
        cap = self.capability_of(data)
        cap.update({"reviewStatus": "unreviewed", "reviewedBy": ""})
        self.write(data)
        code, _o, err = self.cli("set", "--path", CAP + ".reviewStatus", "--value", "reviewed")
        self.assertEqual(code, 0, err)
        self.assertIn("reviewer-identity", err)
        self.assertIn("WARN", err)


class TestHeadlessCapabilities(TempProject):
    """2.7 -- an API-only, CLI or chat capability must be able to reach `implemented`."""

    profile = "regulated"

    def setUp(self):
        super().setUp()
        self.data = self.build_honest_manifest()
        (self.root / "tests" / "api.spec.ts").write_text(
            "test('user.api lists accounts over the admin API', async () => {\n"
            "  expect((await api.get('/admin/api/users')).status).toBe(200);\n"
            "});\n", encoding="utf-8")
        self.api = {
            "id": "user.api", "outcome": "List accounts over the admin API", "kind": "query",
            "roles": ["ops"], "risk": "low", "status": "implemented", "rationale": "",
            "entityStates": {"from": ["active"], "to": []}, "surface": ["api"],
            "uiRoutes": [], "entrypoints": ["GET /admin/api/users"],
            "serverOperations": ["UserApi.list"], "authorizationPolicies": ["policy/user.admin"],
            "auditEvents": [], "safeguards": [], "dataBinding": "postgres:users via UserApi.list",
            "tests": ["tests/api.spec.ts"], "policyTests": ["tests/authz.spec.ts"],
            "evidence": ["evidence/tests.log"], "owner": "impl", "implementedBy": "impl",
            "reviewStatus": "reviewed", "reviewedBy": "qa",
        }
        self.data["entities"][0]["capabilities"].append(self.api)

    def test_an_api_only_capability_passes_release(self):
        self.write(self.data)
        code, out, err = self.validate()
        self.assertEqual(code, 0, out + err)

    def test_it_needs_entrypoints_and_policy_tests(self):
        for key in ("entrypoints", "policyTests"):
            saved = self.api[key]
            self.api[key] = []
            self.write(self.data)
            code, out, _err = self.validate()
            self.assertEqual(code, 1, key)
            self.assertIn(key, out)
            self.api[key] = saved

    def test_a_ui_capability_without_a_route_still_fails(self):
        self.api["surface"] = ["ui"]
        self.write(self.data)
        code, out, _err = self.validate()
        self.assertEqual(code, 1)
        self.assertIn("uiRoutes", out)

    def test_an_unknown_surface_is_an_enum_error(self):
        self.api["surface"] = ["telepathy"]
        self.write(self.data)
        code, out, _err = self.validate()
        self.assertEqual(code, 1)
        self.assertIn("enum-invalid", out)

    def test_a_headless_console_needs_no_screen(self):
        self.data["screens"] = []
        self.data["entities"][0]["capabilities"] = [self.api]
        for section in ("experience",):
            pass
        self.write(self.data)
        _code, out, _err = self.validate()
        self.assertNotIn("release requires at least one connected screen", out)


class TestNotApplicableCapabilities(TempProject):
    """2.8 -- recording a rejected catalog candidate must not block release."""

    profile = "regulated"

    def test_a_not_applicable_query_needs_only_a_rationale(self):
        data = self.build_honest_manifest()
        data["entities"][0]["capabilities"].append({
            "id": "user.legacy", "outcome": "Legacy report", "kind": "query", "roles": [],
            "risk": "low", "status": "not-applicable", "rationale": "the platform has no legacy reports",
            "entityStates": {"from": [], "to": []}, "uiRoutes": [], "serverOperations": [],
            "authorizationPolicies": [], "auditEvents": [], "safeguards": [], "dataBinding": "",
            "tests": [], "evidence": [],
        })
        self.write(data)
        code, out, err = self.validate()
        self.assertEqual(code, 0, out + err)
        self.assertNotIn("capability-no-role", out)

    def test_a_not_applicable_capability_still_needs_its_rationale(self):
        data = self.build_honest_manifest()
        data["entities"][0]["capabilities"].append({
            "id": "user.legacy", "outcome": "Legacy report", "kind": "query", "roles": [],
            "risk": "low", "status": "not-applicable", "rationale": "",
            "entityStates": {"from": [], "to": []}, "uiRoutes": [], "serverOperations": [],
            "authorizationPolicies": [], "auditEvents": [], "safeguards": [], "dataBinding": "",
            "tests": [], "evidence": [],
        })
        self.write(data)
        _code, out, _err = self.validate()
        self.assertIn("rationale-required", out)


class TestScreensAreAddedIncrementally(TempProject):
    """2.10 -- the architect adds the screen, the ux-reviewer adds its states."""

    def test_a_bare_screen_can_be_added_and_release_still_requires_states(self):
        role = {"id": "ops", "name": "Ops", "responsibilities": ["Admin"], "scopes": ["all"]}
        code, _o, err = run("add", "--manifest", self.manifest, "--kind", "role",
                            "--json", json.dumps(role))
        self.assertEqual(code, 0, err)
        screen = {"id": "users", "route": "/admin/users", "purpose": "Search accounts",
                  "roles": ["ops"]}
        code, _o, err = run("add", "--manifest", self.manifest, "--kind", "screen",
                            "--json", json.dumps(screen))
        self.assertEqual(code, 0, err)
        self.assertIn("screen-incomplete", err)
        _code, out, _err = self.validate(phase="release")
        self.assertIn("ERROR: [screen-incomplete]", out)


if __name__ == "__main__":
    unittest.main()
