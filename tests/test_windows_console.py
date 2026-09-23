# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TarikAI
"""Windows console and PowerShell regressions (audit batch 3).

With stdout a pipe -- every agent harness -- Python on Windows uses cp1252, and the
whole suite used to force `PYTHONIOENCODING=utf-8`, which hid the crash. These tests
run the real script under a cp1252 console with characters cp1252 cannot hold.
"""

import codecs
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from cli_helpers import TempProject, run

NAME = "KeyRouter \U0001F511 Schlüssel 日本語"


class TestNonCp1252Output(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="adminwright-win-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.root = self.tmp / "project"
        self.root.mkdir()
        self.store = self.tmp / "store"
        code, _o, err = run("init", "--project-root", self.root, "--name", NAME,
                            "--archetype", "b2b-saas")
        self.assertEqual(code, 0, err)
        self.manifest = self.root / ".admin-console" / "manifest.json"
        role = {"id": "ops", "name": "Ops \U0001F527 Betreiber", "responsibilities": ["Admin"],
                "scopes": ["all"]}
        code, _o, err = run("add", "--manifest", self.manifest, "--kind", "role",
                            "--json", json.dumps(role))
        self.assertEqual(code, 0, err)

    def assert_clean(self, args, expected=(0,), **kwargs):
        code, out, err = run(*args, encoding="cp1252", **kwargs)
        self.assertNotIn("Traceback", err, " ".join(map(str, args)))
        self.assertNotIn("UnicodeEncodeError", err)
        self.assertIn(code, expected, " ".join(map(str, args)) + "\n" + out + err)
        return out

    def test_every_reporting_command_survives_a_cp1252_console(self):
        m = self.manifest
        out = self.assert_clean(["report", "--manifest", m])
        self.assertIn("\U0001F511", out, "the platform name must arrive intact, not mangled")
        self.assert_clean(["validate", "--manifest", m, "--phase", "plan"])
        self.assert_clean(["validate", "--manifest", m, "--phase", "release",
                           "--project-root", self.root], expected=(1,))
        payload = self.assert_clean(["validate", "--manifest", m, "--json"])
        json.loads(payload)
        self.assert_clean(["coverage", "--manifest", m], expected=(0, 1))
        for fmt in ("operator-handbook", "authz-matrix", "gap-report", "nav-map",
                    "seed-plan", "test-plan"):
            self.assert_clean(["emit", "--manifest", m, "--format", fmt])

    def test_harvest_promote_and_lessons_survive_a_cp1252_console(self):
        fb = {"id": "fb-001", "observation": "Schlüssel \U0001F511 rotation missing",
              "category": "gap", "proposedChange": "add a rotation step 日本語",
              "evidence": ["evidence/x.log"]}
        code, _o, err = run("add", "--manifest", self.manifest, "--kind", "feedback",
                            "--json", json.dumps(fb))
        self.assertEqual(code, 0, err)
        out = self.assert_clean(["harvest", "--manifest", self.manifest, "--store", self.store,
                                 "--date", "2026-09-23"])
        self.assertIn("Harvested 1", out)
        self.assert_clean(["promote", "--store", self.store, "--min-projects", "1"])
        lessons = self.tmp / "lessons"
        self.assert_clean(["lesson", "add", "--title", "Rotate \U0001F511 keys", "--category", "gap",
                           "--scope", "SKILL.md", "--trigger", "t 日本語", "--rule", "r",
                           "--date", "2026-09-23", "--lessons-dir", lessons])
        self.assert_clean(["lesson", "list", "--lessons-dir", lessons])
        again = self.assert_clean(["harvest", "--manifest", self.manifest, "--store", self.store,
                                   "--date", "2026-09-23"])
        self.assertIn("Harvested 0", again)


class TestPowerShellJson(TempProject):
    """3.2 -- the encodings and quoting PowerShell 5.1 actually produces."""

    ROLE = {"id": "ops", "name": "Ops team", "responsibilities": ["Admin"], "scopes": ["all"]}

    def write_payload(self, data, bom=None):
        path = self.tmp / "payload.json"
        body = json.dumps(data).encode("utf-8")
        if bom == "utf-8":
            body = codecs.BOM_UTF8 + body
        elif bom == "utf-16":
            body = codecs.BOM_UTF16_LE + json.dumps(data).encode("utf-16-le")
        path.write_bytes(body)
        return path

    def test_json_file_with_a_utf8_bom_is_accepted(self):
        path = self.write_payload(self.ROLE, bom="utf-8")
        code, _o, err = self.cli("add", "--kind", "role", "--json", "@" + str(path))
        self.assertEqual(code, 0, err)
        self.assertEqual(self.read()["roles"][0]["id"], "ops")

    def test_json_file_option_is_the_powershell_safe_form(self):
        path = self.write_payload(self.ROLE, bom="utf-8")
        code, _o, err = self.cli("add", "--kind", "role", "--json-file", str(path))
        self.assertEqual(code, 0, err)

    def test_utf16_json_file_is_accepted(self):
        path = self.write_payload(self.ROLE, bom="utf-16")
        code, _o, err = self.cli("add", "--kind", "role", "--json", "@" + str(path))
        self.assertEqual(code, 0, err)

    def test_stdin_with_a_bom_is_accepted(self):
        code, _o, err = run("add", "--manifest", self.manifest, "--kind", "role", "--json", "-",
                            input="﻿" + json.dumps(self.ROLE))
        self.assertEqual(code, 0, err)

    def test_quote_stripped_json_gets_a_hint_not_a_traceback(self):
        # what PowerShell 5.1 delivers for '{"id":"ops"}'
        code, _o, err = self.cli("add", "--kind", "role", "--json", "{id:ops,name:Ops}")
        self.assertEqual(code, 2)
        self.assertIn("--json-file", err)
        self.assertNotIn("Traceback", err)

    def test_a_manifest_with_a_bom_validates_and_is_rewritten_without_one(self):
        raw = self.manifest.read_bytes()
        self.manifest.write_bytes(codecs.BOM_UTF8 + raw)
        code, out, err = self.validate(phase="plan")
        self.assertEqual(code, 0, out + err)
        code, _o, err = self.cli("add", "--kind", "role", "--json", json.dumps(self.ROLE))
        self.assertEqual(code, 0, err)
        self.assertFalse(self.manifest.read_bytes().startswith(codecs.BOM_UTF8))

    def test_a_utf16_manifest_is_a_clean_usage_error(self):
        self.manifest.write_bytes(codecs.BOM_UTF16_LE + self.manifest.read_text(encoding="utf-8").encode("utf-16-le"))
        code, _o, err = self.validate(phase="plan")
        self.assertEqual(code, 2)
        self.assertIn("Re-save it as UTF-8", err)
        self.assertNotIn("Traceback", err)


class TestBackslashPaths(TempProject):
    """3.5 -- a Windows-written manifest must validate the same everywhere."""

    def test_add_normalises_backslashes_in_evidence_paths(self):
        gap = {"id": "gap.one", "severity": "low", "description": "d", "status": "open",
               "rationale": "", "evidence": ["evidence\\build.log", "https://example.test/a\\b"]}
        code, _o, err = self.cli("add", "--kind", "gap", "--json", json.dumps(gap))
        self.assertEqual(code, 0, err)
        self.assertIn("rewrote backslashes", err)
        stored = self.read()["gaps"][0]["evidence"]
        self.assertEqual(stored[0], "evidence/build.log")
        self.assertEqual(stored[1], "https://example.test/a\\b", "URLs are not paths")

    def test_set_normalises_backslashes_in_evidence_paths(self):
        data = self.build_honest_manifest()
        code, _o, err = self.cli("set", "--path", "qualityGates[build].evidence",
                                 "--value", '["evidence\\\\build.log"]')
        self.assertEqual(code, 0, err)
        self.assertIn("rewrote backslashes", err)
        self.assertEqual(self.read()["qualityGates"][0]["evidence"], ["evidence/build.log"])
        self.assertTrue(data)

    def test_a_backslash_path_already_in_a_manifest_still_resolves(self):
        data = self.build_honest_manifest()
        data["qualityGates"][0]["evidence"] = ["evidence\\build.log"]
        self.write(data)
        code, out, err = self.validate()
        self.assertEqual(code, 0, out + err)


if __name__ == "__main__":
    unittest.main()
