# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TarikAI
"""Regression tests for the false blocks (audit batch 2).

Batch 1 made the gates hard to fake. These tests hold the other side of the bargain:
an honest build must be able to pass, and an honest edit must not be refused. Each
case is something a truthful agent actually wrote and the validator actually rejected.
"""

import json
import unittest
from pathlib import Path

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


class TestSampleAndRandomAreVocabulary(TempProject):
    """'sample' and 'random' name real things in audio, ML, lab and statistics work.

    An audio console's real table `public.cry_samples` was refused in `sourceOfTruth`,
    which cannot be declared static, so the only way through was renaming the domain.
    The words still fail where they mark fabricated data: modifying the noun after them
    ("sample data", "SAMPLE_USERS") or calling a number generator ("Math.random()").
    """

    profile = "regulated"

    def setUp(self):
        super().setUp()
        self.data = self.build_honest_manifest()

    def refused(self, token):
        code, out, _err = self.validate()
        self.assertEqual(code, 1, out)
        self.assertIn("placeholder token '" + token + "'", out)

    def test_head_nouns_and_technical_compounds_are_not_placeholders(self):
        for text in (
            "postgres:public.cry_samples", "See misclassified cry samples",
            "audio_clips.sample_rate", "cry_sample_id", "CrySampleRepository.findForReview",
            "a random sample of refunds for review", "random forest classifier",
            "chosen at random", "random seed 42", "crypto.randomUUID()",
            "crypto.getRandomValues(buffer)",
        ):
            self.assertIsNone(acm.placeholder_match(text), text)

    def test_fabricated_data_is_still_a_placeholder(self):
        for text in (
            "sample data from fixtures", "getSampleData", "SAMPLE_USERS", "sample-orders.json",
            "randomMetrics()", "random values per tenant", "randomly generated users",
            "Math.random() series", "np.random.rand(30)", "random.randint(1, 9)",
        ):
            self.assertIsNotNone(acm.placeholder_match(text), text)
        self.assertEqual(acm.placeholder_match("sample data from fixtures"), "sample data")
        self.assertEqual(acm.placeholder_match("Math.random() series"), "random")

    def test_a_real_samples_table_passes_release_at_every_profile(self):
        self.data["entities"][0]["sourceOfTruth"] = "postgres:public.cry_samples"
        self.write(self.data)
        for profile in ("internal", "standard", "regulated"):
            code, out, err = self.validate(profile=profile)
            self.assertEqual(code, 0, profile + "\n" + out + err)

    def test_a_cited_test_with_mocks_and_samples_passes(self):
        (self.root / "tests" / "user.spec.ts").write_text(
            "jest.mock('../audio/recorder');\n"
            "test('user.list via /admin/users', async () => {\n"
            "  const fixture = { samples: [0.1, 0.2] };\n"
            "  expect((await listUsers({tenantId: 't-1'})).length).toBe(3);\n"
            "  expect(fixture.samples).toHaveLength(2);\n"
            "});\n", encoding="utf-8")
        code, out, err = self.validate()
        self.assertEqual(code, 0, out + err)

    def test_a_sample_data_binding_still_fails(self):
        self.capability_of(self.data)["dataBinding"] = "sample data from fixtures"
        self.write(self.data)
        self.refused("sample data")

    def test_a_random_number_binding_still_fails(self):
        self.capability_of(self.data)["dataBinding"] = "Math.random() series"
        self.write(self.data)
        self.refused("random")

    def test_the_strict_words_are_unchanged(self):
        for where, value, token in (
            ("binding", "mockRepository", "mock"),
            ("source", "fake table", "fake"),
            ("screen", "MOCK_ROWS", "MOCK"),
        ):
            data = json.loads(json.dumps(self.data))
            if where == "binding":
                self.capability_of(data)["dataBinding"] = value
            elif where == "source":
                data["entities"][0]["sourceOfTruth"] = value
            else:
                data["screens"][0]["dataSources"] = [value]
            self.write(data)
            self.refused(token)

    def test_a_defect_described_in_source_of_truth_still_fails(self):
        # A regex cannot tell an insecure-but-real generator from a fabricated series.
        # sourceOfTruth names where the data lives; the defect belongs in gaps[].
        self.data["entities"][0]["sourceOfTruth"] = "token generated with Math.random() (defect)"
        self.write(self.data)
        self.refused("random")


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
        self.assertNotIn("screen-incomplete", err, "an unfinished screen is not a warning to echo")
        _code, out, _err = self.validate(phase="release")
        self.assertIn("ERROR: [screen-incomplete]", out)
        _code, out, _err = self.validate(phase="plan")
        self.assertIn("WARN: [screen-incomplete]", out)


USER_SPEC = (
    "test('user.list via /admin/users', async () => {\n"
    "  expect((await listUsers({tenantId: 't-1'})).length).toBe(3);\n"
    "});\n"
)


class FalseBlockCase(TempProject):
    """The honest console, judged in process, at release."""

    profile = "regulated"

    def setUp(self):
        super().setUp()
        self.data = self.build_honest_manifest()
        self.cap = self.capability_of(self.data)

    def put(self, rel, body):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")

    def result(self, profile="regulated"):
        return acm.validate_manifest(self.data, "release", self.root, profile,
                                     manifest_dir=self.manifest.parent)

    def errors(self, profile="regulated"):
        return {item["rule"] for item in self.result(profile).errors}

    def rule_names(self, profile="regulated"):
        return {item["rule"] for item in self.result(profile).items}


class TestTestPathsAreNotScanned(FalseBlockCase):
    """R-15 -- a test file's path is never placeholder-scanned; its content never was."""

    def test_honest_test_names_with_mock_or_fake_pass(self):
        self.put("src/test/java/UserControllerMockMvcTest.java",
                 "class UserControllerMockMvcTest {\n  @Test\n  void userList() {\n"
                 "    assertEquals(3, controller.list(\"user.list\").size());\n  }\n}\n")
        self.put("tests/user-fake-clock.spec.ts", USER_SPEC)
        self.put("tests/fakes/authz.spec.ts",
                 "test('role matrix', () => {\n  expect(can('ops', 'user.list', {})).toBe(true);\n});\n")
        self.cap["tests"] = ["tests/user-fake-clock.spec.ts",
                             "src/test/java/UserControllerMockMvcTest.java"]
        self.data["screens"][0]["tests"] = ["tests/user-fake-clock.spec.ts"]
        self.data["crossCutting"]["authorization"]["policyTests"] = ["tests/fakes/authz.spec.ts"]
        for profile in ("internal", "standard", "regulated"):
            self.assertEqual(self.errors(profile), set(), profile)

    def test_a_mock_binding_is_still_caught(self):
        self.cap["dataBinding"] = "mockUserRepository.findAll"
        self.assertIn("placeholder-scan", self.errors("internal"))


class TestImplementationFieldsAreScanned(FalseBlockCase):
    """R-11 -- the fields that say where data comes from are scanned like their neighbours."""

    def test_queue_source_integration_operations_and_command_prose(self):
        self.data["workQueues"] = [{
            "id": "refunds", "purpose": "Review refunds", "roles": ["ops"],
            "source": "mockRefundQueue.fakeItems", "priorityRule": "oldest first", "sla": "4h",
            "actions": ["approve"], "status": "planned", "rationale": "", "tests": [],
        }]
        self.data["integrations"] = [{
            "id": "stripe", "direction": "outbound", "sourceOfTruth": "Stripe",
            "operations": ["stubStripeClient.cancel", "fakeInvoices.read"],
            "failureHandling": "retry", "reconciliation": "nightly", "monitoring": "alerts",
            "status": "planned", "rationale": "", "tests": [],
        }]
        self.cap.update({"idempotency": "TODO: mock idempotency key",
                         "recovery": "placeholder - not implemented", "concurrency": "fake lock"})
        paths = {item["path"] for item in self.result("internal").errors
                 if item["rule"] == "placeholder-scan"}
        for expected in ("workQueues[refunds].source", "integrations[stripe].operations[0]",
                         "integrations[stripe].operations[1]", "idempotency", "recovery",
                         "concurrency"):
            self.assertTrue(any(path.endswith(expected) for path in paths), (expected, paths))

    def test_spanish_todo_in_command_prose_is_a_word(self):
        self.cap["recovery"] = "restaurar todo el registro desde la copia de seguridad"
        self.assertNotIn("placeholder-scan", self.rule_names("internal"))


PYTEST_VERBOSE = (
    "$ pytest -v\n"
    "============ test session starts ============\n"
    "tests/test_auth.py::test_rejects_fake_session_token PASSED [ 50%]\n"
    "tests/test_users.py::test_user_list_scoped PASSED [100%]\n"
    "============ 2 passed in 0.31s ============\n\n# exit code: 0\n"
)


class TestVerboseTestLogs(FalseBlockCase):
    """R-16 -- a test's title in run output is its name, not data an operator sees."""

    def test_verbose_reporters_naming_fake_tests_pass(self):
        for body in (
            PYTEST_VERBOSE,
            "$ vitest run --reporter=verbose\n ✓ tests/auth.spec.ts > rejects a fake session token 3ms\n"
            " × tests/auth.spec.ts > uses a stub gateway 2ms\n Tests 6 passed (6)\n",
            "$ go test -v ./...\n=== RUN   TestRejectsFakeToken\n--- PASS: TestRejectsFakeToken (0.00s)\n"
            "ok  \texample.com/admin\t0.01s\n",
            "$ npx playwright test --reporter=list\n  ✓  1 [chromium] › auth.spec.ts:3:5 › "
            "rejects a mock session (1.2s)\n  6 passed (12.4s)\n",
            "$ node --test\nok 1 - rejects a fake session token\n# pass 6\n",
        ):
            self.put("evidence/tests.log", body)
            self.assertNotIn("placeholder-scan", self.rule_names("regulated"), body)

    def test_output_that_names_a_mock_source_is_still_flagged(self):
        self.put("evidence/tests.log", "$ pytest -v\nrendered 12 rows from mockUsers\n\n# exit code: 0\n")
        self.assertIn("placeholder-scan", self.errors("internal"))

    def test_one_registration_covers_every_citation_of_a_log(self):
        self.put("evidence/tests.log", "vitest run\n  mock server started for the smoke check\n"
                                       "  Tests 6 passed (6)\n")
        found = [item for item in self.result().errors if item["rule"] == "placeholder-scan"]
        self.assertEqual(len(found), 2, found)  # the capability's evidence and the tests gate
        for path in ("evidence/tests.log#content", "qualityGates[tests].evidence[0]#content"):
            self.data["declaredStatic"] = [{
                "id": "static.testslog", "path": path, "value": "captured log",
                "reason": "the smoke check really starts a mock server", "approvedBy": "cto",
                "evidence": [],
            }]
            names = self.rule_names("regulated")
            self.assertNotIn("placeholder-scan", names, path)
            self.assertNotIn("declared-static-unused", names, path)


class TestTestShapes(FalseBlockCase):
    """R-17, R-18 -- support folders and Deno tests."""

    def test_a_spec_under_a_support_domain_folder_is_a_test(self):
        self.put("tests/support/user.spec.ts", USER_SPEC)
        self.cap["tests"] = ["tests/support/user.spec.ts"]
        self.data["screens"][0]["tests"] = ["tests/support/user.spec.ts"]
        self.assertEqual(self.errors("regulated"), set())

    def test_support_helpers_are_still_not_tests(self):
        shape = acm.looks_like_test_file
        body = "describe('commands', () => { expect(1).toBe(cy.x()); });\n"
        self.assertFalse(shape(Path("cypress/support/commands.ts"), body))
        self.assertFalse(shape(Path("spec/support/helpers.rb"), "describe 'x' do\nend\n"))
        self.assertTrue(shape(Path("tests/support/tickets.spec.ts"), body))

    def test_a_deno_test_is_a_test(self):
        self.put("tests/user_test.ts",
                 "import { assertEquals } from 'jsr:@std/assert';\n"
                 "Deno.test('user.list returns the tenant users', async () => {\n"
                 "  const res = await listUsers();\n  assertEquals(res.length, 2);\n});\n")
        self.cap["tests"] = ["tests/user_test.ts"]
        self.assertEqual(self.errors("regulated"), set())
        self.assertTrue(acm.looks_like_test_file(
            Path("tests/x_test.ts"), "Deno.test.ignore('x', () => { assertEquals(f(), 1); });\n"))


class TestEvidenceNames(FalseBlockCase):
    """R-19, R-20 -- an evidence file is judged with its folder, and on what it says."""

    def test_licence_scans_and_written_reviews_in_evidence_are_evidence(self):
        self.put("evidence/license-check.log",
                 "$ license-checker --summary\n├─ MIT: 412\n└─ Apache-2.0: 37\n")
        self.put("evidence/security.md", "Security review 2026-11-17\n- 14 endpoints, 0 findings\n")
        for gate in self.data["qualityGates"]:
            if gate["id"] == "security":
                gate["evidence"] = ["evidence/security.log", "evidence/license-check.log",
                                    "evidence/security.md"]
        self.assertEqual(self.errors("regulated"), set())

    def test_project_documents_are_still_refused(self):
        reason = acm.non_evidence_reason
        for relative in ("README.md", "LICENSE", "docs/CHANGELOG.md", "SECURITY.md",
                         "package.json", "evidence/package.json"):
            self.assertNotEqual(reason(Path(relative), "gate", relative), "", relative)

    def test_specific_one_line_evidence_is_informative(self):
        vague = acm.is_uninformative
        self.assertFalse(vague("axe-core 4.10: 0 violations across 14 admin routes, checked at 320px and 1440px"))
        self.assertFalse(vague("Audit sweep: 18/18 privileged mutations verified with actor, target, reason, correlationId"))
        for text in ("tests passed", "done, all good", "command completed, exit 0", "OK"):
            self.assertTrue(vague(text), text)


class TestDigitLookalikes(unittest.TestCase):
    """R-23 -- a digit standing in for a letter inside a word does not hide a placeholder."""

    def test_leetspeak_is_caught_and_real_digits_are_not(self):
        for text in ("postgres:tenants via M0ckTenantRepository.findAll", "F4keTenantService.list",
                     "stubb3dQuery"):
            self.assertIsNotNone(acm.placeholder_match(text), text)
        for text in ("s3://bucket/tenants", "PostgreSQL 16", "k8s job runner", "v1/tenants",
                     "ipv4 allow-list", "oauth2 client", "base64 payload", "x509 certificate"):
            self.assertIsNone(acm.placeholder_match(text), text)


if __name__ == "__main__":
    unittest.main()
