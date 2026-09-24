# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TarikAI
"""The behavioural eval suite in evals/behavior is well formed, and its fixtures work.

`claude plugin eval` runs that suite against a live model, which costs money and needs a
logged-in Claude Code, so it is run by hand or from the manual CI workflow. This test runs
everywhere and checks what can break without a model:
- every case and grader file uses only the keys and types the eval runner accepts;
- regexes compile, and the trigger graders name this skill;
- smoke cases need no tool grant, and shell cases ask for Bash;
- every scaffold runs, and the manifest it seeds passes plan validation;
- no grader already passes on the untouched workspace, unless it is a check that the run
  preserved something. A grader that cannot fail measures nothing.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from cli_helpers import SKILL_ROOT, run

try:
    import yaml
except ImportError:  # CI installs PyYAML; without it the structural checks are skipped
    yaml = None

SUITE = SKILL_ROOT / "evals" / "behavior"
PROMPT_KEYS = {
    "schema_version", "name", "description", "tags", "plugins", "runs", "expected_outcome",
    "model", "max_turns", "timeout_seconds", "allowed_tools", "append_system_prompt", "env",
}
CASE_KEYS = {"schema_version", "name", "description", "tags", "plugins", "runs", "expected_outcome",
             "context", "execution", "graders"}
CONTEXT_KEYS = {"scaffold_script", "history_file", "add_dirs"}
GRADER_OPTIONS = {
    "regex": {"pattern", "flags", "match", "target"},
    "tool_used": {"tool", "input_match", "min", "max"},
    "tool_order": {"before", "after"},
    "file_exists": {"path", "exists"},
    "llm": {"criteria", "focus"},
    "baseline": {"baseline_file", "criteria"},
}
COMMON_GRADER_KEYS = {"type", "weight", "arm"}
READ_ONLY_TOOLS = {"Read", "Glob", "Grep", "NotebookRead", "Skill", "Agent", "TodoWrite",
                   "TaskCreate", "TaskGet", "TaskList", "TaskUpdate", "TaskStop"}
TARGETS = {"last_message", "trace", "files", "mock_calls"}
# Graders that check the run LEFT something as it was, so they pass on the untouched
# workspace by design. Every other file-based grader must fail there.
PRESERVATION = {
    ("architect-plans-before-building", "no-feature-code"),
    ("implementer-builds-on-real-data", "not-self-reviewed"),
    ("security-files-gaps-fixes-nothing", "defect-left-in-place"),
    ("security-files-gaps-fixes-nothing", "not-signed-off"),
    ("qa-contests-instead-of-signing", "not-signed-off"),
}


def frontmatter(path):
    text = path.read_text(encoding="utf-8")
    match = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n?(.*)$", text, re.S)
    assert match, path.as_posix() + ": no frontmatter block"
    data = yaml.safe_load(match.group(1)) or {}
    assert isinstance(data, dict), path.as_posix() + ": frontmatter is not a mapping"
    return data, match.group(2)


def case_dirs():
    found = []
    for marker in sorted(SUITE.rglob("*")):
        if marker.name not in ("prompt.md", "case.yaml") or "_fixtures" in marker.parts:
            continue
        if "results" in marker.relative_to(SUITE).parts:
            continue
        if marker.parent not in found:
            found.append(marker.parent)
    return found


def js_regex(pattern, flags=""):
    """A JavaScript pattern as Python compiles it; the suite keeps to the common subset."""
    assert "(?i)" not in pattern, "inline (?i) is not supported by the runner; use flags: i"
    return re.compile(pattern, re.I if "i" in (flags or "") else 0)


def load_case(directory):
    prompt, body = frontmatter(directory / "prompt.md")
    case = {}
    if (directory / "case.yaml").is_file():
        case = yaml.safe_load((directory / "case.yaml").read_text(encoding="utf-8")) or {}
    graders = {}
    for path in sorted((directory / "graders").glob("*.md")):
        data, text = frontmatter(path)
        graders[path.stem] = (data, text)
    return prompt, body, case, graders


def find_bash():
    """A POSIX bash. On Windows, CreateProcess finds System32's bash.exe (the WSL launcher,
    which fails without a Linux distribution) before anything on PATH, so Git's bash is
    located from git itself."""
    if os.name != "nt":
        return shutil.which("bash")
    git = shutil.which("git")
    if git:
        root = Path(git).resolve().parent.parent  # <Git>/cmd/git.exe or <Git>/bin/git.exe
        for candidate in (root / "bin" / "bash.exe", root / "usr" / "bin" / "bash.exe"):
            if candidate.is_file():
                return str(candidate)
    found = shutil.which("bash")
    return found if found and "system32" not in found.lower() else None


BASH = find_bash()


def scaffold(directory, case):
    """Run the case's scaffold in a fresh workspace; return the workspace (the caller removes it)."""
    workspace = Path(tempfile.mkdtemp(prefix="adminwright-evalcase-"))
    script = (case.get("context") or {}).get("scaffold_script")
    if script:
        done = subprocess.run([BASH, str(directory / script)], cwd=str(workspace),
                              capture_output=True, text=True, encoding="utf-8", errors="replace")
        if done.returncode != 0:
            shutil.rmtree(str(workspace), ignore_errors=True)
            raise AssertionError(directory.name + ": scaffold failed: " + done.stderr[-600:])
    return workspace


@unittest.skipIf(yaml is None, "PyYAML is not installed")
class TestSuiteShape(unittest.TestCase):
    def test_the_plugin_points_the_runner_at_the_suite(self):
        manifest = json.loads((SKILL_ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest.get("experimental", {}).get("evals"), "evals/behavior",
                         "evals/ holds the validator fixtures; the eval runner must look elsewhere")
        self.assertIn("evals/behavior/results/", (SKILL_ROOT / ".gitignore").read_text(encoding="utf-8"))

    def test_every_case_and_grader_is_well_formed(self):
        cases = case_dirs()
        self.assertGreaterEqual(len(cases), 10)
        names = set()
        skill = frontmatter(SKILL_ROOT / "SKILL.md")[0]["name"]
        for directory in cases:
            with self.subTest(case=directory.name):
                prompt, body, case, graders = load_case(directory)
                self.assertTrue(body.strip(), "the prompt body is empty")
                self.assertEqual(set(prompt) - PROMPT_KEYS, set(), "unknown prompt.md keys are an error")
                if case:
                    self.assertEqual(str(case.get("schema_version")), "1.1")
                    self.assertEqual(case.get("name"), directory.name)
                    self.assertEqual(set(case) - CASE_KEYS, set())
                    self.assertEqual(set(case.get("context") or {}) - CONTEXT_KEYS, set())
                    script = (case.get("context") or {}).get("scaffold_script")
                    if script:
                        self.assertTrue((directory / script).is_file(), script)
                name = prompt.get("name") or case.get("name") or directory.name
                self.assertNotIn(name, names, "case names must be unique; --case and the report key on them")
                names.add(name)
                self.assertLessEqual(prompt.get("max_turns", 10), 200)
                self.assertLessEqual(prompt.get("timeout_seconds", 300), 3600)
                tools = set(prompt.get("allowed_tools") or [])
                tags = set(prompt.get("tags") or [])
                self.assertEqual(len(tags & {"smoke", "shell"}), 1, "tag each case smoke or shell")
                if "smoke" in tags:
                    self.assertEqual(tools - READ_ONLY_TOOLS, set(),
                                     "smoke cases must run with no tool grant, and so on native Windows")
                else:
                    self.assertIn("Bash", tools, "shell cases run the manifest CLI")
                self.assertTrue(graders, "a case needs at least one grader")
                scored = [g for g, (data, _t) in graders.items()
                          if data.get("arm") != "with-only"
                          and not (data.get("type") == "tool_used" and data.get("tool") == "Skill"
                                   and data.get("arm") != "both")]
                self.assertTrue(scored, "every grader is an indicator; nothing would be scored")
                for grader, (data, text) in graders.items():
                    kind = data.get("type")
                    self.assertIn(kind, GRADER_OPTIONS, grader)
                    self.assertEqual(set(data) - COMMON_GRADER_KEYS - GRADER_OPTIONS[kind], set(), grader)
                    self.assertIn(data.get("arm"), (None, "with-only", "both"), grader)
                    if kind == "regex":
                        js_regex(data["pattern"], data.get("flags"))
                        match = data.get("match", "contains")
                        self.assertTrue(match in ("contains", "not_contains") or re.match(r"count:\d+$", match))
                    if kind in ("regex", "llm"):
                        target = data.get("target" if kind == "regex" else "focus", "last_message")
                        if isinstance(target, dict):
                            self.assertEqual(target.get("source"), "file", grader)
                            self.assertTrue(target.get("path"), grader)
                        else:
                            self.assertIn(target, TARGETS, grader)
                    if kind == "tool_used":
                        if data.get("input_match"):
                            js_regex(data["input_match"])
                        if data.get("tool") == "Skill":
                            self.assertTrue(js_regex(data["input_match"]).search(
                                '{"skill": "' + skill + ":" + skill + '"}'), grader)
                    if kind == "llm":
                        self.assertIn("PASS", text, grader + ": write the rubric as PASS and FAIL conditions")
                        self.assertIn("FAIL", text, grader)


@unittest.skipIf(yaml is None, "PyYAML is not installed")
@unittest.skipIf(BASH is None or shutil.which("git") is None, "needs bash and git")
class TestScaffolds(unittest.TestCase):
    def test_scaffolds_seed_a_valid_workspace_and_no_grader_passes_on_it(self):
        for directory in case_dirs():
            prompt, _body, case, graders = load_case(directory)
            with self.subTest(case=directory.name):
                workspace = scaffold(directory, case)
                try:
                    manifest = workspace / ".admin-console" / "manifest.json"
                    if manifest.is_file():
                        code, out, err = run("validate", "--manifest", manifest, "--phase", "plan")
                        self.assertEqual(code, 0, out + err)
                    for grader, (data, _text) in graders.items():
                        outcome = initial_outcome(workspace, data)
                        if outcome is None:
                            continue  # judged on the transcript: nothing to check before the run
                        expected = (directory.name, grader) in PRESERVATION
                        self.assertEqual(outcome, expected, grader + (
                            " passes before the run, so it cannot fail" if outcome
                            else " is listed as a preservation check but fails on the untouched workspace"))
                finally:
                    shutil.rmtree(str(workspace), ignore_errors=True)


def initial_outcome(workspace, grader):
    """How a file-based grader scores the workspace before Claude touches it, or None."""
    kind = grader.get("type")
    if kind == "file_exists":
        # Only files created during the run count, so before the run none exist.
        return grader.get("exists", True) is False
    if kind == "regex" and isinstance(grader.get("target"), dict):
        path = workspace / grader["target"]["path"]
        if not path.is_file():
            return False
        found = bool(js_regex(grader["pattern"], grader.get("flags")).search(
            path.read_text(encoding="utf-8")))
        return not found if grader.get("match") == "not_contains" else found
    return None


if __name__ == "__main__":
    unittest.main()
