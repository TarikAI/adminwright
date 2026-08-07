# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TarikAI
"""Regression tests for admin_console_manifest.py.

Standard library only (unittest), so `python -m unittest discover tests` works
anywhere the skill itself works. No pytest, no fixtures package, no plugins.

Every test here exists because something actually broke. The adversarial-review
cases are grouped at the bottom and named for the bypass they close; deleting one
re-opens a hole that shipped once already.

Run:  python -m unittest discover -s tests -v
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = SKILL_ROOT / "scripts" / "admin_console_manifest.py"


def load_module():
    spec = importlib.util.spec_from_file_location("acm", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


acm = load_module()


def run(*args, **kwargs):
    """Invoke the CLI the way a user would, returning (exit_code, stdout, stderr)."""
    env = dict(os.environ)
    env.update(kwargs.pop("env", {}))
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)] + [str(a) for a in args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    return proc.returncode, proc.stdout, proc.stderr


class TempProject(unittest.TestCase):
    """Base class giving each test an isolated project directory."""

    profile = "standard"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="adminwright-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.root = self.tmp / "project"
        self.root.mkdir()
        code, _out, err = run(
            "init",
            "--project-root", self.root,
            "--name", "TestPlatform",
            "--archetype", "b2b-saas",
            "--profile", self.profile,
        )
        self.assertEqual(code, 0, err)
        self.manifest = self.root / ".admin-console" / "manifest.json"

    def read(self):
        return json.loads(self.manifest.read_text(encoding="utf-8"))

    def write(self, data):
        self.manifest.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def validate(self, phase="release", profile=None):
        args = ["validate", "--manifest", self.manifest, "--project-root", self.root, "--phase", phase]
        if profile:
            args += ["--profile", profile]
        return run(*args)


# ---------------------------------------------------------------------------
# scaffolding
# ---------------------------------------------------------------------------


class TestInit(TempProject):
    def test_init_creates_v2_manifest_and_schema(self):
        data = self.read()
        self.assertEqual(data["manifestVersion"], "2.0")
        self.assertEqual(data["profile"], "standard")
        self.assertTrue((self.root / ".admin-console" / "admin-console.manifest.schema.json").exists())

    def test_plan_phase_passes_on_empty_model(self):
        code, out, _err = self.validate(phase="plan")
        self.assertEqual(code, 0, out)

    def test_release_phase_fails_on_empty_model(self):
        code, _out, _err = self.validate(phase="release")
        self.assertEqual(code, 1)

    def test_schema_and_template_agree(self):
        schema = json.loads((SKILL_ROOT / "assets" / "admin-console.manifest.schema.json").read_text(encoding="utf-8"))
        template = json.loads((SKILL_ROOT / "assets" / "admin-console.manifest.template.json").read_text(encoding="utf-8"))
        for key in schema["required"]:
            self.assertIn(key, template, "template is missing schema-required key " + key)


# ---------------------------------------------------------------------------
# incremental modelling — the deadlock that made the tool unusable
# ---------------------------------------------------------------------------


class TestIncrementalModelling(TempProject):
    """A role is unused until its first capability exists.

    Treating that as an error made `add` refuse the very first modelling step at
    the default profile, with no documented way through.
    """

    def add(self, kind, payload, entity=None):
        args = ["add", "--manifest", self.manifest, "--kind", kind, "--json", json.dumps(payload)]
        if entity:
            args += ["--entity", entity]
        return run(*args)

    def test_can_add_role_before_any_capability_exists(self):
        code, _out, err = self.add("role", {
            "id": "ops", "name": "Ops", "responsibilities": ["triage"],
            "scopes": ["all"], "mfaRequired": True,
        })
        self.assertEqual(code, 0, err)

    def test_can_add_entity_before_any_capability_exists(self):
        code, _out, err = self.add("entity", {
            "id": "user", "name": "User", "sourceOfTruth": "postgres:users",
            "sensitivity": "confidential", "tenantScoped": True,
            "lifecycleStates": ["active", "suspended"], "retention": "7y", "capabilities": [],
        })
        self.assertEqual(code, 0, err)

    def test_malformed_edit_is_still_refused(self):
        code, _out, err = self.add("role", {"id": "NOT VALID", "name": "x"})
        self.assertNotEqual(code, 0)
        self.assertIn("id", err.lower())

    def test_duplicate_id_is_refused(self):
        payload = {"id": "ops", "name": "Ops", "responsibilities": ["t"], "scopes": ["a"], "mfaRequired": True}
        self.assertEqual(self.add("role", payload)[0], 0)
        self.assertNotEqual(self.add("role", payload)[0], 0)


# ---------------------------------------------------------------------------
# the no-mock guarantee
# ---------------------------------------------------------------------------


class TestPlaceholderScanner(unittest.TestCase):
    """Unit-level checks on the scanner itself.

    Each 'flag' case is a bypass found by adversarial review; each 'pass' case is
    a legitimate identifier that a blunter rule would have falsely rejected.
    """

    def hit(self, text):
        folded = acm.fold_confusables(text)
        squashed = folded.replace(" ", "").replace("-", "").replace("_", "")
        return bool(
            acm.PLACEHOLDER_PATTERN.search(acm.split_identifiers(folded))
            or acm.substring_hit(squashed)
            or acm.HARDCODE_PATTERN.search(acm.split_identifiers(folded))
        )

    def test_flags_obvious_placeholders(self):
        for value in ("mockData", "FakeService", "stub_payment_gateway", "dummyUser", "hard-coded value"):
            self.assertTrue(self.hit(value), "should flag " + value)

    def test_flags_camelcase_embedded(self):
        for value in ("mockSuspendUser", "HTTPMockClient", "APIStubGateway", "getSampleData"):
            self.assertTrue(self.hit(value), "should flag " + value)

    def test_flags_concatenated_without_case_boundary(self):
        # Word boundaries miss these: no case transition to split on.
        for value in ("gmockRepositoryImpl", "usemockdata", "zmockAdapter", "xfakeHandler"):
            self.assertTrue(self.hit(value), "should flag " + value)

    def test_flags_unicode_homoglyphs(self):
        cyrillic_o = "postgres:users via mоckRepository.restore"
        self.assertTrue(self.hit(cyrillic_o), "Cyrillic homoglyph should not evade the scan")

    def test_does_not_flag_legitimate_identifiers(self):
        for value in (
            "postgres:orders via OrderRepository.findForAdmin",
            "randomizeOrder", "stubbornRetry", "hammockService", "SamplerConfig",
            "resampleAudio", "tests/e2e/admin/orders.spec.ts", "HTTPClient",
            "AccountService.suspend", "policy/user.admin",
        ):
            self.assertFalse(self.hit(value), "should not flag " + value)

    def test_nested_arrays_are_flattened(self):
        pairs = list(acm.flatten_strings("f", ["real op", ["mockHandler"]]))
        self.assertIn("mockHandler", [text for _path, text in pairs])

    def test_short_capability_ids_still_produce_tokens(self):
        tokens = acm.capability_tokens({"id": "a.b", "serverOperations": ["x"], "uiRoutes": []})
        self.assertTrue(tokens, "short ids must not silently disable evidence-token matching")


class TestEvidenceIntegrity(TempProject):
    def test_directory_is_not_evidence(self):
        target = self.root / "evidence-dir"
        (target / "nested").mkdir(parents=True)
        (target / "nested" / "unrelated.txt").write_text("changelog", encoding="utf-8")
        self.assertEqual(acm.path_state(target), "directory")

    def test_whitespace_only_file_is_empty(self):
        target = self.root / "blank.txt"
        target.write_text("   \n\t\n   \n", encoding="utf-8")
        self.assertEqual(acm.path_state(target), "empty")

    def test_evidence_file_content_is_scanned(self):
        target = self.root / "signoff.txt"
        target.write_text("TODO: replace with real results. Fake mock data for now.", encoding="utf-8")
        self.assertEqual(acm.path_state(target), "placeholder")

    def test_genuine_evidence_is_ok(self):
        target = self.root / "real.txt"
        target.write_text("axe-core: 0 violations on /admin/users\n", encoding="utf-8")
        self.assertEqual(acm.path_state(target), "ok")

    def test_path_traversal_outside_project_is_rejected(self):
        outside = self.tmp / "outside.txt"
        outside.write_text("real content", encoding="utf-8")
        self.assertTrue(acm.escapes_root(self.root / ".." / "outside.txt", self.root))

    def test_path_inside_project_is_allowed(self):
        inside = self.root / "inside.txt"
        inside.write_text("real content", encoding="utf-8")
        self.assertFalse(acm.escapes_root(inside, self.root))


# ---------------------------------------------------------------------------
# robustness — these used to emit tracebacks
# ---------------------------------------------------------------------------


class TestMalformedInput(TempProject):
    def assert_clean_usage_error(self, path):
        code, _out, err = run("validate", "--manifest", path, "--phase", "plan")
        self.assertEqual(code, 2, "expected usage-error exit code, got " + str(code))
        self.assertNotIn("Traceback", err)

    def test_non_utf8_manifest(self):
        target = self.tmp / "utf16.json"
        target.write_bytes(json.dumps({"manifestVersion": "2.0"}).encode("utf-16"))
        self.assert_clean_usage_error(target)

    def test_deeply_nested_json(self):
        target = self.tmp / "deep.json"
        target.write_text('{"a":' + "[" * 60000 + "]" * 60000 + "}", encoding="utf-8")
        self.assert_clean_usage_error(target)

    def test_truncated_json(self):
        target = self.tmp / "truncated.json"
        target.write_text('{"manifestVersion": "2.0",', encoding="utf-8")
        self.assert_clean_usage_error(target)

    def test_json_array_as_root(self):
        target = self.tmp / "array.json"
        target.write_text("[1, 2, 3]", encoding="utf-8")
        self.assert_clean_usage_error(target)

    def test_missing_file(self):
        self.assert_clean_usage_error(self.tmp / "nope.json")

    def test_wrong_types_do_not_crash(self):
        data = self.read()
        data["roles"] = "not-an-array"
        data["platform"] = "not-an-object"
        self.write(data)
        code, _out, err = self.validate(phase="plan")
        self.assertIn(code, (1, 2))
        self.assertNotIn("Traceback", err)


# ---------------------------------------------------------------------------
# multi-agent coordination
# ---------------------------------------------------------------------------


class TestClaims(TempProject):
    def setUp(self):
        super().setUp()
        data = self.read()
        data["roles"] = [{
            "id": "ops", "name": "Ops", "responsibilities": ["t"], "scopes": ["a"], "mfaRequired": True,
        }]
        data["entities"] = [{
            "id": "user", "name": "User", "sourceOfTruth": "postgres:users",
            "sensitivity": "internal", "tenantScoped": False,
            "lifecycleStates": ["active"], "retention": "7y",
            "capabilities": [{
                "id": "user.list", "outcome": "Find", "kind": "query", "roles": ["ops"],
                "risk": "low", "status": "planned", "rationale": "",
                "entityStates": {"from": ["active"], "to": []}, "uiRoutes": [],
                "serverOperations": ["UserRepo.find"], "authorizationPolicies": ["p"],
                "auditEvents": [], "safeguards": [],
                "dataBinding": "postgres:users via UserRepo.find", "tests": [], "evidence": [],
            }],
        }]
        self.write(data)

    def test_second_agent_cannot_steal_a_claim(self):
        first = run("claim", "--manifest", self.manifest, "--agent", "a1",
                    "--role", "implementer", "--capability", "user.list")
        self.assertEqual(first[0], 0, first[2])
        second = run("claim", "--manifest", self.manifest, "--agent", "a2",
                     "--role", "implementer", "--capability", "user.list")
        self.assertEqual(second[0], 3, "a conflicting claim must exit 3")

    def test_release_then_reclaim(self):
        run("claim", "--manifest", self.manifest, "--agent", "a1",
            "--role", "implementer", "--capability", "user.list")
        released = run("release-claim", "--manifest", self.manifest, "--agent", "a1")
        self.assertEqual(released[0], 0, released[2])
        again = run("claim", "--manifest", self.manifest, "--agent", "a2",
                    "--role", "implementer", "--capability", "user.list")
        self.assertEqual(again[0], 0, again[2])

    def test_force_steal_requires_reason(self):
        run("claim", "--manifest", self.manifest, "--agent", "a1",
            "--role", "implementer", "--capability", "user.list")
        code, _out, _err = run("claim", "--manifest", self.manifest, "--agent", "a2",
                               "--role", "qa", "--capability", "user.list", "--force-steal")
        self.assertEqual(code, 2, "--force-steal without --reason must be refused")


# ---------------------------------------------------------------------------
# cross-project learning
# ---------------------------------------------------------------------------


class TestGlobalLearning(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="adminwright-learn-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.store = self.tmp / "store"
        self.env = {"ADMINWRIGHT_HOME": str(self.store)}

    def make_project(self, name, observations):
        root = self.tmp / name
        root.mkdir()
        run("init", "--project-root", root, "--name", name, "--profile", "standard")
        manifest = root / ".admin-console" / "manifest.json"
        data = json.loads(manifest.read_text(encoding="utf-8"))
        for index, (category, observation, change) in enumerate(observations, start=1):
            data["feedback"].append({
                "id": "fb." + str(index), "observation": observation, "category": category,
                "proposedChange": change, "evidence": ["docs/incident.md"],
                "status": "open", "scope": "references/security-governance.md",
            })
        manifest.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return manifest

    def test_observation_seen_on_two_projects_clears_the_bar(self):
        shared = ("gap", "Bulk action authorized once for the batch",
                  "Require per-target authorization in bulk commands")
        one_off = ("tooling", "Seed data lacked a cross-tenant neighbour",
                   "Seed plan must include a cross-tenant neighbour")
        a = self.make_project("ProjectA", [shared])
        b = self.make_project("ProjectB", [shared, one_off])
        for manifest in (a, b):
            code, _out, err = run("harvest", "--manifest", manifest, "--date", "2026-08-07", env=self.env)
            self.assertEqual(code, 0, err)

        code, out, err = run("promote", "--json", env=self.env)
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        scopes = {c["proposedChange"] for c in payload["candidates"]}
        self.assertIn("Require per-target authorization in bulk commands", scopes)
        self.assertNotIn("Seed plan must include a cross-tenant neighbour", scopes,
                         "a single-project observation must not clear the bar")

    def test_harvest_is_idempotent(self):
        manifest = self.make_project("ProjectC", [("gap", "obs", "change")])
        run("harvest", "--manifest", manifest, "--date", "2026-08-07", env=self.env)
        code, out, _err = run("harvest", "--manifest", manifest, "--date", "2026-08-07", env=self.env)
        self.assertEqual(code, 0)
        self.assertIn("Harvested 0", out, "already-harvested feedback must not duplicate")


# ---------------------------------------------------------------------------
# honest tiering — the property the whole design rests on
# ---------------------------------------------------------------------------


class TestHonestTiering(TempProject):
    """A truthful build must be able to pass, or teams will falsify the manifest."""

    def build_honest_manifest(self):
        (self.root / "tests").mkdir(exist_ok=True)
        (self.root / "evidence").mkdir(exist_ok=True)
        (self.root / "tests" / "user.spec.ts").write_text(
            "test('user.list via /admin/users', () => { /* real assertions */ });", encoding="utf-8")
        for name in ("authentication", "authorization", "audit", "safety", "data",
                     "experience", "observability"):
            (self.root / "evidence" / (name + ".md")).write_text(
                "Recorded run output with assertions.", encoding="utf-8")
        for name in ("build", "typecheck", "lint", "tests", "browser", "security",
                     "accessibility", "performance"):
            (self.root / "evidence" / (name + ".log")).write_text(
                "command completed, exit 0", encoding="utf-8")

        data = self.read()
        data["platform"].update({
            "summary": "Internal operations console.",
            "operationalObjectives": ["Suspend abusive accounts without engineering"],
            "tenancy": "single-tenant", "sourceSystems": ["PostgreSQL primary"],
            "regulatedData": [],
            "stack": {"frontend": "React 18", "backend": "Node 20", "database": "PostgreSQL 16",
                      "auth": "OIDC", "jobs": "BullMQ", "hosting": "Fly.io",
                      "designSystem": "in-house", "adminFramework": "none"},
            "researchSources": [{"topic": "Express middleware",
                                 "url": "https://expressjs.com/en/guide/using-middleware.html",
                                 "appliedTo": ["user.list"], "checkedOn": "2026-08-07"}],
            "volumes": {"entityCounts": "40k users", "peakConcurrentOperators": "6",
                        "retentionHorizon": "7 years"},
        })
        data["roles"] = [{"id": "ops", "name": "Operations", "responsibilities": ["Admin"],
                          "scopes": ["all accounts"], "mfaRequired": True,
                          "separationOfDuties": []}]
        data["entities"] = [{
            "id": "user", "name": "User", "sourceOfTruth": "postgres:users",
            "sensitivity": "confidential", "tenantScoped": False,
            "lifecycleStates": ["active"], "lifecycleTransitions": [],
            "retention": "7 years", "capabilities": [{
                "id": "user.list", "outcome": "Find an account", "kind": "query",
                "roles": ["ops"], "risk": "low", "status": "implemented", "rationale": "",
                "entityStates": {"from": ["active"], "to": []},
                "uiRoutes": ["/admin/users"], "serverOperations": ["UserRepository.findForAdmin"],
                "authorizationPolicies": ["policy/user.admin"], "auditEvents": [],
                "safeguards": [], "dataBinding": "postgres:users via UserRepository.findForAdmin",
                "tests": ["tests/user.spec.ts"], "evidence": ["evidence/tests.log"],
                "owner": "impl", "reviewStatus": "reviewed",
            }],
        }]
        data["screens"] = [{
            "id": "users", "route": "/admin/users", "purpose": "Search accounts",
            "roles": ["ops"], "dataSources": ["postgres:users via UserRepository.findForAdmin"],
            "capabilities": ["user.list"], "actions": [],
            "states": ["loading", "populated", "error", "forbidden"],
            "responsive": True, "accessibilityStatus": "implemented",
            "status": "implemented", "rationale": "", "tests": ["tests/user.spec.ts"],
        }]
        for section, values in data["crossCutting"].items():
            for key, value in list(values.items()):
                if isinstance(value, bool):
                    values[key] = True
            if "evidence" in values:
                values["evidence"] = ["evidence/" + section + ".md"]
        data["crossCutting"]["authorization"]["policyTests"] = ["tests/user.spec.ts"]
        for gate in data["qualityGates"]:
            gate["status"] = "passed"
            gate["evidence"] = ["evidence/" + gate["id"] + ".log"]
            gate["threshold"] = "command exits zero"
        self.write(data)

    def test_truthful_build_passes_at_every_profile(self):
        self.build_honest_manifest()
        for profile in ("internal", "standard", "regulated"):
            code, out, err = self.validate(profile=profile)
            self.assertEqual(code, 0, "honest manifest must pass at " + profile + "\n" + out + err)

    def test_mock_data_fails_at_every_profile(self):
        self.build_honest_manifest()
        data = self.read()
        data["entities"][0]["capabilities"][0]["dataBinding"] = "mockUserRepository.findAll"
        self.write(data)
        for profile in ("internal", "standard", "regulated"):
            code, _out, _err = self.validate(profile=profile)
            self.assertEqual(code, 1, "mock data must fail at " + profile)

    def test_coverage_does_not_block_on_warnings_alone(self):
        self.build_honest_manifest()
        code, _out, err = run("coverage", "--manifest", self.manifest,
                              "--project-root", self.root, "--profile", "internal")
        self.assertEqual(code, 0, err)


if __name__ == "__main__":
    unittest.main()
