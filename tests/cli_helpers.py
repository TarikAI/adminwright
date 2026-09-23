# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TarikAI
"""Shared helpers for the CLI-level tests.

The tests spawn the real script rather than calling internals, so the write
guard, exit codes and console encoding are exercised the way an agent harness
exercises them. Standard library only.
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
    """Invoke the CLI the way a user would, returning (exit_code, stdout, stderr).

    The child's console is cp1252 unless a test says otherwise. That is what a
    Windows agent harness gives a script whose stdout is a pipe, and it is what
    hid every encoding crash for as long as the suite forced utf-8. The script
    reconfigures itself to UTF-8, so output is always decodable here.
    """
    env = dict(os.environ)
    env.update(kwargs.pop("env", {}))
    env["PYTHONIOENCODING"] = kwargs.pop("encoding", "cp1252")
    cwd = kwargs.pop("cwd", None)
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)] + [str(a) for a in args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        cwd=str(cwd) if cwd else None,
        input=kwargs.pop("input", None),
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
            # Previewing another tier is what the flag is for; lowering it below the
            # manifest's own profile at release is refused unless asked for.
            args += ["--profile", profile, "--allow-lower-profile"]
        return run(*args)

    def cli(self, *args, **kwargs):
        """Run a subcommand against this project's manifest."""
        return run(args[0], "--manifest", self.manifest, *args[1:], **kwargs)

    def rules(self, phase="release", profile=None):
        """Return {rule: severity} from `validate --json`."""
        argv = ["validate", "--manifest", self.manifest, "--project-root", self.root,
                "--phase", phase, "--json"]
        if profile:
            argv += ["--profile", profile, "--allow-lower-profile"]
        _code, out, err = run(*argv)
        try:
            payload = json.loads(out)
        except ValueError:
            self.fail("validate --json did not return JSON:\n" + out + err)
        found = {}
        for item in payload["findings"]:
            if found.get(item["rule"]) != "error":
                found[item["rule"]] = item["severity"]
        return found

    def build_honest_manifest(self):
        """A one-capability console that passes release at every profile.

        The reference point for the bypass tests: each takes this manifest, breaks
        exactly one thing, and asserts the gate now refuses it.
        """
        (self.root / "tests").mkdir(exist_ok=True)
        (self.root / "evidence").mkdir(exist_ok=True)
        (self.root / "tests" / "user.spec.ts").write_text(
            "test('user.list via /admin/users', async () => {\n"
            "  expect((await listUsers({tenantId: 't-1'})).length).toBe(3);\n"
            "});\n", encoding="utf-8")
        (self.root / "tests" / "authz.spec.ts").write_text(
            "test('role matrix', () => {\n"
            "  expect(can('support', 'user.list', {})).toBe(false);\n"
            "  expect(can('ops', 'user.list', {})).toBe(true);\n"
            "});\n", encoding="utf-8")
        for name in ("authentication", "authorization", "audit", "safety", "data",
                     "experience", "observability"):
            (self.root / "evidence" / (name + ".md")).write_text(
                name + " review 2026-08-20\n"
                "- 12 operations exercised, 12 enforced server-side\n"
                "- 0 findings outstanding\n", encoding="utf-8")
        for name in ("build", "typecheck", "lint", "tests", "browser", "security",
                     "accessibility", "performance"):
            (self.root / "evidence" / (name + ".log")).write_text(
                name + " run\n  0 errors across 118 files\n", encoding="utf-8")

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
        for decision in data["decisions"]:
            if decision["id"] == "dec.profile":
                # init records the profile as an assumed decision; an honest build
                # confirms it with the owner.
                decision["status"] = "confirmed"
                decision["reason"] = "Internal staff only, no regulated data (confirmed by the owner)"
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
                "owner": "impl", "implementedBy": "impl", "reviewStatus": "reviewed",
                "reviewedBy": "qa",
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
        data["crossCutting"]["authorization"]["policyTests"] = ["tests/authz.spec.ts"]
        for gate in data["qualityGates"]:
            gate["status"] = "passed"
            gate["evidence"] = ["evidence/" + gate["id"] + ".log"]
            gate["threshold"] = "command exits zero"
        self.write(data)
        return data

    def capability_of(self, data, cid="user.list"):
        for entity in data["entities"]:
            for capability in entity["capabilities"]:
                if capability["id"] == cid:
                    return capability
        raise KeyError(cid)
