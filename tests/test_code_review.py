# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TarikAI
"""Regression tests for code_review.py, the optional OCR bridge.

Standard library only (unittest), so `python -m unittest discover tests` works
anywhere the skill itself works. The `ocr` CLI is faked through the
ADMINWRIGHT_OCR_BIN hook: no network, no LLM, no provider configuration. The
real `ocr` package is never run (RealCliSmoke is opt-in). On Windows the fake is
laid out like an npm install (`ocr.cmd` shim plus a `node_modules/.../ocr.js`
entry point) and ADMINWRIGHT_NODE_BIN points the resolved launcher at this
Python, so the tests need neither node nor cmd.exe's cooperation.

ADMINWRIGHT_HOME is pointed at a scratch directory so nothing touches the
user's home.

Run:  python -m unittest discover -s tests -v
"""

import atexit
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

SKILL_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = SKILL_ROOT / "scripts" / "code_review.py"
MANIFEST_SCRIPT = SKILL_ROOT / "scripts" / "admin_console_manifest.py"


def load_bridge():
    spec = importlib.util.spec_from_file_location("code_review_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bridge = load_bridge()

_SCRATCH_HOME = []


def scratch_home():
    """One ADMINWRIGHT_HOME for the whole test run, removed at exit."""
    if not _SCRATCH_HOME:
        home = tempfile.mkdtemp(prefix="adminwright-ocr-home-")
        atexit.register(shutil.rmtree, home, ignore_errors=True)
        _SCRATCH_HOME.append(home)
    return _SCRATCH_HOME[0]

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
    "import json, os, sys, time\n"
    "args = sys.argv[1:]\n"
    "log = os.environ.get('FAKE_ARGV_LOG')\n"
    "if log:\n"
    "    with open(log, 'a', encoding='utf-8') as handle:\n"
    "        handle.write(json.dumps({'args': args,"
    " 'no_update': os.environ.get('OCR_NO_UPDATE')}) + '\\n')\n"
    "if os.environ.get('FAKE_SLEEP'):\n"
    "    time.sleep(float(os.environ['FAKE_SLEEP']))\n"
    "if os.environ.get('FAKE_FAIL'):\n"
    "    sys.stderr.write('provider not configured\\n')\n"
    "    sys.exit(1)\n"
    "if args[:2] == ['delegate', 'preview']:\n"
    "    print(os.environ.get('FAKE_PREVIEW', " + repr(FAKE_PREVIEW) + "))\n"
    "elif args[:2] == ['delegate', 'rule']:\n"
    "    print(os.environ.get('FAKE_RULE', " + repr(FAKE_RULE) + "))\n"
    "elif args and args[0] in ('review', 'scan'):\n"
    "    print(os.environ.get('FAKE_FINDINGS', " + repr(FAKE_FINDINGS) + "))\n"
    "    sys.exit(int(os.environ.get('FAKE_RC', '0')))\n"
    "else:\n"
    "    sys.stderr.write('unexpected arguments: ' + repr(args) + '\\n')\n"
    "    sys.exit(1)\n"
)

# What `npm install -g` writes for a package whose bin is bin/ocr.js
# (cmd-shim's current output), pointing at the entry point below it.
NPM_SHIM = (
    "@ECHO off\r\n"
    "GOTO start\r\n"
    ":find_dp0\r\n"
    "SET dp0=%~dp0\r\n"
    "EXIT /b\r\n"
    ":start\r\n"
    "SETLOCAL\r\n"
    "CALL :find_dp0\r\n"
    "\r\n"
    'IF EXIST "%dp0%\\node.exe" (\r\n'
    '  SET "_prog=%dp0%\\node.exe"\r\n'
    ") ELSE (\r\n"
    '  SET "_prog=node"\r\n'
    "  SET PATHEXT=%PATHEXT:;.JS;=;%\r\n"
    ")\r\n"
    "\r\n"
    'endLocal & goto #_undefined_# 2>NUL || title %COMSPEC% & "%_prog%"  '
    '"%dp0%\\node_modules\\@alibaba-group\\open-code-review\\bin\\ocr.js" %*\r\n'
)
NPM_SHIM_ENTRY = ("node_modules", "@alibaba-group", "open-code-review", "bin", "ocr.js")

WINDOWS_ONLY = unittest.skipUnless(os.name == "nt", "Windows-only behaviour (cmd.exe shims)")


def run(script, *args, **kwargs):
    """Invoke a skill script the way a user would, returning (exit, stdout, stderr).

    env values of None remove the variable; `input` feeds stdin.
    """
    env = dict(os.environ)
    for key, value in kwargs.pop("env", {}).items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("ADMINWRIGHT_HOME", scratch_home())
    proc = subprocess.run(
        [sys.executable, str(script)] + [str(a) for a in args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        input=kwargs.pop("input", None),
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
        self.argv_log = self.tmp / "argv.jsonl"
        self.launcher = self.make_fake_ocr()

    def make_fake_ocr(self):
        if os.name == "nt":
            # An npm-shaped install: ocr.cmd next to node_modules/.../ocr.js.
            # The bridge must resolve the shim to `node ocr.js`; the entry
            # point is Python source and ADMINWRIGHT_NODE_BIN (see review())
            # makes this interpreter stand in for node.
            home = self.tmp / "npm"
            entry = home.joinpath(*NPM_SHIM_ENTRY)
            entry.parent.mkdir(parents=True)
            entry.write_text(FAKE_OCR_SOURCE, encoding="utf-8")
            launcher = home / "ocr.cmd"
            launcher.write_bytes(NPM_SHIM.encode("ascii"))
        else:
            fake = self.tmp / "fake_ocr.py"
            fake.write_text(FAKE_OCR_SOURCE, encoding="utf-8")
            launcher = self.tmp / "ocr"
            launcher.write_text(
                '#!/bin/sh\nexec "{}" "{}" "$@"\n'.format(sys.executable, fake),
                encoding="utf-8",
            )
            launcher.chmod(0o755)
        return launcher

    def review(self, *args, **kwargs):
        env = {
            "ADMINWRIGHT_OCR_BIN": str(self.launcher),
            "FAKE_ARGV_LOG": str(self.argv_log),
        }
        if os.name == "nt":
            env["ADMINWRIGHT_NODE_BIN"] = sys.executable
        env.update(kwargs.pop("env", {}))
        return run(SCRIPT, *args, env=env, **kwargs)

    def gaps(self):
        return json.loads(self.manifest.read_text(encoding="utf-8")).get("gaps", [])

    def ocr_calls(self):
        """Every argv the fake ocr saw, as parsed log entries."""
        if not self.argv_log.exists():
            return []
        return [
            json.loads(line)
            for line in self.argv_log.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]


class AbsentOcr(TempConsole):
    """ocr genuinely not installed: no override, nothing named ocr on PATH."""

    def absent(self, *args):
        empty = self.tmp / "empty-path"
        empty.mkdir()
        return run(
            SCRIPT, *args,
            env={"ADMINWRIGHT_OCR_BIN": None, "PATH": str(empty)},
        )

    def test_absent_exits_zero_with_notice(self):
        code, out, _err = self.absent("diff", "--manifest", self.manifest)
        self.assertEqual(code, 0)
        self.assertIn("not on PATH", out)

    def test_absent_strict_exits_two(self):
        code, _out, err = self.absent("diff", "--manifest", self.manifest, "--strict")
        self.assertEqual(code, 2)
        self.assertIn("--strict", err)


class WrongOverride(TempConsole):
    """A wrong ADMINWRIGHT_OCR_BIN is a misconfiguration, never "ocr absent"."""

    def test_named_file_that_does_not_exist_exits_two_naming_the_variable(self):
        for extra in ((), ("--strict",)):
            code, out, err = run(
                SCRIPT, "diff", "--manifest", self.manifest, *extra,
                env={"ADMINWRIGHT_OCR_BIN": str(self.tmp / "not-installed.exe")},
            )
            self.assertEqual(code, 2, extra)
            self.assertIn("ADMINWRIGHT_OCR_BIN", err)
            self.assertIn("not-installed.exe", err)
            self.assertNotIn("not on PATH", err + out)
            self.assertNotIn("Traceback", err)

    def test_scan_reports_the_same_way(self):
        code, _out, err = run(
            SCRIPT, "scan", "--manifest", self.manifest,
            env={"ADMINWRIGHT_OCR_BIN": str(self.tmp / "nope")},
        )
        self.assertEqual(code, 2)
        self.assertIn("ADMINWRIGHT_OCR_BIN", err)

    def test_a_directory_is_not_an_executable(self):
        code, _out, err = run(
            SCRIPT, "diff", "--manifest", self.manifest,
            env={"ADMINWRIGHT_OCR_BIN": str(self.tmp)},
        )
        self.assertEqual(code, 2)
        self.assertIn("ADMINWRIGHT_OCR_BIN", err)


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


class ShimResolution(unittest.TestCase):
    """resolve_cmd_shim / ocr_executable in-process, so POSIX runs them too.

    The Windows-only end-to-end behaviour is in WindowsShim; the parsing and
    the refusal rules do not depend on the OS and are pinned here everywhere.
    """

    NPM6_SHIM = (
        '@IF EXIST "%~dp0\\node.exe" (\r\n'
        '  "%~dp0\\node.exe"  "%~dp0\\node_modules\\@alibaba-group\\open-code-review'
        '\\bin\\ocr.js" %*\r\n'
        ") ELSE (\r\n"
        "  @SETLOCAL\r\n"
        "  @SET PATHEXT=%PATHEXT:;.JS;=;%\r\n"
        '  node  "%~dp0\\node_modules\\@alibaba-group\\open-code-review\\bin\\ocr.js" %*\r\n'
        ")\r\n"
    )

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="adminwright-shim-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.home = self.tmp / "npm"
        self.home.mkdir()
        self.entry = self.home.joinpath(*NPM_SHIM_ENTRY)
        self.entry.parent.mkdir(parents=True)
        self.entry.write_text("// entry", encoding="utf-8")
        self.shim = self.home / "ocr.cmd"

    def resolve(self, text=NPM_SHIM, env=None):
        self.shim.write_bytes(text.encode("ascii"))
        base = {"ADMINWRIGHT_NODE_BIN": sys.executable}
        base.update(env or {})  # "" means "no override"; find_node ignores it
        with mock.patch.dict(os.environ, base):
            return bridge.resolve_cmd_shim(self.shim)

    def test_current_npm_shim_resolves_to_node_and_entry_point(self):
        self.assertEqual(self.resolve(), [sys.executable, str(self.entry)])

    def test_npm6_shim_resolves_the_same_way(self):
        self.assertEqual(self.resolve(self.NPM6_SHIM), [sys.executable, str(self.entry)])

    def test_node_exe_beside_the_shim_is_preferred_to_path(self):
        node = self.home / "node.exe"
        node.write_bytes(b"")
        with mock.patch.dict(os.environ):
            os.environ.pop("ADMINWRIGHT_NODE_BIN", None)
            self.shim.write_bytes(NPM_SHIM.encode("ascii"))
            self.assertEqual(bridge.resolve_cmd_shim(self.shim), [str(node), str(self.entry)])

    def test_a_shim_that_is_not_the_ocr_npm_shim_is_refused(self):
        with self.assertRaises(bridge.ReviewError) as caught:
            self.resolve('@"python" "fake.py" %*\r\n')
        message = str(caught.exception)
        self.assertIn("ADMINWRIGHT_OCR_BIN", message)
        self.assertIn("native executable", message)

    def test_an_entry_point_that_is_not_open_code_review_is_refused(self):
        other = NPM_SHIM.replace("open-code-review", "some-other-tool")
        with self.assertRaises(bridge.ReviewError):
            self.resolve(other)

    def test_a_percent_in_the_entry_path_is_never_expanded(self):
        tricky = NPM_SHIM.replace(
            "%dp0%\\node_modules\\@alibaba-group\\open-code-review",
            "%dp0%\\%TEMP%\\open-code-review",
        )
        with self.assertRaises(bridge.ReviewError):
            self.resolve(tricky)

    def test_a_missing_entry_point_is_refused(self):
        self.entry.unlink()
        with self.assertRaises(bridge.ReviewError) as caught:
            self.resolve()
        self.assertIn("does not exist", str(caught.exception))

    def test_missing_node_is_refused_not_guessed(self):
        empty = self.tmp / "empty-path"
        empty.mkdir()
        with self.assertRaises(bridge.ReviewError) as caught:
            self.resolve(env={"ADMINWRIGHT_NODE_BIN": "", "PATH": str(empty)})
        self.assertIn("node", str(caught.exception))
        self.assertIn("ADMINWRIGHT_OCR_BIN", str(caught.exception))

    def test_wrong_node_override_is_named(self):
        with self.assertRaises(bridge.ReviewError) as caught:
            self.resolve(env={"ADMINWRIGHT_NODE_BIN": str(self.tmp / "no-node")})
        self.assertIn("ADMINWRIGHT_NODE_BIN", str(caught.exception))

    def test_wrong_ocr_override_raises_naming_the_variable(self):
        with mock.patch.dict(os.environ, {"ADMINWRIGHT_OCR_BIN": str(self.tmp / "gone")}):
            with self.assertRaises(bridge.ReviewError) as caught:
                bridge.ocr_executable()
        self.assertIn("ADMINWRIGHT_OCR_BIN", str(caught.exception))

    def test_a_bare_command_name_override_is_looked_up_on_path(self):
        with mock.patch.dict(os.environ, {"ADMINWRIGHT_OCR_BIN": "my-ocr"}):
            with mock.patch.object(bridge.shutil, "which", return_value="/opt/bin/my-ocr"):
                self.assertEqual(bridge.ocr_executable(), "/opt/bin/my-ocr")

    @unittest.skipIf(os.name == "nt", "POSIX runs the executable directly")
    def test_posix_never_rewrites_the_command(self):
        self.assertEqual(bridge.command_prefix("/usr/bin/ocr"), ["/usr/bin/ocr"])
        # A .cmd file on POSIX is just a file; no cmd.exe exists to fear.
        self.assertEqual(bridge.command_prefix("/x/ocr.cmd"), ["/x/ocr.cmd"])


@WINDOWS_ONLY
class WindowsShim(TempConsole):
    """The cmd.exe injection (audit 3.4): a `.cmd` shim never sees our arguments."""

    def test_legacy_cmd_shim_is_refused_and_never_executed(self):
        # A shim that is not npm's: were cmd.exe ever asked to run it, it would
        # leave the canary. Refusal must come before any execution.
        canary = self.tmp / "canary.txt"
        for name in ("ocr.cmd", "ocr.bat"):
            legacy = self.tmp / name
            legacy.write_text('@echo executed> "{}"\r\n'.format(canary), encoding="ascii")
            code, _out, err = self.review(
                "diff", "--manifest", self.manifest,
                env={"ADMINWRIGHT_OCR_BIN": str(legacy)},
            )
            self.assertEqual(code, 2, name)
            self.assertIn("ADMINWRIGHT_OCR_BIN", err, name)
            self.assertIn("native executable", err, name)
            self.assertNotIn("Traceback", err, name)
            self.assertFalse(canary.exists(), name + " was executed by cmd.exe")

    def test_shim_with_missing_entry_point_is_refused(self):
        self.launcher.parent.joinpath(*NPM_SHIM_ENTRY).unlink()
        code, _out, err = self.review("diff", "--manifest", self.manifest)
        self.assertEqual(code, 2)
        self.assertIn("does not exist", err)
        self.assertNotIn("Traceback", err)

    def test_no_node_anywhere_is_refused(self):
        empty = self.tmp / "empty-path"
        empty.mkdir()
        code, _out, err = self.review(
            "diff", "--manifest", self.manifest,
            env={"ADMINWRIGHT_NODE_BIN": None, "PATH": str(empty)},
        )
        self.assertEqual(code, 2)
        self.assertIn("`node` was not found", err)

    def test_wrong_node_override_is_reported_as_such(self):
        code, _out, err = self.review(
            "diff", "--manifest", self.manifest,
            env={"ADMINWRIGHT_NODE_BIN": str(self.tmp / "no-node.exe")},
        )
        self.assertEqual(code, 2)
        self.assertIn("ADMINWRIGHT_NODE_BIN", err)

    def test_extensionless_npm_shim_falls_back_to_its_cmd_sibling(self):
        # npm drops an sh script named `ocr` beside ocr.cmd; Windows cannot
        # execute it, and ADMINWRIGHT_OCR_BIN pointing at it used to fail with
        # "%1 is not a valid Win32 application".
        bare = self.launcher.parent / "ocr"
        bare.write_text("#!/bin/sh\nexit 1\n", encoding="ascii")
        code, out, err = self.review(
            "diff", "--manifest", self.manifest, env={"ADMINWRIGHT_OCR_BIN": str(bare)}
        )
        self.assertEqual(code, 0, err)
        self.assertIn("OCR bundle written", out)

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_real_node_runs_the_resolved_entry_point(self):
        # No override: the shim is resolved to the `node` on PATH, and a real
        # node executes a JavaScript entry point. (This is node, not ocr.)
        entry = self.launcher.parent.joinpath(*NPM_SHIM_ENTRY)
        entry.write_text(
            "const fs = require('fs');\n"
            "const a = process.argv.slice(2);\n"
            "fs.appendFileSync(process.env.FAKE_ARGV_LOG, JSON.stringify({args: a,"
            " no_update: process.env.OCR_NO_UPDATE || null}) + '\\n');\n"
            "if (a[0] === 'delegate' && a[1] === 'preview') console.log(JSON.stringify("
            "{schema_version: '1', mode: 'workspace',"
            " reviewable_files: [{path: 'src/x&y.py', status: 'modified'}]}));\n"
            "else if (a[0] === 'delegate' && a[1] === 'rule') console.log(JSON.stringify("
            "{schema_version: '1', groups: []}));\n"
            "else { console.error('unexpected'); process.exit(1); }\n",
            encoding="utf-8",
        )
        code, out, err = self.review(
            "diff", "--manifest", self.manifest, env={"ADMINWRIGHT_NODE_BIN": None}
        )
        self.assertEqual(code, 0, err)
        self.assertIn("OCR bundle written", out)
        rule_call = [c for c in self.ocr_calls() if c["args"][:2] == ["delegate", "rule"]][0]
        self.assertEqual(rule_call["args"][-1], "src/x&y.py")
        self.assertEqual(rule_call["no_update"], "1")


class ArgumentSafety(TempConsole):
    """Repository file names are untrusted input and must reach ocr byte-for-byte."""

    NASTY_PATHS = (
        "src/x&pwned&y.py",  # the audit's own case: no whitespace, so never quoted
        "src/a%PATH%b^c!d(e).py",
        "src/spaced & quoted (1).py",
        "src/semi;colon,comma=eq.py",
    )

    def test_filenames_with_shell_metacharacters_round_trip_intact(self):
        for path in self.NASTY_PATHS:
            if self.argv_log.exists():
                self.argv_log.unlink()
            for stale in (self.root / ".admin-console").glob("ocr-bundle*.json"):
                stale.unlink()
            preview = json.dumps({
                "schema_version": "1", "mode": "workspace",
                "reviewable_files": [{"path": path, "status": "modified"}],
            })
            code, _out, err = self.review(
                "diff", "--manifest", self.manifest, env={"FAKE_PREVIEW": preview}
            )
            self.assertEqual(code, 0, path + ": " + err)
            rule_call = [c for c in self.ocr_calls() if c["args"][:2] == ["delegate", "rule"]]
            self.assertEqual(len(rule_call), 1, path)
            self.assertEqual(rule_call[0]["args"][-1], path)
            bundles = list((self.root / ".admin-console").glob("ocr-bundle*.json"))
            self.assertEqual(len(bundles), 1, path)
            bundle = json.loads(bundles[0].read_text(encoding="utf-8"))
            self.assertEqual([f["path"] for f in bundle["reviewable_files"]], [path])

    def test_ocr_no_update_is_forced_on_every_call(self):
        # Even when the caller's own environment says otherwise: the launcher
        # would otherwise contact npm (and may `npm i -g`) on each invocation.
        code, _out, err = self.review(
            "diff", "--manifest", self.manifest, env={"OCR_NO_UPDATE": "0"}
        )
        self.assertEqual(code, 0, err)
        calls = self.ocr_calls()
        self.assertGreaterEqual(len(calls), 2)
        self.assertEqual({call["no_update"] for call in calls}, {"1"})

    def test_endpoint_engine_also_sets_it(self):
        code, _out, err = self.review("diff", "--manifest", self.manifest, "--engine", "ocr")
        self.assertEqual(code, 1, err)
        self.assertEqual({call["no_update"] for call in self.ocr_calls()}, {"1"})


class LargeBatch(TempConsole):
    """Audit 3.3: ~60 findings overflowed Windows' 32k command line and crashed.

    The batch now travels as `--json @file`. These tests use a payload far
    beyond the limit; on POSIX the limit is higher but the path is the same.
    """

    def payload(self, count=150, size=300):
        findings = [
            {
                "path": "src/orders.py",
                "content": ("finding %03d " % index).ljust(size, "x"),
                "start_line": index + 1,
                "end_line": index + 1,
                "category": "bug",
                "severity": "low",
            }
            for index in range(count)
        ]
        return {"findings": findings, "skipped": []}

    def leftovers(self):
        return sorted(p.name for p in (self.root / ".admin-console").glob("*.tmp"))

    def test_150_findings_of_300_chars_all_persist(self):
        payload = self.payload()
        self.assertGreater(len(json.dumps(payload["findings"])), 32767)
        path = self.tmp / "findings.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        code, out, err = self.review(
            "record", "--manifest", self.manifest, "--findings", "@" + str(path)
        )
        self.assertEqual(code, 1, err)
        self.assertNotIn("Traceback", err)
        self.assertIn("recorded 150 finding(s)", out)
        gaps = self.gaps()
        self.assertEqual(len(gaps), 150)
        self.assertEqual(len({gap["id"] for gap in gaps}), 150)
        self.assertEqual(self.leftovers(), [], "the temp batch file must be removed")

    def test_stdin_payload_of_the_same_size(self):
        code, _out, err = self.review(
            "record", "--manifest", self.manifest, "--findings", "-",
            input=json.dumps(self.payload(count=80)),
        )
        self.assertEqual(code, 1, err)
        self.assertEqual(len(self.gaps()), 80)
        self.assertEqual(self.leftovers(), [])

    def test_a_refused_batch_leaves_no_temp_file_and_no_gaps(self):
        payload = self.payload(count=5)
        payload["findings"][3]["severity"] = "catastrophic"  # refused before any add
        code, _out, err = self.review(
            "record", "--manifest", self.manifest, "--findings", json.dumps(payload)
        )
        self.assertEqual(code, 2)
        self.assertEqual(self.gaps(), [])
        self.assertEqual(self.leftovers(), [])


class BatchIoErrors(unittest.TestCase):
    """An OS error around the manifest call is a ReviewError (exit 2), not a traceback."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="adminwright-batch-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.manifest = self.tmp / ".admin-console" / "manifest.json"
        self.manifest.parent.mkdir()
        self.manifest.write_text("{}", encoding="utf-8")
        self.finding = bridge.normalize_finding(
            {"path": "a.py", "content": "c", "start_line": 1, "severity": "low", "category": "bug"}
        )

    def persist(self):
        return bridge.persist_findings(
            self.manifest, [self.finding], "delegate", "delegate review",
            self.manifest.parent / "evidence.json",
        )

    def test_oserror_starting_the_manifest_script(self):
        with mock.patch.object(bridge.subprocess, "run", side_effect=OSError("boom")):
            with self.assertRaises(bridge.ReviewError) as caught:
                self.persist()
        self.assertIn("boom", str(caught.exception))
        self.assertEqual(list(self.manifest.parent.glob("*.tmp")), [])

    def test_a_manifest_refusal_still_removes_the_temp_file(self):
        refused = subprocess.CompletedProcess([], 2, stdout="", stderr="gap 'x' is invalid")
        with mock.patch.object(bridge.subprocess, "run", return_value=refused):
            with self.assertRaises(bridge.ReviewError) as caught:
                self.persist()
        self.assertIn("nothing was persisted", str(caught.exception))
        self.assertEqual(list(self.manifest.parent.glob("*.tmp")), [])

    def test_oserror_creating_the_temp_file(self):
        with mock.patch.object(bridge.tempfile, "mkstemp", side_effect=OSError("disk full")):
            with self.assertRaises(bridge.ReviewError) as caught:
                self.persist()
        self.assertIn("disk full", str(caught.exception))


@unittest.skipUnless(
    os.environ.get("ADMINWRIGHT_REAL_OCR") and shutil.which("ocr"),
    "opt-in: set ADMINWRIGHT_REAL_OCR=1 with the real ocr CLI installed",
)
class RealCliSmoke(unittest.TestCase):
    """Check the command surface against a real ocr install when one exists.

    Every other test fakes the binary through ADMINWRIGHT_OCR_BIN and accepts
    any flags; this is the only guard against a renamed flag or subcommand
    reaching users. Opt-in (ADMINWRIGHT_REAL_OCR=1) so an ordinary test run
    never executes a globally installed CLI that phones home; skipped where
    ocr is absent, because the bridge itself is optional. It only runs
    `ocr <subcommand> --help`, with the update check switched off.
    """

    def help_text(self, *args):
        executable = shutil.which("ocr")
        env = dict(os.environ)
        env["OCR_NO_UPDATE"] = "1"
        proc = subprocess.run(
            [executable, *args, "--help"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
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
