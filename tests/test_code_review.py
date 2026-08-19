# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TarikAI
"""Regression tests for code_review.py, the optional OCR bridge.

Standard library only (unittest), so `python -m unittest discover tests` works
anywhere the skill itself works. The `ocr` CLI is faked through the
ADMINWRIGHT_OCR_BIN hook: no network, no LLM, no provider configuration.

Run:  python -m unittest discover -s tests -v
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = SKILL_ROOT / "scripts" / "code_review.py"
MANIFEST_SCRIPT = SKILL_ROOT / "scripts" / "admin_console_manifest.py"

FAKE_PREVIEW = json.dumps(
    {
        "schema_version": "1",
        "mode": "workspace",
        "reviewable_files": [{"path": "src/orders.py", "status": "modified"}],
        "excluded_files": [],
    }
)

FAKE_FINDINGS = json.dumps(
    {
        "comments": [
            {
                "path": "src/orders.py",
                "content": "SQL built by string concatenation",
                "start_line": 10,
                "end_line": 10,
                "category": "security",
                "severity": "high",
            }
        ]
    }
)

FAKE_RULE = json.dumps({"schema_version": "1", "groups": []})

FAKE_OCR_SOURCE = (
    "import json, os, sys\n"
    "args = sys.argv[1:]\n"
    "if os.environ.get('FAKE_FAIL'):\n"
    "    sys.stderr.write('provider not configured\\n')\n"
    "    sys.exit(1)\n"
    "if args[:2] == ['delegate', 'preview']:\n"
    "    print(os.environ.get('FAKE_PREVIEW', " + repr(FAKE_PREVIEW) + "))\n"
    "elif args[:2] == ['delegate', 'rule']:\n"
    "    print(os.environ.get('FAKE_RULE', " + repr(FAKE_RULE) + "))\n"
    "elif args and args[0] in ('review', 'scan'):\n"
    "    print(os.environ.get('FAKE_FINDINGS', " + repr(FAKE_FINDINGS) + "))\n"
    "else:\n"
    "    sys.stderr.write('unexpected arguments: ' + repr(args) + '\\n')\n"
    "    sys.exit(1)\n"
)


def run(script, *args, **kwargs):
    """Invoke a skill script the way a user would, returning (exit, stdout, stderr)."""
    env = dict(os.environ)
    env.update(kwargs.pop("env", {}))
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(
        [sys.executable, str(script)] + [str(a) for a in args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    return proc.returncode, proc.stdout, proc.stderr


class TempConsole(unittest.TestCase):
    """Base class giving each test an isolated project with a real manifest."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="adminwright-ocr-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.root = self.tmp / "project"
        self.root.mkdir()
        code, _out, err = run(
            MANIFEST_SCRIPT,
            "init",
            "--project-root", self.root,
            "--name", "TestPlatform",
            "--archetype", "b2b-saas",
            "--profile", "standard",
        )
        self.assertEqual(code, 0, err)
        self.manifest = self.root / ".admin-console" / "manifest.json"
        self.launcher = self.make_fake_ocr()

    def make_fake_ocr(self):
        fake = self.tmp / "fake_ocr.py"
        fake.write_text(FAKE_OCR_SOURCE, encoding="utf-8")
        if os.name == "nt":
            launcher = self.tmp / "ocr.cmd"
            launcher.write_text(
                '@"{}" "{}" %*\n'.format(sys.executable, fake), encoding="utf-8"
            )
        else:
            launcher = self.tmp / "ocr"
            launcher.write_text(
                '#!/bin/sh\nexec "{}" "{}" "$@"\n'.format(sys.executable, fake),
                encoding="utf-8",
            )
            launcher.chmod(0o755)
        return launcher

    def review(self, *args, **kwargs):
        env = {"ADMINWRIGHT_OCR_BIN": str(self.launcher)}
        env.update(kwargs.pop("env", {}))
        return run(SCRIPT, *args, env=env, **kwargs)

    def gaps(self):
        return json.loads(self.manifest.read_text(encoding="utf-8")).get("gaps", [])


class AbsentOcr(TempConsole):
    def test_absent_exits_zero_with_notice(self):
        code, out, _err = run(
            SCRIPT, "diff", "--manifest", self.manifest,
            env={"ADMINWRIGHT_OCR_BIN": str(self.tmp / "not-installed.exe")},
        )
        self.assertEqual(code, 0)
        self.assertIn("not on PATH", out)

    def test_absent_strict_exits_two(self):
        code, _out, err = run(
            SCRIPT, "diff", "--manifest", self.manifest, "--strict",
            env={"ADMINWRIGHT_OCR_BIN": str(self.tmp / "not-installed.exe")},
        )
        self.assertEqual(code, 2)
        self.assertIn("--strict", err)


class DelegateDiff(TempConsole):
    def test_writes_bundle_with_instructions_and_next_command(self):
        code, out, err = self.review("diff", "--manifest", self.manifest)
        self.assertEqual(code, 0, err)
        bundle_path = self.root / ".admin-console" / "ocr-bundle.json"
        self.assertTrue(bundle_path.exists())
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
        self.assertEqual(bundle["engine"], "delegate")
        self.assertIn({"path": "src/orders.py", "status": "modified"}, bundle["reviewable_files"])
        self.assertIn("instructions", bundle)
        self.assertIn("severity", bundle["instructions"])
        self.assertIn("record", out)

    def test_empty_preview_reports_nothing_to_review(self):
        env = {"FAKE_PREVIEW": json.dumps({"schema_version": "1", "mode": "workspace", "reviewable_files": []})}
        code, out, _err = self.review("diff", "--manifest", self.manifest, env=env)
        self.assertEqual(code, 0)
        self.assertIn("nothing to review", out)

    def test_preview_failure_exits_two(self):
        code, _out, err = self.review(
            "diff", "--manifest", self.manifest, env={"FAKE_FAIL": "1"}
        )
        self.assertEqual(code, 2)
        self.assertIn("provider not configured", err)


class Record(TempConsole):
    def setUp(self):
        super().setUp()
        code, out, err = self.review("diff", "--manifest", self.manifest)
        self.assertEqual(code, 0, err)
        self.bundle_path = self.root / ".admin-console" / "ocr-bundle.json"

    def findings_payload(self, **overrides):
        finding = {
            "path": "src/orders.py",
            "content": "SQL built by string concatenation",
            "start_line": 10,
            "end_line": 10,
            "category": "security",
            "severity": "high",
        }
        finding.update(overrides)
        return {"findings": [finding], "skipped": []}

    def test_persists_gap_and_exits_one(self):
        code, out, _err = self.review(
            "record", "--manifest", self.manifest, "--findings", json.dumps(self.findings_payload())
        )
        self.assertEqual(code, 1)
        self.assertIn("coverage_rate=100%", out)
        gaps = self.gaps()
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0]["severity"], "high")
        self.assertIn("src/orders.py:10", gaps[0]["description"])
        self.assertIn("OCR delegate pass", gaps[0]["rationale"])
        self.assertIn(".admin-console/ocr-bundle.json", gaps[0]["evidence"])

    def test_refuses_silent_omission(self):
        payload = {"findings": [], "skipped": []}
        code, _out, err = self.review(
            "record", "--manifest", self.manifest, "--findings", json.dumps(payload)
        )
        self.assertEqual(code, 2)
        self.assertIn("neither reviewed nor skipped", err)
        self.assertEqual(self.gaps(), [])

    def test_malformed_payloads_exit_two_without_traceback(self):
        payloads = [
            '{"findings": [{"content": "x", "severity": "high", "category": "bug"}]}',
            '{"findings": ["bare string"]}',
            '{"findings": {"path": "src/orders.py"}}',
        ]
        for payload in payloads:
            code, _out, err = self.review(
                "record", "--manifest", self.manifest, "--findings", payload
            )
            self.assertEqual(code, 2, payload)
            self.assertIn("ERROR:", err, payload)
            self.assertNotIn("Traceback", err, payload)
            self.assertEqual(self.gaps(), [])

    def test_findings_file_missing_exits_two(self):
        code, _out, err = self.review(
            "record", "--manifest", self.manifest,
            "--findings", "@" + str(self.tmp / "no-such-file.json"),
        )
        self.assertEqual(code, 2)
        self.assertIn("could not read findings file", err)
        self.assertNotIn("Traceback", err)

    def test_corrupt_bundle_exits_two(self):
        self.bundle_path.write_text("{not json", encoding="utf-8")
        code, _out, err = self.review(
            "record", "--manifest", self.manifest,
            "--findings", json.dumps(self.findings_payload()),
        )
        self.assertEqual(code, 2)
        self.assertIn("ERROR:", err)
        self.assertIn("bundle", err)
        self.assertNotIn("Traceback", err)
        self.assertEqual(self.gaps(), [])

    def test_duplicate_bundle_paths_count_once(self):
        env = {"FAKE_PREVIEW": json.dumps({
            "schema_version": "1", "mode": "workspace",
            "reviewable_files": [
                {"path": "src/orders.py", "status": "deleted"},
                {"path": "src/orders.py", "status": "untracked"},
            ],
        })}
        code, _out, err = self.review("diff", "--manifest", self.manifest, env=env)
        self.assertEqual(code, 0, err)
        payload = {"findings": [], "skipped": [{"path": "src/orders.py", "reason": "recreated untracked"}]}
        code, out, _err = self.review(
            "record", "--manifest", self.manifest, "--findings", json.dumps(payload)
        )
        self.assertEqual(code, 0)
        self.assertIn("total_files=1", out)
        self.assertIn("coverage_rate=100%", out)
        self.assertEqual(self.gaps(), [])

    def test_paths_outside_bundle_refused(self):
        payload = self.findings_payload(path="totally/invented.py")
        payload["skipped"] = [{"path": "src/orders.py", "reason": "vendored"}]
        code, _out, err = self.review(
            "record", "--manifest", self.manifest, "--findings", json.dumps(payload)
        )
        self.assertEqual(code, 2)
        self.assertIn("not in the review bundle", err)
        self.assertIn("totally/invented.py", err)
        self.assertEqual(self.gaps(), [])

    def test_unknown_category_records_as_other_with_notice(self):
        payload = self.findings_payload(category="vibe")
        code, _out, err = self.review(
            "record", "--manifest", self.manifest, "--findings", json.dumps(payload)
        )
        self.assertEqual(code, 1)
        self.assertIn("recorded as 'other'", err)
        self.assertIn("(other, high)", self.gaps()[0]["description"])

    def test_repeated_records_never_exhaust_ids(self):
        payload = json.dumps(self.findings_payload())
        for run_index in range(6):
            code, _out, _err = self.review("record", "--manifest", self.manifest, "--findings", payload)
            self.assertEqual(code, 1, "run " + str(run_index + 1))
        ids = [gap["id"] for gap in self.gaps()]
        self.assertEqual(len(ids), 6)
        self.assertEqual(len(set(ids)), 6)

    def test_invalid_finding_in_batch_persists_nothing(self):
        good = self.findings_payload()["findings"][0]
        bad = self.findings_payload(severity="catastrophic")["findings"][0]
        payload = {"findings": [good, bad], "skipped": []}
        code, _out, err = self.review(
            "record", "--manifest", self.manifest, "--findings", json.dumps(payload)
        )
        self.assertEqual(code, 2)
        self.assertIn("severity", err)
        self.assertEqual(self.gaps(), [])

    def test_skipped_with_reason_satisfies_coverage(self):
        payload = {"findings": [], "skipped": [{"path": "src/orders.py", "reason": "generated code"}]}
        code, out, _err = self.review(
            "record", "--manifest", self.manifest, "--findings", json.dumps(payload)
        )
        self.assertEqual(code, 0)
        self.assertIn("skipped_files=1", out)
        self.assertEqual(self.gaps(), [])

    def test_rejects_unknown_severity(self):
        payload = self.findings_payload(severity="catastrophic")
        code, _out, err = self.review(
            "record", "--manifest", self.manifest, "--findings", json.dumps(payload)
        )
        self.assertEqual(code, 2)
        self.assertIn("severity", err)
        self.assertEqual(self.gaps(), [])

    def test_second_run_gets_suffixed_id(self):
        payload = json.dumps(self.findings_payload())
        code, _out, _err = self.review("record", "--manifest", self.manifest, "--findings", payload)
        self.assertEqual(code, 1)
        code, _out, _err = self.review("record", "--manifest", self.manifest, "--findings", payload)
        self.assertEqual(code, 1)
        ids = [gap["id"] for gap in self.gaps()]
        self.assertEqual(len(ids), 2)
        self.assertNotEqual(ids[0], ids[1])


    def test_preview_entry_without_path_exits_two(self):
        env = {"FAKE_PREVIEW": json.dumps({
            "schema_version": "1", "mode": "workspace",
            "reviewable_files": [{"status": "modified"}],
        })}
        code, _out, err = self.review("diff", "--manifest", self.manifest, env=env)
        self.assertEqual(code, 2)
        self.assertIn("without a path", err)


class ConcurrentRecord(TempConsole):
    """qa and security may both run the OCR pass over one domain.

    Ids are chosen from an unlocked read, so two records of the same file race
    for the same id. Before the retry, five of six concurrent findings were
    lost to a message that blamed the payload.
    """

    def test_concurrent_records_of_one_file_all_persist(self):
        def record(index):
            payload = {"findings": [{
                "path": "src/shared.py", "content": "finding from agent %d" % index,
                "start_line": index + 1, "category": "bug", "severity": "low",
            }]}
            return self.review(
                "record", "--manifest", self.manifest, "--findings", json.dumps(payload)
            )[0]

        with ThreadPoolExecutor(max_workers=6) as pool:
            codes = list(pool.map(record, range(6)))
        gaps = self.gaps()
        self.assertEqual(codes, [1] * 6, "every concurrent record must persist")
        self.assertEqual(len(gaps), 6)
        self.assertEqual(len({gap["id"] for gap in gaps}), 6, "ids must stay unique")


class LineAnchors(TempConsole):
    """The line anchor is what this pass guarantees; never drop it silently."""

    def record(self, **overrides):
        finding = {"path": "src/orders.py", "content": "c",
                   "category": "bug", "severity": "low"}
        finding.update(overrides)
        return self.review("record", "--manifest", self.manifest,
                           "--findings", json.dumps({"findings": [finding]}))

    def test_string_line_number_keeps_the_anchor(self):
        # LLM-authored JSON emits "10" as often as 10.
        code, _out, err = self.record(start_line="10")
        self.assertEqual(code, 1, err)
        self.assertIn("src/orders.py:10", self.gaps()[0]["description"])

    def test_integer_line_number_still_works(self):
        code, _out, err = self.record(start_line=10)
        self.assertEqual(code, 1, err)
        self.assertIn("src/orders.py:10", self.gaps()[0]["description"])

    def test_non_numeric_line_degrades_to_unknown(self):
        code, _out, err = self.record(start_line="somewhere")
        self.assertEqual(code, 1, err)
        self.assertIn("src/orders.py:?", self.gaps()[0]["description"])

    def test_boolean_is_not_a_line_number(self):
        code, _out, err = self.record(start_line=True)
        self.assertEqual(code, 1, err)
        self.assertIn("src/orders.py:?", self.gaps()[0]["description"])


class EndpointEngine(TempConsole):
    def test_review_persists_gap_and_keeps_raw_evidence(self):
        code, out, err = self.review("diff", "--manifest", self.manifest, "--engine", "ocr")
        self.assertEqual(code, 1, err)
        self.assertIn("finding(s) recorded", out)
        gaps = self.gaps()
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0]["severity"], "high")
        evidence = [p for p in gaps[0]["evidence"] if "ocr-diff-" in p]
        self.assertTrue(evidence)
        self.assertTrue((self.root / evidence[0]).exists())

    def test_review_failure_exits_two_with_hint(self):
        code, _out, err = self.review(
            "diff", "--manifest", self.manifest, "--engine", "ocr", env={"FAKE_FAIL": "1"}
        )
        self.assertEqual(code, 2)
        self.assertIn("ocr config provider", err)

    def test_scan_persists_gap_in_scan_mode(self):
        code, out, err = self.review("scan", "--manifest", self.manifest)
        self.assertEqual(code, 1, err)
        self.assertIn("scan", out)
        gaps = self.gaps()
        self.assertEqual(len(gaps), 1)
        self.assertIn("scan mode", gaps[0]["rationale"])


@unittest.skipUnless(shutil.which("ocr"), "the real ocr CLI is not installed")
class RealCliSmoke(unittest.TestCase):
    """Check the command surface against a real ocr install when one exists.

    Every other test fakes the binary through ADMINWRIGHT_OCR_BIN and accepts
    any flags; this is the only guard against a renamed flag or subcommand
    reaching users. Skipped, not failed, where ocr is absent — the bridge
    itself is optional.
    """

    def help_text(self, *args):
        executable = shutil.which("ocr")
        proc = subprocess.run(
            [executable, *args, "--help"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        self.assertEqual(proc.returncode, 0, "ocr " + " ".join(args) + " --help failed")
        return (proc.stdout or "") + (proc.stderr or "")

    def test_flags_the_bridge_passes_exist(self):
        for args, flags in (
            (("review",), ("--audience", "--format", "--rule")),
            (("scan",), ("--audience", "--format", "--rule")),
            (("delegate", "preview"), ("--format", "--rule")),
            (("delegate", "rule"), ("--format", "--rule")),
        ):
            text = self.help_text(*args)
            for flag in flags:
                self.assertIn(flag, text, flag + " missing from ocr " + " ".join(args))

    def test_helper_subcommands_from_integrations_exist(self):
        self.assertIn("set", self.help_text("config"))
        self.assertIn("check", self.help_text("rules"))


if __name__ == "__main__":
    unittest.main()
