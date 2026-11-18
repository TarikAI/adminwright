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
    ("architect-plans-before-building", "page-left-as-is"),
    ("architect-plans-before-building", "route-left-as-is"),
    ("architect-plans-before-building", "server-left-as-is"),
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


    def test_a_manifest_rewritten_by_hand_does_not_pass_the_qa_case(self):
        # A-10: `contested` alone passes on a manifest the main session rewrote with its
        # file tools. The process grader only a CLI run satisfies must fail on it.
        directory = SUITE / "shell" / "qa-contests-instead-of-signing"
        _prompt, _body, case, graders = load_case(directory)
        workspace = scaffold(directory, case)
        try:
            manifest = workspace / ".admin-console" / "manifest.json"
            data = json.loads(manifest.read_text(encoding="utf-8"))
            for entity in data["entities"]:
                for capability in entity.get("capabilities", []):
                    if capability["id"] == "tenant.suspend":
                        capability["reviewStatus"] = "contested"
            # Python 3.9's write_text has no newline= argument.
            with open(manifest, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(data, indent=2))
            self.assertTrue(initial_outcome(workspace, graders["contested"][0]))
            self.assertFalse(initial_outcome(workspace, graders["verdict-through-the-cli"][0]),
                             "a hand-written verdict must not pass the process grader")
        finally:
            shutil.rmtree(str(workspace), ignore_errors=True)


def grader(case, name):
    return frontmatter(SUITE / "shell" / case / "graders" / (name + ".md"))[0]


def regex_passes(data, text):
    found = bool(js_regex(data["pattern"], data.get("flags")).search(text))
    return not found if data.get("match") == "not_contains" else found


def doc_corpus():
    """Every file a session may read while running the suite: the skill, its references,
    the agents and the case prompts."""
    paths = [SKILL_ROOT / "SKILL.md", SKILL_ROOT / "INTEGRATIONS.md", SKILL_ROOT / "README.md"]
    paths += sorted((SKILL_ROOT / "references").glob("*.md"))
    paths += sorted((SKILL_ROOT / "agents").glob("*.md"))
    paths += sorted(SUITE.rglob("prompt.md"))
    return [(p, p.read_text(encoding="utf-8")) for p in paths if p.is_file()]


@unittest.skipIf(yaml is None, "PyYAML is not installed")
class TestGradersRejectWrongAnswers(unittest.TestCase):
    """Each grader the audit found passing on a wrong answer, checked against that answer
    without a model (A-02, A-09, A-10, A-11). Each also passes a right answer, so the
    tightening did not make it impossible."""

    def test_trace_graders_do_not_match_the_documentation(self):
        # A-02: `target: trace` sees tool results. One Read of multi-agent.md or
        # agents/README.md put `STATUS: DONE` and `NEEDS_CONTEXT` in the trace.
        trace_graders = []
        for directory in case_dirs():
            for name, (data, _text) in load_case(directory)[3].items():
                if data.get("type") == "regex" and data.get("target") == "trace":
                    trace_graders.append((directory.name + "/" + name, data))
        self.assertGreaterEqual(len(trace_graders), 5)
        corpus = doc_corpus()
        for label, data in trace_graders:
            for path, text in corpus:
                for shape, seen in (("raw", text), ("json", json.dumps(text)),
                                    ("tool result", json.dumps({"content": [{"type": "text", "text": text}]}))):
                    with self.subTest(grader=label, doc=path.name, shape=shape):
                        self.assertFalse(regex_passes(data, seen),
                                         label + " passes on a read of " + path.name)
        # The old, unanchored form failed this check: the corpus does quote the words.
        joined = json.dumps("\n".join(text for _p, text in corpus))
        self.assertTrue(re.search(r"STATUS: (?:DONE|DONE_WITH_CONCERNS)", joined))
        self.assertTrue(re.search(r"NEEDS_CONTEXT", joined))

    def test_trace_graders_pass_a_real_status_line(self):
        returned = json.dumps({"content": [{"type": "text", "text": "STATUS: %s\nThe console ..."}]})
        for case, statuses in (("architect-plans-before-building", ["DONE", "DONE_WITH_CONCERNS"]),
                               ("security-files-gaps-fixes-nothing", ["DONE", "BLOCKED"]),
                               ("harvester-asks-for-the-conversation", ["NEEDS_CONTEXT"])):
            name = "needs-context" if case.startswith("harvester") else "returned-a-status"
            for status in statuses:
                self.assertTrue(regex_passes(grader(case, name), returned % status), case + " " + status)
                self.assertTrue(regex_passes(grader(case, name), "Done.\nSTATUS: " + status + "\n"), case)
        self.assertFalse(regex_passes(grader("harvester-asks-for-the-conversation", "needs-context"),
                                      json.dumps("If you have no digest, return NEEDS_CONTEXT.")))

    def test_the_architect_may_not_rewrite_scaffolded_source(self):
        # A-09: file_exists sees only files the run created, so an edit to the decorative
        # page passed `no-feature-code`. Each scaffolded file now has to match exactly.
        base = SUITE / "_fixtures" / "support-desk"
        for name, relative in (("page-left-as-is", "src/app/admin/tenants/page.tsx"),
                               ("route-left-as-is", "src/app/api/admin/tenants/route.ts"),
                               ("server-left-as-is", "src/server/tenants.ts")):
            data = grader("architect-plans-before-building", name)
            self.assertEqual(data["target"]["path"], relative)
            original = (base / relative).read_text(encoding="utf-8")
            self.assertTrue(regex_passes(data, original), name)
            self.assertTrue(regex_passes(data, original.replace("\n", "\r\n")), name + " with CRLF")
            for wrong in (original.replace("return", "return /* wired */", 1),
                          original + "export const extra = 1;\n",
                          "// wired to the real query\n" + original,
                          "export default function Page() { return null; }\n"):
                self.assertFalse(regex_passes(data, wrong), name + " passes on an edited file")

    def test_the_session_check_needs_a_call_and_a_refusal(self):
        # A-11: the bare word `session` in a comment scored weight 2.
        data = grader("implementer-builds-on-real-data", "server-checks-the-session")
        original = (SUITE / "_fixtures" / "support-desk" / "src/app/api/admin/tenants/route.ts").read_text(
            encoding="utf-8")
        self.assertFalse(regex_passes(data, "// TODO: session check\n" + original))
        self.assertFalse(regex_passes(data, original.replace("export", "const session = null;\nexport", 1)))
        right = ('import { auth } from "@/server/auth";\n' + original.replace(
            "{\n", "{\n  const session = await auth();\n"
            "  if (!session) return new Response(null, { status: 401 });\n", 1))
        self.assertTrue(regex_passes(data, right))

    def test_a_risky_command_is_one_capability(self):
        # A-11: "kind":"command" and "risk":"high" could come from different capabilities.
        data = grader("architect-plans-before-building", "roles-and-a-risky-command")
        roles = '"roles": [{"id": "admin"}], '
        split = roles + ('"capabilities": [{"id": "a.list", "kind": "query", "risk": "high"}, '
                         '{"id": "a.suspend", "kind": "command", "risk": "low"}]')
        self.assertFalse(regex_passes(data, split))
        joined = roles + ('"capabilities": [{"id": "a.suspend", "kind": "command", '
                          '"entityStates": {"from": ["ACTIVE"], "to": ["SUSPENDED"]}, "risk": "high"}]')
        self.assertTrue(regex_passes(data, joined))
        reversed_order = roles + '"capabilities": [{"risk": "critical", "roles": ["admin"], "kind": "command"}]'
        self.assertTrue(regex_passes(data, reversed_order))

    def test_the_build_order_is_a_decision(self):
        # A-11: `spine` in a later gap or feedback entry satisfied the old pattern.
        data = grader("architect-plans-before-building", "build-order-recorded")
        wrong = ('"decisions": [{"id": "dec.profile", "decision": "standard profile"}], '
                 '"gaps": [{"id": "g-1", "description": "the auth spine is missing"}]')
        self.assertFalse(regex_passes(data, wrong))
        self.assertTrue(regex_passes(data, '"decisions": [{"id": "dec.build-order", "decision": "tenant.list, then tenant.suspend"}]'))
        self.assertTrue(regex_passes(data, '"decisions": [{"id": "d-7", "decision": "Walking skeleton first: staff sign-in"}]'))


@unittest.skipIf(yaml is None, "PyYAML is not installed")
class TestEvalWorkflow(unittest.TestCase):
    """The manual workflow spends real money with a repository secret (T-18, A-17)."""

    def setUp(self):
        self.path = SKILL_ROOT / ".github" / "workflows" / "behavior-evals.yml"
        self.workflow = yaml.safe_load(self.path.read_text(encoding="utf-8"))

    def test_the_api_key_reaches_only_the_steps_that_need_it(self):
        for job_id, job in self.workflow["jobs"].items():
            self.assertNotIn("secrets.", json.dumps(job.get("env") or {}), job_id + ": job-level env")
            for step in job["steps"]:
                if "secrets." in json.dumps(step):
                    self.assertIn(step.get("name"), ("Require the API key secret", "Run the suite"),
                                  "a secret reaches step " + str(step.get("name") or step.get("uses")))
                    self.assertNotIn("uses", step, "a secret reaches a third-party action")

    def test_every_global_npm_install_names_a_version(self):
        installs = 0
        for job in self.workflow["jobs"].values():
            for step in job["steps"]:
                for command in re.findall(r"npm install -g [^\n&|;]+", step.get("run", "")):
                    installs += 1
                    for package in command.split()[3:]:
                        if not package.startswith("-"):
                            self.assertRegex(package, r"^@?[^@\s]+@\d+\.\d+\.\d+$", command)
        self.assertGreater(installs, 0)

    def test_the_default_cost_ceiling_covers_the_shell_estimate(self):
        # A-17: a 25-dollar default stopped a shell run partway, wasting it.
        run_step = [s for s in self.workflow["jobs"]["evals"]["steps"] if s.get("name") == "Run the suite"][0]
        readme = (SUITE / "README.md").read_text(encoding="utf-8")
        shell_estimate = int(re.search(r"--tag shell [^\n]*--max-cost-usd (\d+)", readme).group(1))
        default = str(self.workflow[True if True in self.workflow else "on"]["workflow_dispatch"]["inputs"]
                      ["max_cost_usd"].get("default", ""))
        if default:
            self.assertGreaterEqual(int(default), shell_estimate)
        else:
            defaults = dict(re.findall(r"(smoke|shell|all)\) MAX_COST=(\d+)", run_step["run"]))
            self.assertGreaterEqual(int(defaults["shell"]), shell_estimate)
            self.assertGreater(int(defaults["all"]), int(defaults["shell"]))


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
