# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TarikAI
"""Regression tests for code_review.py, the optional OCR bridge.

Standard library only (unittest), so `python -m unittest discover tests` works
anywhere the skill itself works. The `ocr` CLI is faked through the
ADMINWRIGHT_OCR_BIN hook: no network, no LLM, no provider configuration. What
the fake prints for a review or scan is either a shape written here or, in
RealReplies, a reply recorded from the real CLI (tests/fixtures/ocr, made by
capture.py there) and replayed byte for byte. The real `ocr` package is never
run unless RealCliSmoke is opted into. On Windows the fake is
laid out like an npm install (`ocr.cmd` shim plus a `node_modules/.../ocr.js`
entry point) and ADMINWRIGHT_NODE_BIN points the resolved launcher at this
Python, so the tests need neither node nor cmd.exe's cooperation.

ADMINWRIGHT_HOME is pointed at a scratch directory so nothing touches the
user's home.

Run:  python -m unittest discover -s tests -v
"""

import atexit
import codecs
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
# Replies recorded from the real ocr CLI by tests/fixtures/ocr/capture.py.
OCR_FIXTURES = SKILL_ROOT / "tests" / "fixtures" / "ocr"


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
    "if os.environ.get('FAKE_GRANDCHILD'):\n"
    "    import subprocess\n"
    "    code = 'import os, sys, time\\nopen(sys.argv[1], \"w\").write(str(os.getpid()))\\n"
    "time.sleep(60)\\n'\n"
    "    subprocess.Popen([sys.executable, '-c', code, os.environ['FAKE_GRANDCHILD']])\n"
    "if os.environ.get('FAKE_SLEEP'):\n"
    "    time.sleep(float(os.environ['FAKE_SLEEP']))\n"
    "if os.environ.get('FAKE_FAIL'):\n"
    "    sys.stderr.write('provider not configured\\n')\n"
    "    sys.exit(1)\n"
    "if os.environ.get('FAKE_REPLAY') and args and args[0] in ('review', 'scan'):\n"
    "    with open(os.environ['FAKE_REPLAY'], encoding='utf-8') as handle:\n"
    "        recorded = json.load(handle)\n"
    "    sys.stdout.buffer.write(recorded['stdout'].encode('utf-8'))\n"
    "    sys.stderr.buffer.write(recorded['stderr'].encode('utf-8'))\n"
    "    sys.stdout.flush()\n"
    "    sys.stderr.flush()\n"
    "    sys.exit(recorded['returncode'])\n"
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

    def bundles(self):
        return sorted((self.root / ".admin-console").glob("ocr-bundle*.json"))

    def only_bundle(self):
        found = self.bundles()
        self.assertEqual(len(found), 1, found)
        return found[0]

    def rel(self, path):
        """A path as the manifest cites it: relative to the project root, forward slashes."""
        return str(Path(path).relative_to(self.root)).replace("\\", "/")

    def cited(self, gap):
        """The one evidence file a bridge gap cites, as (relative path, parsed JSON)."""
        self.assertEqual(len(gap["evidence"]), 1, gap)
        cited = gap["evidence"][0]
        return cited, json.loads((self.root / cited).read_text(encoding="utf-8"))

    def diff(self, *extra, **kwargs):
        code, out, err = self.review("diff", "--manifest", self.manifest, *extra, **kwargs)
        self.assertEqual(code, 0, err)
        return out

    def record(self, payload, *extra, **kwargs):
        return self.review(
            "record", "--manifest", self.manifest, "--findings", json.dumps(payload),
            *extra, **kwargs
        )


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

    def test_a_file_that_cannot_be_executed_names_the_real_native_binary(self):
        # Windows: "%1 is not a valid Win32 application"; POSIX: permission denied.
        # The advice used to send people looking for an ocr.exe npm never installs.
        text_file = self.tmp / "not-a-program.txt"
        text_file.write_text("hello\n", encoding="utf-8")
        code, _out, err = run(
            SCRIPT, "diff", "--manifest", self.manifest,
            env={"ADMINWRIGHT_OCR_BIN": str(text_file)},
        )
        self.assertEqual(code, 2, err)
        self.assertIn("could not execute", err)
        self.assertIn("opencodereview.exe", err)
        self.assertNotRegex(err, r"(?<![\w-])ocr\.exe")
        self.assertNotIn("Traceback", err)


class DelegateDiff(TempConsole):
    def test_writes_bundle_with_instructions_and_next_command(self):
        code, out, err = self.review("diff", "--manifest", self.manifest)
        self.assertEqual(code, 0, err)
        bundle_path = self.only_bundle()
        self.assertRegex(bundle_path.name, r"^ocr-bundle-\d{8}-\d{6}-\d+\.json$")
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
        self.assertEqual(bundle["engine"], "delegate")
        self.assertIn({"path": "src/orders.py", "status": "modified"}, bundle["reviewable_files"])
        self.assertIn("instructions", bundle)
        self.assertIn("severity", bundle["instructions"])
        self.assertIn("record", out)
        # The exact bundle is handed to `record`; nothing is left to guess.
        self.assertIn("--bundle " + str(bundle_path), out)

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
        self.bundle_path = self.only_bundle()

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
        # The evidence is the committed record of this review, which names the
        # (transient, git-ignored) bundle it was checked against.
        cited, record = self.cited(gaps[0])
        self.assertRegex(cited, r"^\.admin-console/ocr-record-\d{8}-\d{6}-\d+\.json$")
        self.assertEqual(record["bundle"]["file"], self.bundle_path.name)
        self.assertEqual(record["findings"], self.findings_payload()["findings"])

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
        self.bundle_path.unlink()  # setUp's bundle would make two live bundles
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
        # Six different findings on one file all want the same base id
        # (same day, same slug, index 1); each must still get a free one.
        for run_index in range(6):
            payload = json.dumps(self.findings_payload(content="distinct finding %d" % run_index))
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
        # A different finding at the same base id is suffixed, never refused.
        first = json.dumps(self.findings_payload(content="first finding"))
        second = json.dumps(self.findings_payload(content="second finding"))
        code, _out, _err = self.review("record", "--manifest", self.manifest, "--findings", first)
        self.assertEqual(code, 1)
        code, _out, _err = self.review("record", "--manifest", self.manifest, "--findings", second)
        self.assertEqual(code, 1)
        ids = [gap["id"] for gap in self.gaps()]
        self.assertEqual(len(ids), 2)
        self.assertNotEqual(ids[0], ids[1])
        self.assertTrue(ids[1].endswith("-r2"), ids)


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


def comment(path="src/orders.py", **extra):
    """One OCR-schema finding."""
    item = {
        "path": path,
        "content": "SQL built by string concatenation",
        "start_line": 10,
        "end_line": 10,
        "category": "security",
        "severity": "high",
    }
    item.update(extra)
    return item


class EndpointVerdict(TempConsole):
    """Audit 6.14: a review that failed must never read as "0 finding(s) recorded"."""

    def endpoint(self, document, rc=0, *extra, **kwargs):
        env = {"FAKE_FINDINGS": json.dumps(document), "FAKE_RC": str(rc)}
        return self.review(
            "diff", "--manifest", self.manifest, "--engine", "ocr", *extra, env=env, **kwargs
        )

    def assert_failed_run(self, code, out, err, *needles):
        self.assertEqual(code, 2, out + err)
        self.assertNotIn("finding(s) recorded", out)
        self.assertNotIn("Traceback", err)
        for needle in needles:
            self.assertIn(needle, err)
        self.assertEqual(self.gaps(), [])
        self.assertEqual(list((self.root / ".admin-console").glob("ocr-diff-*.json")), [])

    def test_nonzero_exit_with_a_failed_status_body_exits_two(self):
        code, out, err = self.endpoint(
            {"status": "failed", "error": "provider rejected the token"}, rc=1
        )
        self.assert_failed_run(code, out, err, "provider rejected the token", "exit code 1", "status 'failed'")

    def test_nonzero_exit_alone_is_enough_even_with_findings_in_the_body(self):
        code, out, err = self.endpoint({"comments": [comment()]}, rc=3)
        self.assert_failed_run(code, out, err, "exit code 3")

    def test_failed_status_with_exit_zero(self):
        code, out, err = self.endpoint({"status": "failed", "message": "quota exceeded", "comments": [comment()]})
        self.assert_failed_run(code, out, err, "quota exceeded", "status 'failed'")

    def test_a_failed_terminal_state_records_nothing_even_with_exit_zero(self):
        for document in (
            {"terminal_state": "failed", "comments": [comment()]},
            {"manifest": {"terminal_state": "failed"}, "comments": [comment()]},
            {"status": "complete", "run_manifest": {"terminal_state": "FAILED"}, "comments": [comment()]},
        ):
            code, out, err = self.endpoint(document)
            self.assert_failed_run(code, out, err, "terminal_state")

    def test_an_unknown_state_keeps_the_findings_but_is_not_called_complete(self):
        # Replaces "terminal_state other than completed fails": that rule turned
        # ocr's real `complete` into a failure (R2). An unknown word with exit 0
        # is usable coverage by ocr's own exit contract, so the findings are
        # kept; exit 2 says completeness was not confirmed.
        for index, document in enumerate((
            {"manifest": {"terminal_state": "aborted"}, "comments": [comment(start_line=1)]},
            {"run_manifest": {"terminal_state": "in_progress"}, "comments": [comment(start_line=2)]},
            {"status": "finished_somehow", "comments": [comment(start_line=3)]},
        ), 1):
            code, out, err = self.endpoint(document)
            self.assertEqual(code, 2, out + err)
            self.assertIn("1 finding(s) recorded", out)
            self.assertIn("does not know", err)
            self.assertNotIn("Traceback", err)
            self.assertEqual(len(self.gaps()), index)

    def test_a_completed_run_with_nothing_failed_is_recorded_normally(self):
        code, out, err = self.endpoint({
            "status": "success",
            "manifest": {"terminal_state": "completed"},
            "coverage": {"failed": [], "failed_files": 0},
            "comments": [comment()],
        })
        self.assertEqual(code, 1, err)
        self.assertIn("1 finding(s) recorded", out)
        self.assertEqual(len(self.gaps()), 1)

    def test_a_clean_completed_run_exits_zero(self):
        code, out, err = self.endpoint({"manifest": {"terminal_state": "completed"}, "comments": []})
        self.assertEqual(code, 0, err)
        self.assertIn("0 finding(s) recorded", out)

    def test_failed_files_are_listed_and_exit_two_but_real_findings_are_kept(self):
        for index, (label, document) in enumerate((
            ("coverage.failed", {"coverage": {"failed": ["src/a.py", {"path": "src/b.py", "reason": "timeout"}]}}),
            ("manifest.coverage.failed_files", {"manifest": {"coverage": {"failed_files": ["src/a.py", "src/b.py"]}}}),
        )):
            document["comments"] = [comment(path="src/ok%d.py" % index)]
            code, out, err = self.endpoint(document)
            self.assertEqual(code, 2, label + ": " + err)
            self.assertIn("src/a.py", err, label)
            self.assertIn("src/b.py", err, label)
            self.assertIn("could not review 2 file(s)", err, label)
            self.assertIn("1 finding(s) recorded", out, label)
            self.assertNotIn("Traceback", err, label)
        # The reviewed files' findings were kept both times.
        self.assertEqual(len(self.gaps()), 2)

    def test_a_failed_file_count_without_names_still_exits_two(self):
        code, _out, err = self.endpoint({"coverage": {"failed": 3}, "comments": []})
        self.assertEqual(code, 2)
        self.assertIn("could not review 3 file(s)", err)

    def test_scan_gets_the_same_verdicts(self):
        env = {"FAKE_FINDINGS": json.dumps({"status": "failed", "error": "boom"}), "FAKE_RC": "1"}
        code, out, err = self.review("scan", "--manifest", self.manifest, env=env)
        self.assert_failed_run(code, out, err, "boom", "ocr scan did not complete")

    def test_silent_failure_still_explains_itself(self):
        code, _out, err = self.review(
            "diff", "--manifest", self.manifest, "--engine", "ocr", env={"FAKE_FINDINGS": "", "FAKE_RC": "4"}
        )
        self.assertEqual(code, 2)
        self.assertIn("exit code 4", err)


def recorded_reply(name):
    return json.loads((OCR_FIXTURES / (name + ".json")).read_text(encoding="utf-8"))


class RealReplies(TempConsole):
    """Audit R2 (6.14): the bridge against what ocr really prints.

    Every reply here was recorded from the real CLI (open-code-review v1.12.9,
    tests/fixtures/ocr/capture.py, against the stand-in endpoint in
    fake_llm.py) and is replayed byte for byte, exit code included. The tests
    above use shapes written from memory; one of them, "terminal_state":
    "completed", is a word ocr never prints, and a bridge that failed every real
    review passed them all.
    """

    def replay(self, name, path=None):
        command = ["scan"] if name.startswith("scan-") else ["diff", "--engine", "ocr"]
        return self.review(
            command[0], "--manifest", self.manifest, *command[1:],
            env={"FAKE_REPLAY": str(path or OCR_FIXTURES / (name + ".json"))},
        )

    def raw_files(self):
        folder = self.root / ".admin-console"
        return sorted(list(folder.glob("ocr-diff-*.json")) + list(folder.glob("ocr-scan-*.json")))

    def lines_of(self, gaps):
        return sorted((gap["severity"], gap["description"].split(" — ")[0]) for gap in gaps)

    def test_the_fixtures_are_what_ocr_printed(self):
        for name, returncode, state in (
            ("review-complete", 0, "complete"), ("review-partial", 0, "partial"),
            ("review-failed", 1, "failed"), ("review-skipped", 0, "skipped"),
        ):
            recorded = recorded_reply(name)
            self.assertIn("open-code-review v", recorded["captured_with"], name)
            self.assertEqual(recorded["returncode"], returncode, name)
            body = json.loads(recorded["stdout"])
            self.assertEqual((body["status"], body["manifest"]["terminal_state"]), (state, state), name)
        for name, returncode, status in (("scan-success", 0, "success"), ("scan-partial", 0, "completed_with_errors")):
            recorded = recorded_reply(name)
            self.assertEqual(recorded["returncode"], returncode, name)
            self.assertEqual(json.loads(recorded["stdout"])["status"], status, name)
        for name in ("review-no-provider", "review-bad-ref", "scan-failed"):
            recorded = recorded_reply(name)
            self.assertEqual((recorded["returncode"], recorded["stdout"]), (1, ""), name)
            self.assertIn("Error: ", recorded["stderr"], name)

    def test_a_complete_review_records_its_findings(self):
        code, out, err = self.replay("review-complete")
        self.assertEqual(code, 1, out + err)
        self.assertIn("2 finding(s) recorded as gaps", out)
        self.assertNotIn("ERROR", err)
        self.assertNotIn("hint:", err)
        self.assertEqual(self.lines_of(self.gaps()), [("high", "src/a.py:2"), ("medium", "src/c.py:2")])
        self.assertEqual(len(self.raw_files()), 1)

    def test_a_complete_review_that_found_nothing_exits_zero(self):
        recorded = recorded_reply("review-complete")
        body = json.loads(recorded["stdout"])
        body["comments"] = []
        recorded["stdout"] = json.dumps(body, indent=2)
        path = self.tmp / "clean.json"
        path.write_text(json.dumps(recorded), encoding="utf-8")
        code, out, err = self.replay("review-complete", path)
        self.assertEqual(code, 0, out + err)
        self.assertIn("0 finding(s) recorded", out)
        self.assertEqual(self.gaps(), [])

    def test_a_partial_review_records_what_it_found_and_names_the_failed_file(self):
        code, out, err = self.replay("review-partial")
        self.assertEqual(code, 2, out + err)
        self.assertIn("2 finding(s) recorded as gaps", out)
        self.assertIn("could not review 1 file(s) of 4 selected", err)
        self.assertIn("src/b.py (provider: provider or subtask request failed)", err)
        # The provider answered for three files: "configure a provider" would mislead.
        self.assertNotIn("ocr config provider", err)
        self.assertEqual(self.lines_of(self.gaps()), [("high", "src/a.py:2"), ("medium", "src/c.py:2")])
        self.assertEqual(len(self.raw_files()), 1)

    def test_a_failed_review_records_nothing_and_says_why(self):
        code, out, err = self.replay("review-failed")
        self.assertEqual(code, 2, out + err)
        for needle in ("exit code 1", "status 'failed'", "terminal_state 'failed'",
                       "check your LLM configuration and API key"):
            self.assertIn(needle, err)
        for path in ("src/a.py", "src/b.py", "src/c.py", "src/d.py"):
            self.assertIn(path + " (provider:", err)
        self.assertIn("ocr config provider", err)  # every file failed at the provider
        self.assertNotIn('"session_id"', err, "ocr's stderr usage record is noise here")
        self.assertNotIn("finding(s) recorded", out)
        self.assertEqual(self.gaps(), [])
        self.assertEqual(self.raw_files(), [])

    def test_a_skipped_review_had_nothing_to_review(self):
        code, out, err = self.replay("review-skipped")
        self.assertEqual(code, 0, out + err)
        self.assertIn("nothing to review", out)
        self.assertIn("Review skipped: no items were selected.", out)
        self.assertNotIn("ERROR", err)
        self.assertEqual(self.gaps(), [])
        self.assertEqual(self.raw_files(), [])

    def test_no_provider_configured_gets_the_set_up_hint(self):
        code, out, err = self.replay("review-no-provider")
        self.assertEqual(code, 2, out + err)
        self.assertIn("no valid LLM endpoint configured", err)
        self.assertIn("ocr config provider", err)
        self.assertEqual(self.gaps(), [])

    def test_a_bad_ref_is_not_blamed_on_the_provider(self):
        code, out, err = self.replay("review-bad-ref")
        self.assertEqual(code, 2, out + err)
        self.assertIn('--from value "no-such-ref" is not a valid commit ref', err)
        self.assertNotIn("hint:", err)
        self.assertNotIn("ocr config provider", err)

    def test_a_successful_scan_records_its_findings(self):
        code, out, err = self.replay("scan-success")
        self.assertEqual(code, 1, out + err)
        self.assertIn("OCR scan: 2 finding(s) recorded", out)
        self.assertEqual(self.lines_of(self.gaps()), [("high", "src/a.py:2"), ("medium", "src/c.py:2")])
        for gap in self.gaps():
            self.assertIn("scan mode", gap["rationale"])

    def test_a_scan_completed_with_errors_is_partial(self):
        code, out, err = self.replay("scan-partial")
        self.assertEqual(code, 2, out + err)
        self.assertIn("OCR scan: 2 finding(s) recorded", out)
        self.assertIn("could not review 1 file(s)", err)
        self.assertIn("src/b.py (scan_subtask_error: LLM completion error", err)
        self.assertEqual(len(self.gaps()), 2)

    def test_a_failed_scan_records_nothing(self):
        code, out, err = self.replay("scan-failed")
        self.assertEqual(code, 2, out + err)
        self.assertIn("scan failed: all 4 file scan(s) failed", err)
        self.assertIn("ocr config provider", err)
        self.assertEqual(self.gaps(), [])
        self.assertEqual(self.raw_files(), [])

    def test_a_rerun_of_a_partial_review_does_not_duplicate(self):
        self.assertEqual(self.replay("review-partial")[0], 2)
        code, out, err = self.replay("review-complete")
        self.assertEqual(code, 1, out + err)
        self.assertIn("(2 already recorded)", out)
        self.assertEqual(len(self.gaps()), 2)


class BundleLifecycle(TempConsole):
    """Audit 6.15: per-run bundles, consumed on record, expired when abandoned."""

    def payload(self, **extra):
        return {"findings": [comment(**extra)], "skipped": []}

    def test_concurrent_diffs_do_not_clobber_each_other(self):
        self.diff("--agent", "qa-1")
        self.diff("--agent", "security-1")
        found = self.bundles()
        self.assertEqual(len(found), 2)
        self.assertEqual(
            sorted(json.loads(p.read_text(encoding="utf-8"))["agent"] for p in found),
            ["qa-1", "security-1"],
        )
        self.assertTrue(any("qa-1" in p.name for p in found))
        self.assertTrue(any("security-1" in p.name for p in found))

    def test_record_refuses_to_guess_between_live_bundles(self):
        self.diff("--agent", "qa-1")
        self.diff("--agent", "security-1")
        code, _out, err = self.record(self.payload())
        self.assertEqual(code, 2)
        self.assertIn("--bundle", err)
        self.assertIn("qa-1", err)
        self.assertIn("security-1", err)
        self.assertEqual(self.gaps(), [])

    def test_explicit_bundle_records_and_leaves_the_other_agents_bundle_live(self):
        self.diff("--agent", "qa-1")
        self.diff("--agent", "security-1")
        qa = [p for p in self.bundles() if "qa-1" in p.name][0]
        code, out, err = self.record(self.payload(), "--bundle", str(qa))
        self.assertEqual(code, 1, err)
        self.assertEqual(self.cited(self.gaps()[0])[1]["bundle"]["file"], qa.name)
        # security-1's bundle is now the only live one, so it is picked up.
        code, out, err = self.record({"findings": [], "skipped": [{"path": "src/orders.py", "reason": "vendored"}]})
        self.assertEqual(code, 0, err)
        self.assertIn("coverage_rate=100%", out)
        self.assertIn("security-1", out)

    def test_diff_prints_the_bundle_it_wrote_in_the_record_command(self):
        out = self.diff("--agent", "qa-1")
        bundle = self.only_bundle()
        self.assertIn("--bundle " + str(bundle), out)
        self.assertIn("qa-1", bundle.name)

    def test_bundle_records_agent_and_refs(self):
        self.diff("--agent", "qa-1", "--from", "main", "--to", "feature")
        bundle = json.loads(self.only_bundle().read_text(encoding="utf-8"))
        self.assertEqual(bundle["agent"], "qa-1")
        self.assertEqual(bundle["refs"], {"from": "main", "to": "feature"})

    def test_an_agent_id_cannot_escape_the_directory(self):
        self.diff("--agent", "../../evil name")
        bundle = self.only_bundle()
        self.assertEqual(bundle.parent, self.root / ".admin-console")
        self.assertNotIn("/", bundle.name)
        self.assertNotIn("\\", bundle.name)

    def test_record_closes_its_bundle_and_keeps_the_skip_reasons(self):
        self.diff()
        bundle_path = self.only_bundle()
        payload = {"findings": [], "skipped": [{"path": "src/orders.py", "reason": "generated code"}]}
        code, _out, err = self.record(payload)
        self.assertEqual(code, 0, err)
        closed = json.loads(bundle_path.read_text(encoding="utf-8"))["closed"]
        self.assertEqual(closed["by"], "record")
        self.assertEqual(closed["skipped"], [{"path": "src/orders.py", "reason": "generated code"}])
        # The skip reasons also reach the committed record the bundle points at.
        record = json.loads((bundle_path.parent / closed["record"]).read_text(encoding="utf-8"))
        self.assertEqual(record["skipped"], closed["skipped"])
        self.assertEqual(record["bundle"]["reviewable_files"],
                         [{"path": "src/orders.py", "status": "modified"}])
        # Spent: the next record is no longer gated by, or checked against, it.
        code, out, err = self.record(self.payload(path="anywhere/else.py"))
        self.assertEqual(code, 1, err)
        self.assertIn("coverage: unknown", out)

    def test_closed_bundle_cannot_be_recorded_against_again(self):
        self.diff()
        bundle_path = self.only_bundle()
        self.assertEqual(self.record(self.payload())[0], 1)
        code, _out, err = self.record(self.payload(content="another"), "--bundle", str(bundle_path))
        self.assertEqual(code, 2)
        self.assertIn("already closed", err)
        self.assertEqual(len(self.gaps()), 1)

    def test_a_refused_record_leaves_the_bundle_usable(self):
        self.diff()
        code, _out, _err = self.record({"findings": [], "skipped": []})  # silent omission
        self.assertEqual(code, 2)
        self.assertNotIn("closed", json.loads(self.only_bundle().read_text(encoding="utf-8")))
        self.assertEqual(self.record(self.payload())[0], 1)

    def test_an_empty_diff_supersedes_that_agents_stale_bundle(self):
        self.diff("--agent", "qa-1")
        bundle_path = self.only_bundle()
        empty = {"FAKE_PREVIEW": json.dumps({"schema_version": "1", "mode": "workspace", "reviewable_files": []})}
        out = self.diff("--agent", "qa-1", env=empty)
        self.assertIn("nothing to review", out)
        closed = json.loads(bundle_path.read_text(encoding="utf-8"))["closed"]
        self.assertEqual(closed["by"], "diff")
        # The stale bundle no longer gates a record about something else.
        code, out, err = self.record(self.payload(path="docs/readme.md"))
        self.assertEqual(code, 1, err)
        self.assertIn("coverage: unknown", out)

    def test_an_empty_diff_never_touches_another_agents_bundle(self):
        self.diff("--agent", "security-1")
        bundle_path = self.only_bundle()
        empty = {"FAKE_PREVIEW": json.dumps({"schema_version": "1", "mode": "workspace", "reviewable_files": []})}
        self.diff("--agent", "qa-1", env=empty)
        self.diff(env=empty)
        self.assertNotIn("closed", json.loads(bundle_path.read_text(encoding="utf-8")))

    def test_the_same_agent_rerunning_diff_replaces_its_own_bundle(self):
        self.diff("--agent", "qa-1")
        first = self.only_bundle()
        self.diff("--agent", "qa-1")
        self.assertEqual(len(self.bundles()), 2)
        self.assertEqual(json.loads(first.read_text(encoding="utf-8"))["closed"]["by"], "diff")
        code, out, err = self.record(self.payload())  # no ambiguity: only one is live
        self.assertEqual(code, 1, err)

    def test_an_abandoned_bundle_expires(self):
        self.diff()
        bundle_path = self.only_bundle()
        two_days_ago = time.time() - 2 * 24 * 3600
        os.utime(str(bundle_path), (two_days_ago, two_days_ago))
        code, out, err = self.record(self.payload(path="docs/readme.md"))
        self.assertEqual(code, 1, err)
        self.assertIn("expired", err)
        self.assertIn("coverage: unknown", out)

    def test_a_bundle_inside_the_ttl_still_gates(self):
        self.diff()
        bundle_path = self.only_bundle()
        an_hour_ago = time.time() - 3600
        os.utime(str(bundle_path), (an_hour_ago, an_hour_ago))
        code, _out, err = self.record(self.payload(path="docs/readme.md"))
        self.assertEqual(code, 2)
        self.assertIn("not in the review bundle", err)

    def test_the_old_shared_bundle_name_is_still_honoured(self):
        legacy = self.root / ".admin-console" / "ocr-bundle.json"
        legacy.write_text(json.dumps({
            "schema_version": "1", "engine": "delegate", "mode": "workspace",
            "reviewable_files": [{"path": "src/orders.py", "status": "modified"}],
        }), encoding="utf-8")
        code, _out, err = self.record(self.payload(path="docs/readme.md"))
        self.assertEqual(code, 2)
        self.assertIn("not in the review bundle", err)

    def test_without_a_bundle_coverage_is_unknown_not_a_hundred_percent(self):
        code, out, err = self.record(self.payload(path="any/invented/path.py"))
        self.assertEqual(code, 1, err)
        self.assertIn("coverage: unknown", out)
        self.assertNotIn("coverage_rate=100%", out)
        self.assertNotIn("total_files", out)
        self.assertIn("no review bundle", err)

    def test_gaps_cite_committed_evidence_never_a_bundle(self):
        # Bundles are transient and git-ignored; a gap citing one pointed every
        # clone and CI checkout at a file it does not have (audit ocr#5).
        self.diff("--agent", "qa-1")
        self.assertEqual(self.record(self.payload())[0], 1)
        self.assertEqual(self.record(self.payload(content="without a bundle"))[0], 1)
        endpoint = {"FAKE_FINDINGS": json.dumps({"comments": [comment(content="from the endpoint")]})}
        code, _out, err = self.review("diff", "--manifest", self.manifest, "--engine", "ocr", env=endpoint)
        self.assertEqual(code, 1, err)
        gaps = self.gaps()
        self.assertEqual(len(gaps), 3)
        for gap in gaps:
            cited, _document = self.cited(gap)
            self.assertNotIn("ocr-bundle", cited, gap["id"])
            self.assertRegex(cited, r"^\.admin-console/ocr-(record|diff|scan)-[0-9-]+\.json$")

    def test_a_refused_record_leaves_no_record_file_behind(self):
        self.diff()
        payload = self.payload()
        payload["findings"].append(dict(comment(), severity="catastrophic"))
        code, _out, _err = self.record(payload)
        self.assertEqual(code, 2)
        self.assertEqual(list((self.root / ".admin-console").glob("ocr-record-*.json")), [])
        # And the add itself refusing (not just normalisation) cleans up too.
        with mock.patch.object(bridge, "add_gaps", return_value=subprocess.CompletedProcess(
                [], 2, stdout="", stderr="gap refused")):
            args = bridge.build_parser().parse_args(
                ["record", "--manifest", str(self.manifest), "--findings", json.dumps(self.payload())])
            with mock.patch.object(bridge, "stderr"):
                with self.assertRaises(bridge.ReviewError):
                    bridge.cmd_record(args)
        self.assertEqual(list((self.root / ".admin-console").glob("ocr-record-*.json")), [])
        self.assertNotIn("closed", json.loads(self.only_bundle().read_text(encoding="utf-8")))

    def test_every_json_file_the_bridge_writes_has_lf_line_endings(self):
        # Text mode wrote CRLF on Windows (1205 CR bytes in one ocr-record file).
        self.diff("--agent", "qa-1")
        self.assertEqual(self.record(self.payload())[0], 1)  # closes (rewrites) the bundle
        self.assertEqual(self.record(self.payload(content="no bundle now"))[0], 1)
        code, _out, err = self.review("diff", "--manifest", self.manifest, "--engine", "ocr")
        self.assertIn(code, (0, 1), err)
        folder = self.root / ".admin-console"
        written = sorted(folder.glob("ocr-*.json"))
        kinds = {path.name.split("-")[1] for path in written}
        self.assertTrue({"bundle", "record", "diff"} <= kinds, kinds)
        for path in written:
            data = path.read_bytes()
            self.assertNotIn(b"\r", data, path.name)
            self.assertTrue(data.endswith(b"\n"), path.name)

    def test_the_no_bundle_evidence_file_is_unique_per_run(self):
        self.record(self.payload(content="one"))
        self.record(self.payload(content="two"))
        records = list((self.root / ".admin-console").glob("ocr-record-*.json"))
        self.assertEqual(len(records), 2)
        cited = {gap["evidence"][0] for gap in self.gaps()}
        self.assertEqual(len(cited), 2)


class Deduplication(TempConsole):
    """Audit 6.15: a successful re-run must not pile up copies of a finding."""

    def payload(self, **extra):
        return {"findings": [comment(**extra)], "skipped": []}

    def test_an_identical_rerun_records_nothing_new_but_still_reports_findings(self):
        self.assertEqual(self.record(self.payload())[0], 1)
        code, out, err = self.record(self.payload())
        self.assertEqual(code, 1, err)  # findings exist: not "clean"
        self.assertIn("recorded 0 finding(s) as gaps (1 already recorded)", out)
        self.assertEqual(len(self.gaps()), 1)

    def test_a_changed_line_or_content_is_a_new_finding(self):
        self.assertEqual(self.record(self.payload())[0], 1)
        self.assertEqual(self.record(self.payload(start_line=11))[0], 1)
        self.assertEqual(self.record(self.payload(content="a different problem"))[0], 1)
        self.assertEqual(len(self.gaps()), 3)

    def test_severity_or_category_drift_does_not_defeat_the_match(self):
        self.assertEqual(self.record(self.payload())[0], 1)
        self.record(self.payload(severity="low", category="bug"))
        self.assertEqual(len(self.gaps()), 1)

    def test_duplicates_inside_one_payload_are_written_once(self):
        payload = {"findings": [comment(), comment(), comment(start_line=99)], "skipped": []}
        code, out, err = self.record(payload)
        self.assertEqual(code, 1, err)
        self.assertEqual(len(self.gaps()), 2)
        self.assertIn("(1 already recorded)", out)

    def test_a_fixed_gap_that_comes_back_is_recorded_again(self):
        self.assertEqual(self.record(self.payload())[0], 1)
        gap_id = self.gaps()[0]["id"]
        code, _out, err = run(
            MANIFEST_SCRIPT, "set", "--manifest", self.manifest,
            "--path", "gaps[" + gap_id + "].status", "--value", "fixed",
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(self.record(self.payload())[0], 1)
        self.assertEqual([g["status"] for g in self.gaps()], ["fixed", "open"])

    def test_an_accepted_gap_is_not_recorded_a_second_time(self):
        self.assertEqual(self.record(self.payload())[0], 1)
        gap_id = self.gaps()[0]["id"]
        code, _out, err = run(
            MANIFEST_SCRIPT, "set", "--manifest", self.manifest,
            "--path", "gaps[" + gap_id + "].status", "--value", "accepted",
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(self.record(self.payload())[0], 1)
        self.assertEqual(len(self.gaps()), 1)

    def set_field(self, gap_id, field, value):
        code, _out, err = run(
            MANIFEST_SCRIPT, "set", "--manifest", self.manifest,
            "--path", "gaps[" + gap_id + "]." + field, "--value", value,
        )
        self.assertEqual(code, 0, err)

    def test_accepting_a_gap_the_documented_way_keeps_it_accepted(self):
        # Audit 6.15: verification.md says to record a triaged finding with
        # status accepted and a rationale. Rewriting the rationale used to wipe
        # the `fp:` key stored in it, and the next run filed the finding again
        # as a new open gap, turning the release gate red for an accepted risk.
        self.assertEqual(self.record(self.payload(severity="critical"))[0], 1)
        gap_id = self.gaps()[0]["id"]
        self.set_field(gap_id, "rationale",
                       "Accepted risk: internal-only tool, approved by ops lead (dec.ocr-accept)")
        self.set_field(gap_id, "status", "accepted")
        code, out, err = self.record(self.payload(severity="critical"))
        self.assertEqual(code, 1, err)
        self.assertIn("recorded 0 finding(s) as gaps (1 already recorded)", out)
        gaps = self.gaps()
        self.assertEqual([(g["id"], g["status"]) for g in gaps], [(gap_id, "accepted")])

    def test_the_legacy_rationale_suffix_is_still_honoured(self):
        # Gaps written before the `fingerprint` field keep their key in the
        # rationale; a re-run must not duplicate them either.
        finding = bridge.normalize_finding(comment())
        legacy = {
            "id": "ocr-20260901-src-orders-py-1", "severity": "high",
            "description": "src/orders.py:10 — SQL built by string concatenation (security, high)",
            "status": "open", "evidence": [],
            "rationale": "OCR delegate pass, delegate review mode, 2026-09-01; fp:"
            + bridge.finding_fingerprint(finding),
            "source": "ocr",
        }
        code, _out, err = run(
            MANIFEST_SCRIPT, "add", "--manifest", self.manifest, "--kind", "gap",
            "--json", json.dumps(legacy),
        )
        self.assertEqual(code, 0, err)
        code, out, err = self.record(self.payload())
        self.assertEqual(code, 1, err)
        self.assertIn("(1 already recorded)", out)
        self.assertEqual(len(self.gaps()), 1)

    def test_the_fingerprint_field_is_part_of_the_schema(self):
        self.assertEqual(self.record(self.payload())[0], 1)
        code, out, err = run(MANIFEST_SCRIPT, "validate", "--manifest", self.manifest, "--json")
        self.assertNotIn("unknown-field", out + err)
        self.assertIn("fingerprint", self.gaps()[0])

    def test_endpoint_reruns_do_not_duplicate(self):
        for _ in range(3):
            code, out, err = self.review("diff", "--manifest", self.manifest, "--engine", "ocr")
            self.assertEqual(code, 1, err)
        self.assertEqual(len(self.gaps()), 1)
        self.assertIn("(1 already recorded)", out)

    def test_path_separator_style_does_not_defeat_the_match(self):
        self.assertEqual(self.record(self.payload(path="src/orders.py"))[0], 1)
        # Backslash spelling of the same path (a Windows agent): same finding.
        self.record({"findings": [comment(path="src\\orders.py")], "skipped": []})
        self.assertEqual(len(self.gaps()), 1)


class GapShape(TempConsole):
    """Every gap the bridge writes is marked ocr and can never be a lifecycle escape."""

    def test_delegate_gaps_carry_source_ocr_and_no_entity_or_capability(self):
        payload = {"findings": [comment(), comment(path="src/b.py", start_line=3)], "skipped": []}
        self.assertEqual(self.record(payload)[0], 1)
        for gap in self.gaps():
            self.assertEqual(gap["source"], "ocr")
            self.assertNotIn("entity", gap)
            self.assertNotIn("capability", gap)
            # The dedupe key has its own field now (audit 6.15); the rationale
            # is free text people rewrite when they accept a gap.
            self.assertRegex(gap["fingerprint"], r"^[0-9a-f]{12}$")
            self.assertNotIn("fp:", gap["rationale"])

    def test_endpoint_gaps_carry_the_same_marks(self):
        code, _out, err = self.review("diff", "--manifest", self.manifest, "--engine", "ocr")
        self.assertEqual(code, 1, err)
        code, _out, err = self.review("scan", "--manifest", self.manifest)
        self.assertEqual(code, 1, err)
        gaps = self.gaps()
        self.assertGreaterEqual(len(gaps), 1)
        for gap in gaps:
            self.assertEqual(gap["source"], "ocr")
            self.assertNotIn("entity", gap)
            self.assertNotIn("capability", gap)

    def test_a_finding_that_mentions_an_entity_cannot_grow_an_entity_field(self):
        # OCR content is free text; whatever it says, the gap keeps its fixed shape.
        finding = comment(content="order.quarantined is never reachable", entity="order",
                          capability="order.quarantine")
        self.assertEqual(self.record({"findings": [finding]})[0], 1)
        gap = self.gaps()[0]
        self.assertEqual(gap["source"], "ocr")
        self.assertNotIn("entity", gap)
        self.assertNotIn("capability", gap)


class RuleFiles(TempConsole):
    """A missing rule file must not silently drop the shipped admin-console rules."""

    def test_missing_rule_file_exits_two_on_every_command(self):
        missing = str(self.tmp / "no-such-rules.json")
        for args in (
            ("diff", "--manifest", self.manifest, "--rule", missing),
            ("diff", "--manifest", self.manifest, "--engine", "ocr", "--rule", missing),
            ("scan", "--manifest", self.manifest, "--rule", missing),
        ):
            code, _out, err = self.review(*args)
            self.assertEqual(code, 2, args)
            self.assertIn("no-such-rules.json", err)
            self.assertNotIn("Traceback", err)
        self.assertEqual(self.ocr_calls(), [], "ocr must not have been run without its rules")

    def test_the_shipped_rules_are_passed_by_default_as_an_absolute_path(self):
        self.diff()
        preview = self.ocr_calls()[0]["args"]
        rule = preview[preview.index("--rule") + 1]
        self.assertTrue(os.path.isabs(rule), rule)
        self.assertTrue(os.path.samefile(rule, str(SKILL_ROOT / "assets" / "adminwright-ocr-rules.json")))

    def test_a_custom_rule_file_replaces_the_default(self):
        custom = self.tmp / "custom-rules.json"
        custom.write_text("{}", encoding="utf-8")
        self.diff("--rule", str(custom))
        preview = self.ocr_calls()[0]["args"]
        self.assertTrue(os.path.samefile(preview[preview.index("--rule") + 1], str(custom)))

    def test_missing_shipped_rules_are_an_error_not_a_silent_omission(self):
        namespace = mock.Mock(rule=None)
        with mock.patch.object(bridge, "DEFAULT_RULE_PATH", self.tmp / "gone.json"):
            with self.assertRaises(bridge.ReviewError) as caught:
                bridge.rule_arguments(namespace)
        self.assertIn("--rule", str(caught.exception))


class GapIds(unittest.TestCase):
    def test_a_slug_cut_at_forty_characters_never_ends_in_a_hyphen(self):
        # 39 letters, a slash, more: the cut lands on the hyphen that replaced
        # the slash, which the id pattern refuses once "-<n>" follows it.
        self.assertEqual(bridge.slug_for("a" * 39 + "/b.py"), "a" * 39)
        self.assertEqual(bridge.slug_for("src/orders.py"), "src-orders-py")
        self.assertEqual(bridge.slug_for("///"), "file")


class GapIdsEndToEnd(TempConsole):
    def test_a_path_whose_slug_ends_at_the_cut_is_recorded(self):
        path = "a" * 39 + "/b.py"
        code, _out, err = self.record({"findings": [comment(path=path)]})
        self.assertEqual(code, 1, err)
        gap_id = self.gaps()[0]["id"]
        self.assertNotIn("--", gap_id)


class ProjectRoot(TempConsole):
    """Audit 6.15: --project-root defaults to the manifest's project root, not the cwd."""

    def repo_of(self, call):
        args = call["args"]
        return args[args.index("--repo") + 1]

    def same(self, left, right):
        return os.path.normcase(os.path.abspath(str(left))) == os.path.normcase(os.path.abspath(str(right)))

    def set_project_root(self, value):
        data = json.loads(self.manifest.read_text(encoding="utf-8"))
        data["projectRoot"] = value
        self.manifest.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def test_default_is_the_manifests_project_root_regardless_of_cwd(self):
        self.diff()  # run() executes from the test runner's cwd, not the project
        calls = self.ocr_calls()
        self.assertEqual(len(calls), 2)
        for call in calls:
            self.assertTrue(self.same(self.repo_of(call), self.root), call)

    def test_repo_never_trails_the_file_list(self):
        self.diff()
        rule_call = [c for c in self.ocr_calls() if c["args"][:2] == ["delegate", "rule"]][0]["args"]
        self.assertLess(rule_call.index("--repo"), rule_call.index("src/orders.py"))

    def test_endpoint_engine_and_scan_use_it_too(self):
        self.review("diff", "--manifest", self.manifest, "--engine", "ocr")
        self.review("scan", "--manifest", self.manifest)
        for call in self.ocr_calls():
            self.assertTrue(self.same(self.repo_of(call), self.root), call)

    def test_explicit_flag_wins(self):
        other = self.tmp / "other-repo"
        other.mkdir()
        self.diff("--project-root", str(other))
        for call in self.ocr_calls():
            self.assertTrue(self.same(self.repo_of(call), other), call)

    def test_manifest_project_root_is_relative_to_the_manifest_directory(self):
        (self.root / "packages" / "admin").mkdir(parents=True)
        self.set_project_root("../packages/admin")
        self.diff()
        for call in self.ocr_calls():
            self.assertTrue(self.same(self.repo_of(call), self.root / "packages" / "admin"), call)

    def test_manifest_project_root_may_be_absolute(self):
        elsewhere = self.tmp / "elsewhere"
        elsewhere.mkdir()
        self.set_project_root(str(elsewhere))
        self.diff()
        for call in self.ocr_calls():
            self.assertTrue(self.same(self.repo_of(call), elsewhere), call)

    def test_a_project_root_that_does_not_exist_is_an_error(self):
        self.set_project_root("../no-such-dir")
        code, _out, err = self.review("diff", "--manifest", self.manifest)
        self.assertEqual(code, 2)
        self.assertIn("project root", err)
        self.assertIn("--project-root", err)
        self.assertEqual(self.ocr_calls(), [])


class ProjectRootHelper(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="adminwright-root-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.manifest = self.tmp / "repo" / "apps" / "admin" / ".admin-console" / "manifest.json"
        self.manifest.parent.mkdir(parents=True)

    def write(self, text, encoding="utf-8"):
        self.manifest.write_bytes(text.encode(encoding))

    def root(self):
        return bridge.project_root_of(self.manifest)

    def test_default_is_the_grandparent_of_the_manifest_file(self):
        self.write("{}")
        self.assertEqual(self.root(), Path(os.path.abspath(str(self.manifest.parent.parent))))

    def test_declared_root_is_resolved_against_the_manifest_directory(self):
        self.write(json.dumps({"projectRoot": "../../.."}))
        self.assertEqual(self.root(), Path(os.path.abspath(str(self.tmp / "repo"))))

    def test_bom_blank_non_string_and_unreadable_manifests_fall_back_to_the_default(self):
        default = Path(os.path.abspath(str(self.manifest.parent.parent)))
        self.write("﻿" + json.dumps({"projectRoot": ""}))
        self.assertEqual(self.root(), default)
        self.write(json.dumps({"projectRoot": 7}))
        self.assertEqual(self.root(), default)
        self.write("{not json")
        self.assertEqual(self.root(), default)
        self.manifest.unlink()
        self.assertEqual(self.root(), default)

    def test_a_bom_manifest_still_honours_its_declared_root(self):
        self.write("﻿" + json.dumps({"projectRoot": "../../.."}))
        self.assertEqual(self.root(), Path(os.path.abspath(str(self.tmp / "repo"))))

    def test_evidence_is_cited_relative_to_the_declared_root(self):
        self.write(json.dumps({"projectRoot": "../../.."}))
        captured = {}

        def fake_add(manifest, gaps):
            captured["gaps"] = gaps
            return subprocess.CompletedProcess([], 0, stdout="", stderr="")

        finding = bridge.normalize_finding(
            {"path": "a.py", "content": "c", "start_line": 1, "severity": "low", "category": "bug"}
        )
        evidence = self.manifest.parent / "ocr-bundle-x.json"
        with mock.patch.object(bridge, "add_gaps", side_effect=fake_add):
            bridge.persist_findings(self.manifest, [finding], "delegate", "delegate review", evidence)
        self.assertEqual(captured["gaps"][0]["evidence"], ["apps/admin/.admin-console/ocr-bundle-x.json"])


def pid_alive(pid):
    if os.name == "nt":
        listing = subprocess.run(
            ["tasklist", "/FI", "PID eq %d" % pid, "/NH"], capture_output=True, text=True
        )
        return str(pid) in listing.stdout
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


class Timeouts(TempConsole):
    """Audit 6.15: run_ocr had no timeout, so a hung ocr hung the agent."""

    def test_flag_stops_a_hung_ocr_with_a_clear_message(self):
        started = time.time()
        code, _out, err = self.review(
            "diff", "--manifest", self.manifest, "--timeout", "1", env={"FAKE_SLEEP": "30"}
        )
        self.assertEqual(code, 2)
        self.assertIn("did not finish within 1 seconds", err)
        self.assertIn("--timeout", err)
        self.assertLess(time.time() - started, 25)
        self.assertNotIn("Traceback", err)

    def test_environment_variable_sets_the_default(self):
        code, _out, err = self.review(
            "scan", "--manifest", self.manifest,
            env={"FAKE_SLEEP": "30", "ADMINWRIGHT_OCR_TIMEOUT": "1"},
        )
        self.assertEqual(code, 2)
        self.assertIn("did not finish within 1 seconds", err)

    def test_bad_timeouts_are_usage_errors(self):
        for args, env in (
            (("--timeout", "0"), {}),
            (("--timeout", "-5"), {}),
            ((), {"ADMINWRIGHT_OCR_TIMEOUT": "soon"}),
        ):
            code, _out, err = self.review("diff", "--manifest", self.manifest, *args, env=env)
            self.assertEqual(code, 2, (args, env))
            self.assertNotIn("Traceback", err)
        self.assertEqual(self.ocr_calls(), [])

    def test_a_non_finite_timeout_is_refused_before_ocr_runs(self):
        # `--timeout inf` crashed communicate() with OverflowError: exit 1
        # ("findings recorded"), a traceback, and ocr left running.
        for command, args, env in (
            ("diff", ("--timeout", "inf"), {}),
            ("diff", ("--timeout", "nan"), {}),
            ("scan", (), {"ADMINWRIGHT_OCR_TIMEOUT": "inf"}),
            ("diff", (), {"ADMINWRIGHT_OCR_TIMEOUT": "-Infinity"}),
        ):
            code, _out, err = self.review(command, "--manifest", self.manifest, *args, env=env)
            self.assertEqual(code, 2, (command, args, env, err))
            self.assertNotIn("Traceback", err)
        self.assertEqual(self.ocr_calls(), [])

    def test_a_huge_timeout_is_lowered_to_the_maximum_not_a_crash(self):
        for command, args, env in (
            ("diff", ("--timeout", "1e30"), {}),
            ("scan", (), {"ADMINWRIGHT_OCR_TIMEOUT": "1e30"}),
        ):
            code, _out, err = self.review(command, "--manifest", self.manifest, *args, env=env)
            self.assertIn(code, (0, 1), (command, err))
            self.assertNotIn("Traceback", err)
            self.assertIn("using " + str(bridge.MAX_TIMEOUT_SECONDS), err)
        self.assertEqual(bridge.ocr_timeout(mock.Mock(timeout=1e30)), bridge.MAX_TIMEOUT_SECONDS)
        self.assertEqual(bridge.ocr_timeout(mock.Mock(timeout=120.0)), 120.0)

    def test_ocr_is_stopped_whatever_communicate_raises(self):
        proc = mock.Mock(pid=4242)
        proc.communicate.side_effect = OverflowError("timestamp out of range for platform time_t")
        with mock.patch.object(bridge.subprocess, "Popen", return_value=proc):
            with mock.patch.object(bridge, "kill_tree") as kill:
                with self.assertRaises(OverflowError):
                    bridge.run_bounded(["ocr"], {}, 5)
        kill.assert_called_once_with(proc)

    def test_a_slow_but_finishing_ocr_is_not_cut_off(self):
        self.diff("--timeout", "20", env={"FAKE_SLEEP": "0.5"})

    def test_the_default_is_generous_enough_for_a_real_review(self):
        self.assertGreaterEqual(bridge.DEFAULT_TIMEOUT_SECONDS, 600)
        with mock.patch.dict(os.environ):
            os.environ.pop("ADMINWRIGHT_OCR_TIMEOUT", None)
            self.assertEqual(bridge.ocr_timeout(mock.Mock(timeout=None)), bridge.DEFAULT_TIMEOUT_SECONDS)

    def test_the_whole_process_tree_is_stopped(self):
        # The npm launcher is node spawning the native binary: killing only the
        # direct child would leave the review running after "timed out".
        pidfile = self.tmp / "grandchild.pid"
        code, _out, _err = self.review(
            "diff", "--manifest", self.manifest, "--timeout", "4",
            env={"FAKE_SLEEP": "30", "FAKE_GRANDCHILD": str(pidfile)},
        )
        self.assertEqual(code, 2)
        deadline = time.time() + 10
        while time.time() < deadline and not (pidfile.exists() and pidfile.read_text().strip()):
            time.sleep(0.1)
        if not (pidfile.exists() and pidfile.read_text().strip()):
            self.skipTest("the grandchild never started; nothing to observe")
        pid = int(pidfile.read_text().strip())

        def reap():
            if pid_alive(pid):
                if os.name == "nt":
                    subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
                else:
                    os.kill(pid, 9)

        self.addCleanup(reap)
        deadline = time.time() + 10
        while time.time() < deadline and pid_alive(pid):
            time.sleep(0.2)
        self.assertFalse(pid_alive(pid), "the timed-out ocr left a child process running")


class ConsoleEncoding(TempConsole):
    """Review text is arbitrary Unicode; a legacy console codepage must not crash it."""

    TEXT = "Schlüssel \U0001f511 ключ — 日本語"

    def test_non_cp1252_text_survives_a_cp1252_console(self):
        payload = {"findings": [comment(path="src/über.py", content=self.TEXT)]}
        path = self.tmp / "findings.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        code, out, err = self.review(
            "record", "--manifest", self.manifest, "--findings", "@" + str(path),
            env={"PYTHONIOENCODING": "cp1252"},
        )
        self.assertEqual(code, 1, err)
        self.assertNotIn("Traceback", err)
        self.assertIn("Schlüssel", self.gaps()[0]["description"])
        self.assertIn("日本語", self.gaps()[0]["description"])

    def test_a_bom_findings_file_from_powershell_is_accepted(self):
        path = self.tmp / "findings.json"
        path.write_bytes(b"\xef\xbb\xbf" + json.dumps({"findings": [comment()]}).encode("utf-8"))
        code, _out, err = self.review(
            "record", "--manifest", self.manifest, "--findings", "@" + str(path)
        )
        self.assertEqual(code, 1, err)

    def test_a_bom_on_stdin_and_non_ascii_text_on_stdin(self):
        text = "﻿" + json.dumps({"findings": [comment(content=self.TEXT)]}, ensure_ascii=False)
        code, _out, err = self.review(
            "record", "--manifest", self.manifest, "--findings", "-", input=text,
            env={"PYTHONIOENCODING": "cp1252"},  # stdin would otherwise decode as cp1252
        )
        self.assertEqual(code, 1, err)
        self.assertIn("Schlüssel", self.gaps()[0]["description"])


class FindingsInput(TempConsole):
    """Windows-written findings: decoded like the manifest script, never exit 1 for nothing.

    Windows PowerShell 5.1's `>` and Out-File write UTF-16LE with a byte-order
    mark, and Set-Content without -Encoding writes the ANSI code page. Both used
    to end in a UnicodeDecodeError traceback and exit 1, which the exit table
    defines as "findings recorded", with no gap written.
    """

    TEXT = "Schlüssel 東京 \U0001f511"

    def payload_bytes(self, encoding, bom=b""):
        payload = {"findings": [comment(content=self.TEXT)]}
        return bom + json.dumps(payload, ensure_ascii=False).encode(encoding)

    def record_file(self, data, flag="--findings-file"):
        path = self.tmp / "findings.json"
        path.write_bytes(data)
        value = str(path) if flag == "--findings-file" else "@" + str(path)
        option = "--findings-file" if flag == "--findings-file" else "--findings"
        return self.review("record", "--manifest", self.manifest, option, value)

    def record_stdin(self, data):
        env = dict(os.environ)
        env.update({
            "ADMINWRIGHT_OCR_BIN": str(self.launcher), "PYTHONIOENCODING": "cp1252",
            "ADMINWRIGHT_HOME": scratch_home(),
        })
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "record", "--manifest", str(self.manifest), "--findings", "-"],
            input=data, capture_output=True, env=env,
        )
        return proc.returncode, proc.stdout.decode("utf-8"), proc.stderr.decode("utf-8")

    def assert_recorded(self, result):
        code, out, err = result
        self.assertEqual(code, 1, out + err)
        self.assertNotIn("Traceback", err)
        self.assertIn(self.TEXT, self.gaps()[-1]["description"])

    def assert_refused(self, result, *needles):
        code, out, err = result
        self.assertEqual(code, 2, out + err)
        self.assertNotIn("Traceback", err)
        for needle in needles:
            self.assertIn(needle, err)
        self.assertEqual(self.gaps(), [])

    def test_a_utf16_file_as_powershell_redirection_writes_it_is_read(self):
        for flag in ("--findings-file", "--findings"):
            self.assert_recorded(self.record_file(self.payload_bytes("utf-16-le", codecs.BOM_UTF16_LE), flag))

    def test_big_endian_utf16_and_utf8_with_or_without_bom_are_read(self):
        # Content differs per run so none is deduplicated away.
        for index, (encoding, bom) in enumerate((
            ("utf-16-be", codecs.BOM_UTF16_BE), ("utf-8", codecs.BOM_UTF8), ("utf-8", b""),
        ), 1):
            self.TEXT = "Schlüssel %d 東京" % index
            self.assert_recorded(self.record_file(self.payload_bytes(encoding, bom)))
        self.assertEqual(len(self.gaps()), 3)

    def test_an_ansi_file_from_set_content_is_refused_with_a_hint(self):
        data = json.dumps({"findings": [comment(content="Schlüssel")]}, ensure_ascii=False).encode("cp1252")
        self.assert_refused(self.record_file(data), "not valid UTF-8", "Re-save it as UTF-8")
        self.assert_refused(self.record_file(data, "--findings"), "not valid UTF-8")

    def test_a_bom_followed_by_bad_bytes_is_refused_not_a_traceback(self):
        self.assert_refused(self.record_file(codecs.BOM_UTF8 + b'{"findings": ["\xfc"]}'), "not valid UTF-8")
        self.assert_refused(self.record_file(codecs.BOM_UTF16_LE + b"{\x00x"), "not valid UTF-16")

    def test_stdin_is_decoded_the_same_way(self):
        self.assert_recorded(self.record_stdin(self.payload_bytes("utf-16-le", codecs.BOM_UTF16_LE)))

    def test_undecodable_stdin_points_at_output_encoding(self):
        data = json.dumps({"findings": [comment(content="Schlüssel")]}, ensure_ascii=False).encode("cp1252")
        self.assert_refused(self.record_stdin(data), "standard input", "$OutputEncoding", "--findings-file")

    def test_findings_and_findings_file_are_exclusive_and_one_is_required(self):
        path = self.tmp / "findings.json"
        path.write_text(json.dumps({"findings": [comment()]}), encoding="utf-8")
        code, _out, err = self.review(
            "record", "--manifest", self.manifest, "--findings", "[]", "--findings-file", str(path)
        )
        self.assertEqual(code, 2)
        self.assertIn("not allowed with", err)
        code, _out, err = self.review("record", "--manifest", self.manifest)
        self.assertEqual(code, 2)
        self.assertIn("--findings", err)
        self.assertEqual(self.gaps(), [])

    def test_a_missing_findings_file_is_a_usage_error(self):
        code, _out, err = self.review(
            "record", "--manifest", self.manifest, "--findings-file", str(self.tmp / "nope.json")
        )
        self.assertEqual(code, 2)
        self.assertIn("could not read findings file", err)
        self.assertNotIn("Traceback", err)

    def test_inline_json_that_powershell_mangled_names_the_file_option(self):
        # What PS 5.1 hands Python for '{"findings": []}': the quotes are gone.
        code, _out, err = self.review("record", "--manifest", self.manifest, "--findings", "{findings: []}")
        self.assertEqual(code, 2)
        self.assertIn("not valid JSON", err)
        self.assertIn("--findings-file", err)

    def test_the_printed_record_command_is_safe_to_paste_into_powershell(self):
        out = self.diff()
        line = [text for text in out.splitlines() if " record " in text][0]
        self.assertIn("--findings-file <findings.json>", line)
        self.assertNotRegex(line, r"(^|\s)@", "a bare leading @ is splatting in PowerShell")


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
        # The npm install has no ocr.exe; its binary is opencodereview.exe.
        self.assertIn("opencodereview.exe", message)
        self.assertNotIn("(ocr.exe)", message)
        self.assertNotRegex(message, r"(?<![\w-])ocr\.exe")

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
            with mock.patch.object(bridge, "stderr"):  # the expected diagnostic line
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
