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
        self.assertIn("evidence-shared", out)

    def test_evidence_inside_the_manifest_directory_is_refused(self):
        self.data["qualityGates"][0]["evidence"] = [".admin-console/manifest.json"]
        self.save()
        for profile in PROFILES:
            self.assertIn("evidence-inside-manifest-dir", self.errors(profile), profile)

    def test_a_test_file_cannot_double_as_gate_evidence(self):
        self.data["qualityGates"][3]["evidence"] = ["tests/user.spec.ts"]
        self.assertIn("evidence-is-test-file", self.rule_names("standard"))
        self.assertIn("evidence-is-test-file", self.errors("standard"))
        self.assertEqual(acm.RULES["evidence-is-test-file"], acm.QUALITY)

    def test_gate_evidence_must_mention_its_gate_at_regulated(self):
        self.write_file("evidence/lint.log", "run finished\n  0 errors across 118 files\n")
        self.assertIn("gate-evidence-unrelated", self.errors("regulated"))
        self.assertNotIn("gate-evidence-unrelated", self.errors("standard"))
        self.assertIn("gate-evidence-unrelated", self.rule_names("standard"))

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
        self.cap["tests"] = ["tests/smoke.spec.ts"]
        self.data["screens"][0]["tests"] = ["tests/smoke.spec.ts"]
        self.data["crossCutting"]["authorization"]["policyTests"] = ["tests/smoke.spec.ts"]
        self.save()
        code, out, _err = self.validate()
        self.assertEqual(code, 1, out)
        self.assertIn("evidence-token-match", out)
        self.assertIn("test-file-shared", out)

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
        self.cli("set", "--path", CAP + ".reviewedBy", "--value", "a2")
        code, _o, err = self.cli("set", "--path", CAP + ".reviewStatus", "--value", "reviewed")
        self.assertEqual(code, 0, err)
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
        self.save()
        code, out, err = run("validate", "--manifest", self.manifest, "--project-root",
                             self.root, "--phase", "release", "--profile", "regulated")
        self.assertEqual(code, 0, out + err)

    def test_the_summary_line_names_the_effective_profile(self):
        self.save()
        _code, out, _err = self.validate()
        self.assertIn("[profile regulated, phase release]", out)


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
        self.data["decisions"] = [{
            "id": "dec.purge", "decision": "No purge command", "reason": "legal hold",
            "evidence": [], "status": "confirmed", "appliesTo": ["user.purge"],
        }]
        self.save()
        code, out, err = self.validate()
        self.assertEqual(code, 0, out + err)

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


if __name__ == "__main__":
    unittest.main()
