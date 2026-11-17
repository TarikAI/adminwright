# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TarikAI
"""Regression tests for the release-gate bypasses (audit batch 1).

The plugin description promises "a process an agent can't fake its way through".
Each test here takes the honest one-capability console from
`TempProject.build_honest_manifest`, breaks exactly one thing, and asserts that
`validate --phase release` now refuses it. Deleting a test re-opens a hole that
was reproduced at `--profile regulated` with exit 0 and zero warnings.
"""

import json
import unittest
from pathlib import Path

from cli_helpers import TempProject, acm, run

PROFILES = ("internal", "standard", "regulated")
CAP = "entities[user].capabilities[user.list]"


class ReleaseGateCase(TempProject):
    profile = "regulated"

    def setUp(self):
        super().setUp()
        self.data = self.build_honest_manifest()
        self.cap = self.capability_of(self.data)

    def result(self, profile="regulated", phase="release"):
        return acm.validate_manifest(
            self.data, phase, self.root, profile, manifest_dir=self.manifest.parent
        )

    def rule_names(self, profile="regulated", phase="release"):
        return {item["rule"] for item in self.result(profile, phase).items}

    def errors(self, profile="regulated"):
        return {item["rule"] for item in self.result(profile).errors}

    def save(self):
        self.write(self.data)

    def write_file(self, rel, body):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")

    def add_command(self, cid="user.suspend", kind="command", risk="high"):
        """A second, honest, implemented capability that mutates."""
        self.write_file(
            "tests/" + cid + ".spec.ts",
            "test('" + cid + " suspends an account', async () => {\n"
            "  expect((await suspendUser('u-1')).status).toBe('suspended');\n"
            "});\n")
        capability = {
            "id": cid, "outcome": "Suspend an account", "kind": kind, "roles": ["ops"],
            "risk": risk, "status": "implemented", "rationale": "",
            "entityStates": {"from": ["active"], "to": ["active"]},
            "uiRoutes": ["/admin/users"], "serverOperations": ["UserService.suspend"],
            "authorizationPolicies": ["policy/user.suspend"], "auditEvents": ["user.suspended"],
            "safeguards": ["reason capture"], "recovery": "user.restore reverses it",
            "idempotency": "state-conditional; a repeat is a no-op",
            "dataBinding": "postgres:users via UserService.suspend",
            "tests": ["tests/" + cid + ".spec.ts"], "evidence": ["evidence/tests.log"],
            "owner": "impl", "implementedBy": "impl", "reviewStatus": "reviewed",
            "reviewedBy": "qa",
        }
        self.data["entities"][0]["capabilities"].append(capability)
        self.data["screens"][0]["capabilities"].append(cid)
        return capability


class TestBaseline(ReleaseGateCase):
    def test_honest_console_passes_release_at_every_profile(self):
        self.save()
        for profile in PROFILES:
            code, out, err = self.validate(profile=profile)
            self.assertEqual(code, 0, profile + "\n" + out + err)
            self.assertNotIn("WARN", out, profile)


class TestNonListEvidence(ReleaseGateCase):
    """1.1 -- a non-list value used to skip every evidence check."""

    BAD_VALUES = ("x", True, {"path": "x"}, [1], [["x"]])

    def setUp(self):
        super().setUp()
        self.data["gaps"] = [{
            "id": "gap.one", "severity": "low", "description": "d", "status": "fixed",
            "rationale": "r", "evidence": ["evidence/tests.log"],
        }]
        self.data["declaredStatic"] = [{
            "id": "static.one", "path": CAP + ".safeguards", "value": "v", "reason": "r",
            "approvedBy": "cto", "evidence": ["evidence/tests.log"],
        }]

    def targets(self):
        cross = self.data["crossCutting"]
        yield "capability.tests", self.cap, "tests"
        yield "capability.evidence", self.cap, "evidence"
        for gate in self.data["qualityGates"]:
            yield "gate " + gate["id"], gate, "evidence"
        for section, values in cross.items():
            yield "crossCutting." + section, values, "evidence"
        yield "authorization.policyTests", cross["authorization"], "policyTests"
        yield "gaps[].evidence", self.data["gaps"][0], "evidence"
        yield "declaredStatic[].evidence", self.data["declaredStatic"][0], "evidence"
        yield "screens[].tests", self.data["screens"][0], "tests"

    def test_every_evidence_field_rejects_a_non_list_of_strings(self):
        for label, holder, key in self.targets():
            original = holder[key]
            for bad in self.BAD_VALUES:
                holder[key] = bad
                for profile in PROFILES:
                    found = {i["rule"] for i in self.result(profile).errors}
                    self.assertIn(
                        "manifest-type", found,
                        "%s = %r must be rejected at %s" % (label, bad, profile),
                    )
            holder[key] = original

    def test_manifest_type_is_an_error_at_every_profile(self):
        self.assertEqual(acm.RULES["manifest-type"], acm.E3)

    def test_release_exit_code_for_a_string_tests_field(self):
        self.cap["tests"] = "tests/user.spec.ts"
        self.save()
        for profile in PROFILES:
            code, out, _err = self.validate(profile=profile)
            self.assertEqual(code, 1, profile)
            self.assertIn("manifest-type", out)

    def test_true_as_gate_evidence_is_refused_at_release(self):
        self.data["qualityGates"][0]["evidence"] = True
        self.save()
        code, out, _err = self.validate()
        self.assertEqual(code, 1)
        self.assertIn("manifest-type", out)

    def test_write_guard_refuses_a_non_list_evidence_value(self):
        self.save()
        code, _out, err = run(
            "add", "--manifest", self.manifest, "--kind", "gap", "--json",
            json.dumps({"id": "gap.two", "evidence": "x"}),
        )
        self.assertEqual(code, 2)
        self.assertIn("manifest-type", err)


class TestDeclaredStatic(ReleaseGateCase):
    """1.2 -- declaredStatic may exempt a value, never a claim about implementation."""

    def register(self, path, approver="cto"):
        self.data["declaredStatic"] = [{
            "id": "static.one", "path": path, "value": "mock database", "reason": "demo",
            "approvedBy": approver, "evidence": [],
        }]

    def test_a_mock_data_binding_cannot_be_declared_static(self):
        self.cap["dataBinding"] = "mockTenants via MockTenantRepository.findAll"
        self.register(CAP + ".dataBinding")
        self.save()
        code, out, _err = self.validate()
        self.assertEqual(code, 1)
        self.assertIn("declared-static-forbidden-path", out)
        self.assertIn("placeholder-scan", out)

    def test_forbidden_fields_are_refused_even_when_indexed(self):
        for field in ("serverOperations[0]", "authorizationPolicies", "auditEvents",
                      "tests", "evidence", "uiRoutes"):
            self.register(CAP + "." + field)
            self.assertIn(
                "declared-static-forbidden-path", self.errors(), field
            )
        self.register("screens[users].dataSources")
        self.assertIn("declared-static-forbidden-path", self.errors())
        self.register("entities[user].sourceOfTruth")
        self.assertIn("declared-static-forbidden-path", self.errors())

    def test_a_legitimate_static_value_still_passes(self):
        self.cap["safeguards"] = ["typed confirmation shows placeholder text 'type DELETE'"]
        self.register(CAP + ".safeguards")
        self.save()
        for profile in PROFILES:
            code, out, err = self.validate(profile=profile)
            self.assertEqual(code, 0, profile + out + err)

    def test_self_approval_is_flagged_strictly(self):
        self.cap["safeguards"] = ["placeholder text 'type DELETE'"]
        self.register(CAP + ".safeguards", approver=self.cap["implementedBy"])
        self.assertNotIn("declared-static-self-approved", self.rule_names("internal"))
        self.assertIn("declared-static-self-approved", self.rule_names("standard"))
        self.assertIn("declared-static-self-approved", self.errors("regulated"))

    def test_an_agent_cannot_approve_its_own_exemption(self):
        self.cap["safeguards"] = ["placeholder text 'type DELETE'"]
        self.data["agents"] = [{
            "id": "agent-9", "role": "implementer", "ownsCapabilities": [], "ownsScreens": [],
            "status": "done",
        }]
        self.register(CAP + ".safeguards", approver="agent-9")
        self.assertIn("declared-static-self-approved", self.errors("regulated"))

    def test_gap_report_and_report_list_every_exemption(self):
        self.cap["safeguards"] = ["placeholder text 'type DELETE'"]
        self.register(CAP + ".safeguards")
        self.save()
        code, out, _err = self.cli("emit", "--format", "gap-report")
        self.assertEqual(code, 0)
        self.assertIn("Declared static", out)
        self.assertIn(CAP + ".safeguards", out)
        self.assertIn("cto", out)
        code, out, _err = self.cli("report")
        self.assertEqual(code, 0)
        self.assertIn("cto", out)


class TestSharedEvidence(ReleaseGateCase):
    """1.3 -- ordinary tracked files must not satisfy gates and sections."""

    def test_package_json_for_every_gate_and_readme_for_every_section_fails(self):
        self.write_file("package.json", '{\n  "name": "admin",\n  "scripts": {\n    "build": "next build"\n  }\n}\n')
        self.write_file("README.md", "# Admin console\n\nBuild with npm run build. 12 routes.\n")
        for gate in self.data["qualityGates"]:
            gate["evidence"] = ["package.json"]
        for values in self.data["crossCutting"].values():
            values["evidence"] = ["README.md"]
        self.save()
        code, out, _err = self.validate()
        self.assertEqual(code, 1, out)
        self.assertIn("evidence-not-output", out)

    def test_project_files_are_not_evidence_at_standard(self):
        for name, body in (("tsconfig.json", '{"compilerOptions": {"strict": true}}\n'),
                           ("vitest.config.ts", "export default { test: { include: ['tests'] } }\n"),
                           ("SECURITY.md", "# Security\n\nReport issues privately.\n")):
            self.write_file(name, body)
        self.data["qualityGates"][1]["evidence"] = ["tsconfig.json"]
        self.data["qualityGates"][3]["evidence"] = ["vitest.config.ts"]
        self.data["crossCutting"]["safety"]["evidence"] = ["SECURITY.md"]
        self.assertIn("evidence-not-output", self.errors("standard"))
        self.assertEqual(acm.RULES["evidence-not-output"], acm.QUALITY)

    def test_one_document_for_every_section_is_shared(self):
        self.write_file("docs/review.md", "Console review 2026-09-20\n- 18 operations exercised\n")
        for values in self.data["crossCutting"].values():
            values["evidence"] = ["docs/review.md"]
        self.assertIn("evidence-shared", self.errors("regulated"))

    def test_a_copy_of_one_log_is_still_that_log(self):
        body = "run finished\n  build typecheck lint: 0 errors across 118 files\n"
        for name in ("build", "typecheck", "lint"):
            self.write_file("evidence/copy-" + name + ".log", body)
        for gate in self.data["qualityGates"][:3]:
            gate["evidence"] = ["evidence/copy-" + gate["id"] + ".log"]
        self.assertIn("evidence-shared", self.errors("regulated"))

    def test_a_copy_of_the_manifest_is_refused(self):
        self.save()
        (self.root / "evidence" / "build.log").write_bytes(self.manifest.read_bytes())
        code, out, _err = self.validate()
        self.assertEqual(code, 1, out)
        self.assertIn("is a copy of the manifest", out)

    def test_evidence_inside_the_manifest_directory_is_refused(self):
        self.data["qualityGates"][0]["evidence"] = [".admin-console/manifest.json"]
        self.save()
        for profile in PROFILES:
            self.assertIn("evidence-inside-manifest-dir", self.errors(profile), profile)

    def test_a_test_file_cannot_double_as_gate_evidence(self):
        # Test source cited for a gate is source code, not the run's output.
        self.data["qualityGates"][3]["evidence"] = ["tests/user.spec.ts"]
        self.assertIn("evidence-not-output", self.errors("standard"))

    def test_a_test_file_cannot_double_as_section_evidence(self):
        self.data["crossCutting"]["audit"]["evidence"] = ["tests/user.spec.ts"]
        self.assertIn("evidence-is-test-file", self.rule_names("standard"))
        self.assertIn("evidence-is-test-file", self.errors("standard"))
        self.assertEqual(acm.RULES["evidence-is-test-file"], acm.QUALITY)

    def test_gate_evidence_must_mention_its_gate_at_regulated(self):
        self.write_file("evidence/run.log", "run finished\n  0 errors across 118 files\n")
        for gate in self.data["qualityGates"]:
            if gate["id"] == "lint":
                gate["evidence"] = ["evidence/run.log"]
        self.assertIn("gate-evidence-unrelated", self.errors("regulated"))
        self.assertNotIn("gate-evidence-unrelated", self.errors("standard"))
        self.assertIn("gate-evidence-unrelated", self.rule_names("standard"))

    def test_real_tool_output_in_a_file_named_for_the_gate_passes(self):
        # `npm audit` prints neither "security" nor its own command line.
        self.write_file("evidence/security.log", "found 0 vulnerabilities\n")
        self.assertNotIn("gate-evidence-unrelated", self.rule_names("regulated"))

    def test_a_utf16_log_from_powershell_is_read(self):
        path = self.root / "evidence" / "build.log"
        path.write_bytes("next build\r\n  Compiled successfully in 41.2s\r\n".encode("utf-16"))
        self.assertNotIn("gate-evidence-unrelated", self.rule_names("regulated"))
        self.assertNotIn("evidence-file-empty", self.rule_names("regulated"))

    def test_a_declared_command_can_stand_in_for_the_gate_name(self):
        self.write_file("evidence/lint.log", "eslint .\n  0 problems across 118 files\n")
        for gate in self.data["qualityGates"]:
            if gate["id"] == "lint":
                gate["command"] = "npx eslint ."
        self.assertNotIn("gate-evidence-unrelated", self.rule_names("regulated"))

    def test_one_file_for_two_gates_is_fine_but_three_is_not(self):
        self.write_file("evidence/all.log", "build typecheck lint\n  0 errors across 118 files\n")
        for gate in self.data["qualityGates"][:2]:
            gate["evidence"] = ["evidence/all.log"]
        self.assertNotIn("evidence-shared", self.rule_names("regulated"))
        self.data["qualityGates"][2]["evidence"] = ["evidence/all.log"]
        self.assertIn("evidence-shared", self.errors("regulated"))

    def test_a_gate_and_a_section_may_not_share_a_file(self):
        self.data["crossCutting"]["audit"]["evidence"] = ["evidence/build.log"]
        self.assertIn("evidence-shared", self.errors("regulated"))


class TestRouteSmokeTests(ReleaseGateCase):
    """1.4 -- a route-smoke loop must not satisfy every capability and screen."""

    def test_uiroutes_are_not_capability_tokens(self):
        tokens = acm.capability_tokens({"id": "user.list", "serverOperations": ["R.f"],
                                        "uiRoutes": ["/admin/users"]})
        self.assertNotIn("/admin/users", tokens)
        self.assertIn("user.list", tokens)

    def test_one_smoke_spec_for_everything_fails_at_regulated(self):
        self.write_file(
            "tests/smoke.spec.ts",
            "for (const route of ['/admin/users', '/admin/other']) {\n"
            "  test('route ' + route, async () => {\n"
            "    const res = await get(route);\n"
            "    expect(res.status).toBe(200);\n"
            "  });\n"
            "}\n",
        )
        second = json.loads(json.dumps(self.cap))
        second.update({"id": "user.export", "serverOperations": ["UserExport.run"]})
        self.data["entities"][0]["capabilities"].append(second)
        self.data["screens"][0]["capabilities"].append("user.export")
        for capability in self.data["entities"][0]["capabilities"]:
            capability["tests"] = ["tests/smoke.spec.ts"]
        self.data["screens"][0]["tests"] = ["tests/smoke.spec.ts"]
        self.data["crossCutting"]["authorization"]["policyTests"] = ["tests/smoke.spec.ts"]
        self.save()
        code, out, _err = self.validate()
        self.assertEqual(code, 1, out)
        self.assertIn("evidence-token-match", out)
        self.assertIn("test-file-shared", out)

    def test_one_spec_for_a_capability_its_screen_and_policy_is_fine(self):
        # A spec that names its capability may prove the capability, its screen and
        # the authorization policy at once; only an unnamed shared spec is suspect.
        self.write_file(
            "tests/user.spec.ts",
            "test('user.list on /admin/users', async () => {\n"
            "  expect((await listUsers({tenantId: 't-1'})).length).toBe(3);\n"
            "  expect(can('support', 'user.list', {})).toBe(false);\n"
            "});\n",
        )
        self.data["crossCutting"]["authorization"]["policyTests"] = ["tests/user.spec.ts"]
        self.assertNotIn("test-file-shared", self.rule_names("regulated"))

    def test_a_named_matrix_spec_for_many_capabilities_is_fine(self):
        self.write_file(
            "tests/matrix.spec.ts",
            "it.each([\n"
            "  ['support', 'user.list', true], ['support', 'user.second', false],\n"
            "  ['ops', 'user.list', true], ['ops', 'user.second', true],\n"
            "])('%s may %s', (role, capability, allowed) => {\n"
            "  expect(can(role, capability)).toBe(allowed);\n"
            "});\n",
        )
        second = json.loads(json.dumps(self.cap))
        second.update({"id": "user.second", "serverOperations": ["R.g"]})
        self.data["entities"][0]["capabilities"].append(second)
        self.data["screens"][0]["capabilities"].append("user.second")
        for capability in self.data["entities"][0]["capabilities"]:
            capability["tests"] = ["tests/matrix.spec.ts"]
        names = self.rule_names("regulated")
        self.assertNotIn("test-file-shared", names)
        self.assertNotIn("test-asserts-nothing", names)

    def test_a_spec_cited_by_many_capabilities_needs_that_many_assertions(self):
        self.write_file(
            "tests/many.spec.ts",
            "test('user.list user.second', () => {\n  assert(listAndSecond());\n});\n",
        )
        second = json.loads(json.dumps(self.cap))
        second["id"] = "user.second"
        second["serverOperations"] = ["R.g"]
        self.data["entities"][0]["capabilities"].append(second)
        for capability in self.data["entities"][0]["capabilities"]:
            capability["tests"] = ["tests/many.spec.ts"]
        self.assertEqual(
            acm.assertion_count((self.root / "tests" / "many.spec.ts").read_text(encoding="utf-8")), 1
        )
        self.assertIn("test-asserts-nothing", self.errors("regulated"))
        self.assertNotIn("test-asserts-nothing", self.errors("standard"))


class TestManifestAsTest(ReleaseGateCase):
    """1.5 -- the manifest, or a run log, is not a test file."""

    def test_the_manifest_cited_as_a_test_is_refused(self):
        self.cap["tests"] = [".admin-console/manifest.json"]
        self.save()
        code, out, _err = self.validate()
        self.assertEqual(code, 1)
        self.assertIn("evidence-inside-manifest-dir", out)

    def test_a_run_log_cited_as_a_test_is_refused(self):
        self.cap["tests"] = ["evidence/tests.log"]
        self.save()
        code, out, _err = self.validate()
        self.assertEqual(code, 1)
        self.assertIn("test-file-shape", out)

    def test_assertion_pattern_is_anchored(self):
        for body in (
            "import { assert } from 'chai';\n",
            "// add assertions later\n",
            "/* expect(x) once the API is stable */\nconst t = 1;\n",
            "const claims = jwt.verify(token, key);\n",
            "the assertion framework is not wired up yet\n",
        ):
            self.assertFalse(acm.has_assertion(body), body)

    def test_real_assertions_still_count(self):
        for body in (
            "expect(x).toBe(1);",
            "assert.equal(a, b);",
            "self.assertEqual(a, b)",
            "def test_x():\n    assert result == 3\n",
            "assert_eq!(a, b);",
            "require.NoError(t, err)",
            "verify(mock).called();",
        ):
            self.assertTrue(acm.has_assertion(body), body)

    def test_test_file_shape(self):
        shape = acm.looks_like_test_file
        from pathlib import Path
        self.assertTrue(shape(Path("tests/user.spec.ts"), ""))
        self.assertTrue(shape(Path("tests/test_user.py"), ""))
        self.assertTrue(shape(Path("pkg/user_test.go"), ""))
        self.assertTrue(shape(Path("src/UserTests.cs"), ""))
        self.assertTrue(shape(Path("src/lib.rs"), "#[test]\nfn t() {}"))
        self.assertFalse(shape(Path("src/lib.rs"), "fn t() {}"))
        self.assertTrue(shape(Path("e2e/flows.js"), "describe('flows', () => {})"))
        self.assertFalse(shape(Path("evidence/tests.log"), "vitest run\n  Tests  6 passed (6)\n"))


class TestSelfReview(ReleaseGateCase):
    """1.6 -- the documented handoff must not launder self-review."""

    def blank_review(self):
        self.cap.update({"owner": "", "implementedBy": "", "reviewStatus": "unreviewed",
                         "reviewedBy": ""})
        self.save()

    def test_claim_release_then_self_review_is_refused(self):
        self.blank_review()
        code, _o, err = self.cli("claim", "--agent", "a1", "--role", "implementer",
                                 "--capability", "user.list")
        self.assertEqual(code, 0, err)
        code, _o, err = self.cli("release-claim", "--agent", "a1")
        self.assertEqual(code, 0, err)
        stored = self.capability_of(self.read())
        self.assertEqual(stored["implementedBy"], "a1")
        self.assertEqual(stored["owner"], "")
        path = CAP + ".reviewedBy"
        code, _o, err = self.cli("set", "--path", path, "--value", "a1")
        self.assertEqual(code, 0, err)
        code, _o, err = self.cli("set", "--path", CAP + ".reviewStatus", "--value", "reviewed")
        self.assertEqual(code, 2, "the write guard must refuse a self-review at regulated")
        self.assertIn("reviewer-identity", err)
        code, _o, err = self.cli("set", "--path", CAP + ".reviewStatus", "--value", "reviewed",
                                 "--allow-invalid")
        self.assertEqual(code, 0, err)
        code, out, _err = self.validate()
        self.assertEqual(code, 1)
        self.assertIn("reviewer-identity", out)

    def test_a_different_agent_may_review(self):
        self.blank_review()
        self.cli("claim", "--agent", "a1", "--role", "implementer", "--capability", "user.list")
        self.cli("release-claim", "--agent", "a1")
        # Once agents are registered the reviewer is one of them (R-06): it claims first.
        code, _o, err = self.cli("claim", "--agent", "a2", "--role", "qa", "--capability", "user.list")
        self.assertEqual(code, 0, err)
        self.cli("set", "--path", CAP + ".reviewedBy", "--value", "a2")
        code, _o, err = self.cli("set", "--path", CAP + ".reviewStatus", "--value", "reviewed")
        self.assertEqual(code, 0, err)
        self.cli("release-claim", "--agent", "a2")
        code, out, err = self.validate()
        self.assertEqual(code, 0, out + err)

    def test_previously_owned_implementers_count_even_if_implementedby_is_edited(self):
        self.cap.update({"owner": "", "implementedBy": "someone-else", "reviewedBy": "a1"})
        self.data["agents"] = [{
            "id": "a1", "role": "implementer", "ownsCapabilities": [],
            "previouslyOwned": ["user.list"], "ownsScreens": [], "status": "done",
        }]
        self.assertIn("reviewer-identity", self.errors("regulated"))

    def test_an_implementer_id_cannot_re_register_as_a_reviewer(self):
        self.blank_review()
        self.cli("claim", "--agent", "a1", "--role", "implementer", "--capability", "user.list")
        self.cli("release-claim", "--agent", "a1")
        code, _o, err = self.cli("claim", "--agent", "a1", "--role", "qa",
                                 "--capability", "user.list")
        self.assertEqual(code, 2)
        self.assertIn("different agent id", err)

    def test_missing_implementedby_is_flagged_late(self):
        self.cap["implementedBy"] = ""
        self.assertEqual(acm.RULES["implementer-unrecorded"], acm.LATE)
        self.assertIn("implementer-unrecorded", self.errors("regulated"))
        self.assertNotIn("implementer-unrecorded", self.errors("standard"))
        self.assertIn("implementer-unrecorded", self.rule_names("standard"))

    def test_a_later_implementer_replaces_an_architect_as_implementer(self):
        self.blank_review()
        self.cli("claim", "--agent", "arch", "--role", "architect", "--capability", "user.list")
        self.cli("release-claim", "--agent", "arch")
        self.cli("claim", "--agent", "a1", "--role", "implementer", "--capability", "user.list")
        self.assertEqual(self.capability_of(self.read())["implementedBy"], "a1")


class TestReviewerClaims(ReleaseGateCase):
    """1.7 -- claiming a capability to review it must not make you its implementer."""

    def test_a_reviewer_claim_leaves_owner_and_implementer_alone(self):
        self.cap.update({"reviewStatus": "unreviewed", "reviewedBy": ""})
        self.save()
        code, _o, err = self.cli("claim", "--agent", "r1", "--role", "ux-reviewer",
                                 "--capability", "user.list")
        self.assertEqual(code, 0, err)
        stored = self.capability_of(self.read())
        self.assertEqual(stored["owner"], "impl")
        self.assertEqual(stored["implementedBy"], "impl")
        code, _o, err = self.cli("set", "--path", CAP + ".reviewedBy", "--value", "r1")
        self.assertEqual(code, 0, err)
        code, _o, err = self.cli("set", "--path", CAP + ".reviewStatus", "--value", "reviewed")
        self.assertEqual(code, 0, err)
        agents = self.read()["agents"]
        self.assertEqual(agents[0]["id"], "r1")
        self.assertIn("user.list", agents[0]["ownsCapabilities"])


class TestProfileOverride(ReleaseGateCase):
    """1.8 -- --profile must not certify a release below the manifest's profile."""

    def test_release_below_the_declared_profile_is_refused(self):
        self.save()
        code, _out, err = run("validate", "--manifest", self.manifest, "--project-root",
                              self.root, "--phase", "release", "--profile", "internal")
        self.assertEqual(code, 2)
        self.assertIn("--allow-lower-profile", err)

    def test_the_flag_allows_it_and_says_so(self):
        self.save()
        code, out, err = run("validate", "--manifest", self.manifest, "--project-root",
                             self.root, "--phase", "release", "--profile", "internal",
                             "--allow-lower-profile", "--json")
        self.assertEqual(code, 0, out + err)
        self.assertIn("evaluated at internal; manifest profile is regulated", err)
        payload = json.loads(out)
        self.assertEqual(payload["manifestProfile"], "regulated")
        self.assertEqual(payload["profile"], "internal")

    def test_plan_phase_may_use_any_profile(self):
        self.save()
        code, _out, err = run("validate", "--manifest", self.manifest, "--project-root",
                              self.root, "--phase", "plan", "--profile", "internal")
        self.assertEqual(code, 0, err)

    def test_a_stricter_profile_is_always_allowed(self):
        self.profile = "standard"
        self.data["profile"] = "standard"
        for decision in self.data["decisions"]:
            if decision["id"] == "dec.profile":
                decision["decision"] = "Run at the standard profile"
        self.save()
        code, out, err = run("validate", "--manifest", self.manifest, "--project-root",
                             self.root, "--phase", "release", "--profile", "regulated")
        self.assertEqual(code, 0, out + err)

    def test_the_summary_line_names_the_effective_profile(self):
        self.save()
        _code, out, _err = self.validate()
        self.assertIn("[profile regulated, phase release, adminwright ", out)
        self.assertIn(", ruleset ", out)


class TestNotApplicableRelabel(ReleaseGateCase):
    """1.9 -- not-applicable must not be a release-passing relabel of deferred."""

    def add_command(self, status="not-applicable"):
        self.data["entities"][0]["capabilities"].append({
            "id": "user.purge", "outcome": "Purge an account", "kind": "command",
            "roles": ["ops"], "risk": "high", "status": status,
            "rationale": "not needed for launch", "entityStates": {"from": [], "to": []},
            "uiRoutes": [], "serverOperations": [], "authorizationPolicies": [],
            "auditEvents": [], "safeguards": [], "dataBinding": "", "tests": [],
            "evidence": [], "owner": "", "implementedBy": "", "reviewStatus": "unreviewed",
        })

    def test_relabelled_command_needs_a_confirmed_decision(self):
        self.add_command()
        self.save()
        code, out, _err = self.validate()
        self.assertEqual(code, 1)
        self.assertIn("not-applicable-undecided", out)

    def test_a_confirmed_decision_makes_it_pass(self):
        self.add_command()
        self.data["decisions"].append({
            "id": "dec.purge", "kind": "not-applicable", "decision": "No purge command",
            "reason": "legal hold", "evidence": [], "status": "confirmed",
            "appliesTo": ["user.purge"], "confirmedBy": "Dana Reyes, legal counsel",
        })
        self.save()
        code, out, err = self.validate()
        self.assertEqual(code, 0, out + err)

    def test_a_decision_nobody_is_recorded_as_confirming_is_not_enough(self):
        # R-05: `status: confirmed` alone is what any agent can write.
        self.add_command()
        self.data["decisions"].append({
            "id": "dec.purge", "kind": "not-applicable", "decision": "No purge command",
            "reason": "legal hold", "evidence": [], "status": "confirmed",
            "appliesTo": ["user.purge"],
        })
        errors = self.errors("regulated")
        self.assertIn("not-applicable-undecided", errors)
        self.assertIn("decision-self-confirmed", errors)

    def test_a_decision_about_something_else_is_not_enough(self):
        # The critic's reproduction: the fixture's own profile decision named the
        # capability, and any confirmed decision naming it used to count.
        self.add_command()
        for decision in self.data["decisions"]:
            if decision["id"] == "dec.profile":
                decision["appliesTo"] = ["user.purge"]
        self.data["decisions"].append({
            "id": "dec.design", "kind": "design", "decision": "Keep purge out of the UI",
            "reason": "risk", "evidence": [], "status": "confirmed", "appliesTo": ["user.purge"],
        })
        self.assertIn("not-applicable-undecided", self.errors("regulated"))

    def test_an_assumed_decision_is_not_enough(self):
        self.add_command()
        self.data["decisions"] = [{
            "id": "dec.purge", "decision": "No purge command", "reason": "legal hold",
            "evidence": [], "status": "assumed", "appliesTo": ["user.purge"],
        }]
        self.assertIn("not-applicable-undecided", self.errors("regulated"))

    def test_a_query_may_be_not_applicable_without_a_decision(self):
        self.add_command()
        self.data["entities"][0]["capabilities"][-1]["kind"] = "query"
        self.assertNotIn("not-applicable-undecided", self.rule_names("regulated"))

    def test_report_counts_not_applicable(self):
        self.add_command()
        self.save()
        code, out, _err = self.cli("report")
        self.assertEqual(code, 0)
        self.assertIn("not-applicable: 1", out)


class TestConfusableFolding(ReleaseGateCase):
    """1.10 -- invisible and look-alike letters must not hide a placeholder."""

    def test_disguised_mock_is_flagged(self):
        for disguised in (
            "mo​ck",                 # zero-width space
            "mo‍ck",                 # zero-width joiner
            "moск",             # Cyrillic es, ka -> "mock"
            "ｍｏｃｋ",   # fullwidth
            "\U0001d426\U0001d428\U0001d41c\U0001d424",  # mathematical bold
            "mo­ck",                 # soft hyphen
        ):
            self.cap["dataBinding"] = disguised + "Repository.findAll"
            self.assertIn("placeholder-scan", self.errors("internal"), repr(disguised))


class TestLifecycleEscape(ReleaseGateCase):
    """1.11 -- an unrelated gap must not silence lifecycle checks."""

    def setUp(self):
        super().setUp()
        self.data["entities"][0]["lifecycleStates"] = ["active", "quarantined"]

    def gap(self, **fields):
        base = {"id": "gap.q", "severity": "low", "description": "", "status": "accepted",
                "rationale": "", "evidence": []}
        base.update(fields)
        self.data["gaps"] = [base]

    def test_the_state_is_flagged_without_any_gap(self):
        self.assertIn("lifecycle-observable", self.rule_names("standard"))
        self.assertIn("lifecycle-reachable", self.rule_names("standard"))

    def test_an_unrelated_gap_mentioning_the_state_does_not_silence_it(self):
        self.gap(description="Orders are quarantined by the fraud vendor",
                 rationale="order.quarantined is set externally")
        self.assertIn("lifecycle-observable", self.rule_names("standard"))

    def test_a_gap_naming_entity_and_state_is_an_escape(self):
        self.gap(description="user quarantined by the abuse webhook, never by an operator",
                 rationale="external system owns the transition")
        names = self.rule_names("standard")
        self.assertNotIn("lifecycle-observable", names)
        self.assertNotIn("lifecycle-reachable", names)

    def test_an_explicit_entity_field_is_an_escape(self):
        self.gap(description="quarantined is set by the abuse webhook", entity="user")
        self.assertNotIn("lifecycle-observable", self.rule_names("standard"))

    def test_ocr_findings_are_never_an_escape(self):
        self.gap(description="user quarantined path unreviewed", source="ocr")
        self.assertIn("lifecycle-observable", self.rule_names("standard"))

    def test_a_fixed_or_blocked_gap_is_not_an_escape(self):
        for status in ("fixed", "blocked"):
            self.gap(description="user quarantined by webhook", status=status)
            self.assertIn("lifecycle-observable", self.rule_names("standard"), status)


class TestFixedGapNeedsProof(ReleaseGateCase):
    """R-01 -- a critical gap closed as fixed must cite the run that proves the fix."""

    def add_gap(self, **fields):
        gap = {"id": "gap.authz", "severity": "critical", "capability": "user.list",
               "description": "user.list skips the tenant scope check", "status": "fixed",
               "rationale": "", "evidence": []}
        gap.update(fields)
        self.data["gaps"] = [gap]

    def test_fixed_critical_gap_without_evidence_blocks_release(self):
        self.add_gap()
        for profile in PROFILES:
            self.assertIn("gap-fix-unproven", self.errors(profile), profile)

    def test_an_ocr_gap_whose_only_evidence_is_its_own_record_is_unproven(self):
        (self.manifest.parent / "ocr-record-1.json").write_text('{"finding": "x"}', encoding="utf-8")
        self.add_gap(source="ocr", evidence=[".admin-console/ocr-record-1.json"])
        self.assertIn("gap-fix-unproven", self.errors("internal"))
        self.assertIn("gap-fix-unproven", self.errors("regulated"))

    def test_a_fixed_gap_citing_a_passing_run_passes(self):
        self.add_gap(evidence=["evidence/tests.log"])
        self.assertEqual(self.errors("regulated"), set())

    def test_a_low_fixed_gap_needs_no_proof(self):
        self.add_gap(severity="low")
        self.assertNotIn("gap-fix-unproven", self.rule_names("regulated"))


class TestAcceptedGapNeedsAPerson(ReleaseGateCase):
    """R-02 -- only a person accepts a critical or high risk."""

    def add_gap(self, severity="critical"):
        self.data["gaps"] = [{
            "id": "gap.authz", "severity": severity,
            "description": "Support operators can list any tenant's users",
            "status": "accepted", "rationale": "ship it", "evidence": [],
        }]

    def accept(self, **fields):
        decision = {
            "id": "dec.accept-authz", "kind": "risk-acceptance",
            "decision": "Accept gap.authz until the March release",
            "reason": "compensating control: nightly access review", "evidence": [],
            "status": "confirmed", "appliesTo": ["gap.authz"],
            "confirmedBy": "Dana Reyes, Head of Operations, 2026-11-17",
        }
        decision.update(fields)
        self.data["decisions"].append(decision)

    def test_accepted_critical_gap_without_decision_blocks_release(self):
        self.add_gap()
        for profile in PROFILES:
            self.assertIn("gap-acceptance-undecided", self.errors(profile), profile)

    def test_a_confirmed_risk_acceptance_naming_the_gap_clears_it(self):
        self.add_gap()
        self.accept()
        self.assertEqual(self.errors("regulated"), set())

    def test_an_unconfirmed_or_unattributed_decision_does_not_count(self):
        self.add_gap()
        for fields in ({"status": "assumed"}, {"confirmedBy": ""}, {"kind": "other"},
                       {"appliesTo": ["*"]}):
            self.data["decisions"] = [d for d in self.data["decisions"] if d["id"] != "dec.accept-authz"]
            self.accept(**fields)
            self.assertIn("gap-acceptance-undecided", self.errors("internal"), fields)

    def test_an_agent_cannot_be_the_person_who_accepted(self):
        self.add_gap()
        self.data["agents"] = [{"id": "sec-1", "role": "security", "ownsCapabilities": [],
                                "ownsScreens": [], "status": "done"}]
        self.accept(confirmedBy="SEC-1​")
        self.assertIn("gap-acceptance-undecided", self.errors("internal"))
        self.assertIn("decision-self-confirmed", self.errors("regulated"))

    def test_a_high_gap_counts_and_a_low_gap_needs_only_its_rationale(self):
        self.add_gap("high")
        self.assertIn("gap-acceptance-undecided", self.errors("internal"))
        self.add_gap("low")
        self.assertNotIn("gap-acceptance-undecided", self.rule_names("regulated"))


class TestBlankListMembers(ReleaseGateCase):
    """R-03 -- a list of blank strings is as empty as an empty list."""

    def test_the_command_is_honest_to_begin_with(self):
        self.add_command()
        self.assertEqual(self.errors("regulated"), set())

    def test_blank_members_raise_the_rule_an_empty_list_raises(self):
        command = self.add_command()
        cases = (
            ("serverOperations", "required-field-empty"),
            ("authorizationPolicies", "required-field-empty"),
            ("auditEvents", "audit-events-missing"),
            ("safeguards", "high-risk-controls"),
        )
        for key, rule in cases:
            original = command[key]
            for blank in ([], [""], ["  "]):
                command[key] = blank
                self.assertIn(rule, self.errors("internal"), "%s = %r" % (key, blank))
            command[key] = original

    def test_blank_separation_of_duties_is_none(self):
        self.add_command(risk="critical")
        self.data["roles"][0]["separationOfDuties"] = [""]
        self.assertIn("separation-of-duties", self.errors("regulated"))
        self.data["roles"][0]["separationOfDuties"] = ["user.suspend"]
        self.assertNotIn("separation-of-duties", self.rule_names("regulated"))


class TestNotApplicableIsNoQueryAndNeedsADecision(ReleaseGateCase):
    """R-04 -- not-applicable is not a query, and is not free for integrations and queues."""

    def test_an_entity_whose_only_query_is_not_applicable_has_no_query(self):
        self.cap.update({"status": "not-applicable", "rationale": "n/a", "tests": [],
                         "evidence": [], "reviewStatus": "unreviewed", "reviewedBy": ""})
        self.assertIn("entity-query-missing", self.errors("standard"))

    def integration(self):
        self.data["integrations"] = [{
            "id": "stripe", "direction": "outbound", "sourceOfTruth": "Stripe for billing",
            "operations": ["cancel subscription"], "failureHandling": "retry with backoff",
            "reconciliation": "", "monitoring": "", "status": "not-applicable",
            "rationale": "n/a", "tests": [], "roles": ["ops"],
        }]

    def queue(self):
        self.data["workQueues"] = [{
            "id": "refunds", "purpose": "Review refunds", "roles": ["ops"],
            "source": "postgres:refunds where status = pending", "priorityRule": "oldest first",
            "sla": "4h", "actions": ["approve"], "status": "not-applicable", "rationale": "n/a",
            "tests": [],
        }]

    def test_a_not_applicable_integration_or_queue_needs_a_decision(self):
        for build, cid in ((self.integration, "stripe"), (self.queue, "refunds")):
            build()
            self.assertIn("not-applicable-undecided", self.errors("regulated"), cid)
            self.data["decisions"].append({
                "id": "dec." + cid, "kind": "not-applicable", "decision": "Not part of this console",
                "reason": "the vendor console covers it", "evidence": [], "status": "confirmed",
                "appliesTo": [cid], "confirmedBy": "Sam Okafor, product owner",
            })
            self.assertNotIn("not-applicable-undecided", self.rule_names("regulated"), cid)
            self.data["integrations"] = []
            self.data["workQueues"] = []


class TestDecisionsNameWhoConfirmed(ReleaseGateCase):
    """R-05 -- a waiver needs a person's name, not only `status: confirmed`."""

    WAIVED = ("build", "typecheck", "lint", "browser")

    def waive(self, **fields):
        for gate in self.data["qualityGates"]:
            if gate["id"] in self.WAIVED:
                gate.update({"status": "not-applicable", "evidence": [], "rationale": "not needed"})
        decision = {"id": "dec.skip", "kind": "not-applicable",
                    "decision": "skip build, typecheck, lint and browser", "reason": "agent decided",
                    "evidence": [], "status": "confirmed", "appliesTo": list(self.WAIVED)}
        decision.update(fields)
        self.data["decisions"].append(decision)

    def test_a_self_confirmed_waiver_is_refused(self):
        self.waive()
        errors = self.errors("regulated")
        self.assertIn("decision-self-confirmed", errors)
        self.assertIn("gate-not-applicable-undecided", errors)

    def test_an_agent_named_as_confirmer_is_refused_even_disguised(self):
        self.data["agents"] = [{"id": "arch-1", "role": "architect", "ownsCapabilities": [],
                                "ownsScreens": [], "status": "done"}]
        for name in ("arch-1", "Arch-1.", "arch​-1"):
            self.data["decisions"] = [d for d in self.data["decisions"] if d["id"] != "dec.skip"]
            self.waive(confirmedBy=name)
            self.assertIn("decision-self-confirmed", self.errors("regulated"), repr(name))
            self.assertIn("gate-not-applicable-undecided", self.errors("regulated"), repr(name))

    def test_the_implementer_of_a_covered_capability_cannot_confirm(self):
        self.waive(confirmedBy="impl", appliesTo=list(self.WAIVED) + ["user.list"])
        self.assertIn("decision-self-confirmed", self.errors("regulated"))

    def test_a_named_person_clears_it(self):
        self.waive(confirmedBy="product-owner")
        names = self.rule_names("regulated")
        self.assertNotIn("decision-self-confirmed", names)
        self.assertNotIn("gate-not-applicable-undecided", names)

    def test_a_file_a_decision_cites_must_exist(self):
        self.waive(confirmedBy="product-owner", evidence=["evidence/waiver-email.md"])
        self.assertIn("evidence-path-missing", self.errors("internal"))
        self.write_file("evidence/waiver-email.md", "From the product owner, 2026-11-17: agreed.\n")
        self.assertNotIn("evidence-path-missing", self.rule_names("internal"))


class TestReviewerIdentityIsFolded(ReleaseGateCase):
    """R-06 -- the reviewer is compared after the placeholder scan's fold, and registered."""

    def test_a_disguised_implementer_is_still_the_implementer(self):
        for disguised in ("impl​", "іmpl", "IMPL.", " impl "):
            self.cap["reviewedBy"] = disguised
            self.assertIn("reviewer-identity", self.errors("regulated"), repr(disguised))

    def agents(self, *extra):
        self.data["agents"] = [{"id": "impl", "role": "implementer", "ownsCapabilities": [],
                                "previouslyOwned": ["user.list"], "ownsScreens": [],
                                "status": "done"}] + list(extra)

    def test_once_agents_register_the_reviewer_must_be_one_of_them(self):
        self.agents()
        self.cap["reviewedBy"] = "someone-else"
        self.assertIn("reviewer-identity", self.errors("regulated"))
        self.agents({"id": "qa-1", "role": "qa", "ownsCapabilities": [], "ownsScreens": [],
                     "status": "done"})
        self.cap["reviewedBy"] = "qa-1"
        self.assertNotIn("reviewer-identity", self.rule_names("regulated"))

    def test_another_implementer_is_not_a_reviewer(self):
        self.agents({"id": "impl-2", "role": "implementer", "ownsCapabilities": [],
                     "ownsScreens": [], "status": "done"})
        self.cap["reviewedBy"] = "impl-2"
        self.assertIn("reviewer-identity", self.errors("regulated"))

    def test_contest_and_static_approval_are_folded_too(self):
        self.cap["contestedBy"] = "qa​"
        self.assertIn("contest-self-cleared", self.errors("regulated"))
        self.data["declaredStatic"] = [{
            "id": "static.one", "path": CAP + ".safeguards", "value": "v", "reason": "r",
            "approvedBy": "Impl.", "evidence": [],
        }]
        self.assertIn("declared-static-self-approved", self.errors("regulated"))


class TestEvidenceIsRunOutput(ReleaseGateCase):
    """R-08, R-09, R-10 -- capability, crossCutting and fixed-gap evidence is a passing run."""

    def test_source_code_is_not_capability_or_gap_evidence(self):
        self.write_file("src/user.service.ts", "export function list() {\n  return db.users.findMany();\n}\n")
        self.cap["evidence"] = ["src/user.service.ts"]
        self.assertIn("evidence-not-output", self.errors("standard"))
        self.cap["evidence"] = ["evidence/tests.log"]
        self.data["gaps"] = [{"id": "gap.x", "severity": "high", "description": "user.list leaks",
                              "status": "fixed", "rationale": "", "evidence": ["src/user.service.ts"]}]
        self.assertIn("evidence-not-output", self.errors("standard"))

    def test_evidence_that_records_a_failed_run_is_refused(self):
        self.write_file("evidence/list-e2e.log",
                        "$ playwright test user-list\n1 failed: user.list returns 500\n\n# exit code: 1\n")
        self.cap["evidence"] = ["evidence/list-e2e.log"]
        for profile in ("internal", "regulated"):
            self.assertIn("evidence-records-failure", self.errors(profile), profile)
        self.data["crossCutting"]["audit"]["evidence"] = ["evidence/list-e2e.log"]
        self.cap["evidence"] = ["evidence/tests.log"]
        self.assertIn("evidence-records-failure", self.errors("internal"))

    def test_a_passing_frame_is_accepted(self):
        self.write_file("evidence/list-e2e.log",
                        "$ playwright test user-list\n4 passed: user.list returns 3 rows\n\n# exit code: 0\n")
        self.cap["evidence"] = ["evidence/list-e2e.log"]
        self.assertNotIn("evidence-records-failure", self.rule_names("regulated"))
        self.assertEqual(self.errors("regulated"), set())

    def test_version_control_and_dependency_files_are_not_evidence(self):
        self.write_file(".git/HEAD", "ref: refs/heads/main\n")
        self.write_file(".git/config", "[core]\n\trepositoryformatversion = 0\n")
        self.write_file("node_modules/left-pad/index.js", "module.exports = leftPad;\n")
        cross = self.data["crossCutting"]
        cross["audit"]["evidence"] = [".git/config"]
        cross["safety"]["evidence"] = [".git/HEAD"]
        cross["data"]["evidence"] = ["node_modules/left-pad/index.js"]
        found = [item for item in self.result("standard").errors
                 if item["rule"] == "evidence-not-output"]
        self.assertEqual(len(found), 3, found)

    def test_folders_are_judged_inside_the_project(self):
        reason = acm.non_evidence_reason
        checkout = Path("/home/x/node_modules/app/evidence/audit.log")
        self.assertEqual(reason(checkout, "cross", "evidence/audit.log"), "")
        self.assertNotEqual(reason(checkout, "cross", "node_modules/app/evidence/audit.log"), "")


class TestTestsAndSecurityGatesAtInternal(ReleaseGateCase):
    """R-13 -- the tests and security gates are never not-applicable, at internal either."""

    def test_internal_release_needs_both_gates(self):
        for gate in self.data["qualityGates"]:
            if gate["id"] in ("tests", "security"):
                gate.update({"status": "not-applicable", "evidence": [],
                             "rationale": "small internal tool"})
        self.assertIn("gate-always-required", self.errors("internal"))
        self.assertEqual(acm.RULES["gate-always-required"], acm.E3)


class TestSafetyFlags(ReleaseGateCase):
    """R-14 -- the safety flags the template ships are enforced when they apply."""

    def test_step_up_and_bulk_preview_are_required_when_they_apply(self):
        safety = self.data["crossCutting"]["safety"]
        safety["highRiskApprovalOrStepUp"] = False
        safety["bulkPreviewAndPartialFailure"] = False
        self.assertNotIn("crosscutting-flag", self.rule_names("regulated"))
        self.add_command()
        self.add_command("user.export", kind="export", risk="moderate")
        paths = {item["path"] for item in self.result("regulated").errors
                 if item["rule"] == "crosscutting-flag"}
        self.assertEqual(paths, {"crossCutting.safety.highRiskApprovalOrStepUp",
                                 "crossCutting.safety.bulkPreviewAndPartialFailure"})


class TestInternalCrossCuttingWaivers(ReleaseGateCase):
    """D-01 -- at internal four release flags may be waived with a rationale."""

    def test_a_waiver_warns_at_internal_and_fails_above_it(self):
        audit = self.data["crossCutting"]["audit"]
        audit["tamperProtected"] = False
        self.assertIn("crosscutting-flag", self.errors("internal"))
        audit["notApplicable"] = {"tamperProtected": "three operators; append-only grants, no hash chain"}
        self.assertNotIn("crosscutting-flag", self.rule_names("internal"))
        self.assertEqual(self.errors("internal"), set())
        self.assertIn("crosscutting-flag-waived", self.rule_names("internal"))
        self.assertIn("crosscutting-flag-waived", self.errors("standard"))
        self.assertIn("crosscutting-flag-waived", self.errors("regulated"))

    def test_only_the_four_flags_can_be_waived(self):
        self.data["crossCutting"]["experience"].update({
            "keyboardCriticalFlowsTested": False, "responsiveCriticalFlowsTested": False,
            "notApplicable": {"keyboardCriticalFlowsTested": "r", "responsiveCriticalFlowsTested": "r"},
        })
        self.data["crossCutting"]["authentication"].update({
            "mfaForPrivilegedRoles": False, "notApplicable": {"mfaForPrivilegedRoles": "r"},
        })
        self.assertEqual(self.errors("internal"), set())
        self.data["crossCutting"]["authorization"]["serverEnforced"] = False
        self.data["crossCutting"]["authorization"]["notApplicable"] = {"serverEnforced": "r"}
        errors = self.errors("internal")
        self.assertIn("crosscutting-flag", errors)
        self.assertIn("unknown-field", self.rule_names("internal"))


class TestSpecificTokens(ReleaseGateCase):
    """R-12 -- a test idiom or a short route does not name a capability or a screen."""

    def test_code_idioms_are_not_specific(self):
        for value in ("expect(res.status)", "async () =>", "x = y.z", "'quoted.thing'"):
            self.assertFalse(acm.specific_token(value), value)
        for value in ("TenantService.suspend", "admin users suspend", "suspend_tenant",
                      "suspendTenant", "TenantsController#suspend", "ns::suspend_tenant",
                      "POST /admin/tenants", "App\\Services\\TenantService::suspend"):
            self.assertTrue(acm.specific_token(value), value)

    def test_a_test_idiom_as_an_operation_does_not_match_an_unrelated_test(self):
        self.write_file("tests/generic.spec.ts",
                        "test('health', async () => {\n  const res = await get('/health');\n"
                        "  expect(res.status).toBe(200);\n});\n")
        self.cap.update({"serverOperations": ["expect(res.status)"], "tests": ["tests/generic.spec.ts"]})
        self.assertIn("evidence-token-match", self.errors("regulated"))

    def test_a_root_route_does_not_name_a_screen(self):
        self.write_file("tests/unrelated.spec.ts",
                        "import { add } from './math';\n"
                        "test('adds', () => { expect(add(1, 2)).toBe(3); });\n")
        screen = self.data["screens"][0]
        screen.update({"id": "home", "route": "/", "tests": ["tests/unrelated.spec.ts"]})
        self.cap["uiRoutes"] = ["/"]
        paths = {item["path"] for item in self.result("regulated").errors
                 if item["rule"] == "evidence-token-match"}
        self.assertIn("screens[home].tests", paths)


class TestGapLookupsAreIndexed(ReleaseGateCase):
    """R-24 -- gaps are walked a fixed number of times, not once per capability."""

    def count_gap_walks(self, capabilities):
        for index in range(capabilities):
            cid = "user.view-" + str(index)
            capability = dict(self.cap, id=cid)
            self.data["entities"][0]["capabilities"].append(capability)
            self.data["screens"][0]["capabilities"].append(cid)
        self.data["gaps"] = [{"id": "gap." + str(n), "severity": "low", "source": "ocr",
                              "description": "finding " + str(n), "status": "open",
                              "rationale": "", "evidence": []} for n in range(20)]
        original = acm.iter_named
        calls = []

        def counting(manifest, key):
            if key == "gaps":
                calls.append(key)
            return original(manifest, key)

        acm.iter_named = counting
        try:
            self.result("regulated")
        finally:
            acm.iter_named = original
        return len(calls)

    def test_gap_walks_do_not_grow_with_capabilities(self):
        # One walk each for the index, decisions, gaps, evidence and (sometimes) the
        # archetype escape text; before the index it was one per capability and state.
        self.assertLessEqual(self.count_gap_walks(40), 6)


if __name__ == "__main__":
    unittest.main()
