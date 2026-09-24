# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TarikAI
"""Regression tests for install_agents.py.

Standard library only (unittest). Run: python -m unittest discover -s tests -v
"""

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

try:
    import tomllib  # Python 3.11+; the TOML round-trip checks skip without it
except ImportError:  # pragma: no cover
    tomllib = None

SKILL_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = SKILL_ROOT / "scripts" / "install_agents.py"


def load_module():
    spec = importlib.util.spec_from_file_location("ia", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ia = load_module()


def run(*args):
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)] + [str(a) for a in args],
        capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
    )
    return proc.returncode, proc.stdout, proc.stderr


def run_piped_cp1252(*args):
    """Run as an agent harness does on Windows: stdout is a pipe, so Python picks
    the ANSI code page. Returns raw bytes so the test decodes them itself."""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "cp1252"
    env.pop("PYTHONUTF8", None)
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)] + [str(a) for a in args],
        capture_output=True, env=env,
    )
    return proc.returncode, proc.stdout, proc.stderr


def frontmatter_of(text):
    """Top-level `key: value` pairs of a leading --- block, values left raw."""
    assert text.startswith("---\n"), "no frontmatter"
    block = text.split("\n---", 1)[0][4:]
    fields = {}
    for line in block.split("\n"):
        match = re.match(r"([A-Za-z_][\w-]*):[ \t]*(.*)$", line)
        if match:
            fields[match.group(1)] = match.group(2)
    return fields


def make_skill(dest):
    """A minimal skill directory: SKILL.md plus the real agents."""
    dest.mkdir(parents=True)
    (dest / "SKILL.md").write_text("---\nname: adminwright\n---\n", encoding="utf-8")
    shutil.copytree(str(SKILL_ROOT / "agents"), str(dest / "agents"))
    return dest


TRICKY_DESCRIPTION = 'Owns the gate: run "validate" # not a comment; naïve \U0001F511 \\ slash'
TRICKY_BODY = "Body with ''' and \"\"\" and back\\slash. Run ${CLAUDE_PLUGIN_ROOT}/scripts/x.py\n"


def make_tricky_skill(dest):
    """A skill whose agents carry Claude-only frontmatter and hostile characters."""
    (dest / "agents").mkdir(parents=True)
    (dest / "SKILL.md").write_text("---\nname: adminwright\n---\n", encoding="utf-8")
    template = (
        '---\nname: NAME\n'
        'description: "Owns the gate: run \\"validate\\" # not a comment; naïve \U0001F511 \\\\ slash"\n'
        'tools: Read, Grep, Bash\nmodel: sonnet\n---\n\n' + TRICKY_BODY
    )
    for name in ia.AGENT_ORDER:
        with open(str(dest / "agents" / (name + ".md")), "w", encoding="utf-8", newline="\n") as handle:
            handle.write(template.replace("NAME", name))
    return dest


def make_runnable_skill(dest):
    """A skill directory an agent could actually run: SKILL.md, the real agents
    and the real manifest script."""
    make_skill(dest)
    (dest / "scripts").mkdir()
    shutil.copy2(str(SKILL_ROOT / "scripts" / "admin_console_manifest.py"),
                 str(dest / "scripts" / "admin_console_manifest.py"))
    return dest


MACHINE_FILE = (".adminwright", "skill-dir")


def machine_path(project):
    """The first line of the project's per-machine skill path file."""
    return project.joinpath(*MACHINE_FILE).read_text(encoding="utf-8").split("\n", 1)[0]


def committed_files(project):
    """Every file the installer left that a project would commit: all of them
    except the git-ignored per-machine path file."""
    ignored = project.joinpath(*MACHINE_FILE)
    return sorted(p for p in project.rglob("*") if p.is_file() and p != ignored)


def machine_needles(*paths):
    """Spellings of absolute paths that must never reach a committed file:
    each path resolved and as written, plus the home directory, compared
    lowercase with forward slashes."""
    needles = set()
    for path in paths:
        needles.add(Path(path).resolve().as_posix().lower())
        needles.add(os.path.abspath(str(path)).replace("\\", "/").lower())
    home = Path.home().resolve()
    if len(home.parts) > 2:  # a real home directory, not "/" or a drive root
        needles.add(home.as_posix().lower())
    return needles


def leaks(project, needles):
    """(file, needle) for every committed file that holds a machine path."""
    found = []
    for path in committed_files(project):
        text = path.read_text(encoding="utf-8", errors="replace").replace("\\", "/").lower()
        found += [(path.relative_to(project).as_posix(), n) for n in needles if n in text]
    return found


def git_ignores(project, relative):
    """True/False from `git check-ignore` in a fresh repository at `project`, or
    None when git is not installed. Global excludes are switched off so only the
    project's own .gitignore decides."""
    git = shutil.which("git")
    if git is None:
        return None
    base = [git, "-c", "core.excludesFile=", "-C", str(project)]
    if not (project / ".git").exists():
        subprocess.run(base + ["init", "-q"], check=True, capture_output=True)
    proc = subprocess.run(base + ["check-ignore", "-q", relative], capture_output=True)
    return proc.returncode == 0


# Where each harness's own documentation says custom agents live, written out
# here independently of ia.HARNESSES (checked 2026-09-24; see the URLs beside the
# HARNESSES entries): (directory, file pattern, frontmatter/TOML keys required).
NATIVE = {
    "claude-code": (".claude/agents", "{n}.md", ("name", "description")),
    "opencode": (".opencode/agents", "{n}.md", ("description", "mode")),
    "codex": (".codex/agents", "{n}.toml", ("name", "description", "developer_instructions")),
    "gemini": (".gemini/agents", "{n}.md", ("name", "description")),
    "cursor": (".cursor/agents", "{n}.md", ("name", "description")),
    "antigravity": (".agents/agents", "{n}/agent.md", ("name", "description")),
}


class InstallBase(unittest.TestCase):
    def setUp(self):
        self.project = Path(tempfile.mkdtemp(prefix="adminwright-install-"))
        self.addCleanup(shutil.rmtree, self.project, ignore_errors=True)

    def scratch(self):
        path = Path(tempfile.mkdtemp(prefix="adminwright-skill-"))
        self.addCleanup(shutil.rmtree, str(path), ignore_errors=True)
        return path


class TestInstall(InstallBase):
    def test_every_source_agent_exists(self):
        for name in ia.AGENT_ORDER:
            self.assertTrue((SKILL_ROOT / "agents" / (name + ".md")).exists(), name)

    def test_generic_install_names_the_skill_without_a_machine_path(self):
        # The skill (this repository) lives outside the project, so the copies
        # name the placeholder; the absolute path goes only to the per-machine
        # file. Before 6.18 was finished every copy carried the absolute path.
        code, out, _err = run("--harness", "generic", "--project-root", self.project)
        self.assertEqual(code, 0, out)
        target = self.project / ".adminwright" / "agents"
        files = sorted(p.name for p in target.glob("*.md"))
        self.assertEqual(len(files), len(ia.AGENT_ORDER))
        for path in target.glob("*.md"):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("${CLAUDE_PLUGIN_ROOT}", text, path.name)
            self.assertEqual(named_skill_dir(text), ia.PLACEHOLDER, path.name)
            self.assertNotIn(SKILL_ROOT.resolve().as_posix().lower(), text.lower(), path.name)
        self.assertEqual(machine_path(self.project), SKILL_ROOT.resolve().as_posix())

    def test_opencode_gets_subagent_mode(self):
        code, _out, _err = run("--harness", "opencode", "--project-root", self.project)
        self.assertEqual(code, 0)
        # opencode reads .opencode/agents (plural). The singular directory is
        # not a fallback — files there are simply never loaded — and globbing a
        # directory that does not exist made this assertion vacuous.
        target = self.project / ".opencode" / "agents"
        installed = sorted(target.glob("*.md"))
        self.assertEqual(len(installed), len(ia.AGENT_ORDER),
                         "agents must land in .opencode/agents, plural")
        for path in installed:
            frontmatter = path.read_text(encoding="utf-8").split("\n---", 1)[0]
            self.assertIn("mode: subagent", frontmatter, path.name)

    def test_cursor_writes_a_project_rule_not_dot_cursorrules(self):
        # .cursorrules is deprecated and silently ignored in Cursor's agent
        # mode: the agents install fine and nothing ever points at them.
        code, _out, _err = run("--harness", "cursor", "--project-root", self.project)
        self.assertEqual(code, 0)
        rule = self.project / ".cursor" / "rules" / "adminwright.mdc"
        self.assertTrue(rule.is_file(), "Cursor needs a .cursor/rules/*.mdc project rule")
        text = rule.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---"), "an .mdc rule needs frontmatter")
        self.assertIn("alwaysApply: true", text)
        self.assertIn(ia.MARKER_START, text)
        self.assertFalse((self.project / ".cursorrules").exists(),
                         "the deprecated .cursorrules must not be written")

    def test_cursor_rule_is_rewritten_not_duplicated(self):
        run("--harness", "cursor", "--project-root", self.project)
        run("--harness", "cursor", "--project-root", self.project)
        text = (self.project / ".cursor" / "rules" / "adminwright.mdc").read_text(encoding="utf-8")
        self.assertEqual(text.count(ia.MARKER_START), 1)
        self.assertEqual(text.count("---\ndescription:"), 1)

    def test_append_pointer_is_idempotent(self):
        run("--harness", "codex", "--project-root", self.project, "--append-pointer")
        code, out, _err = run("--harness", "codex", "--project-root", self.project,
                              "--append-pointer")
        self.assertEqual(code, 0)
        self.assertIn("already present", out)
        text = (self.project / "AGENTS.md").read_text(encoding="utf-8")
        self.assertEqual(text.count(ia.MARKER_START), 1)

    def test_append_pointer_preserves_existing_instructions(self):
        agents_md = self.project / "AGENTS.md"
        agents_md.write_text("# Existing rules\n\nDo not break these.\n", encoding="utf-8")
        code, _out, _err = run("--harness", "antigravity", "--project-root", self.project,
                               "--append-pointer")
        self.assertEqual(code, 0)
        text = agents_md.read_text(encoding="utf-8")
        self.assertIn("Do not break these.", text)
        self.assertIn(ia.MARKER_START, text)

    def test_antigravity_pointer_forbids_replanning(self):
        # Field-tested: Antigravity drafted a fresh implementation plan even when
        # the user supplied one. The pointer block must name the behavior.
        code, _out, _err = run("--harness", "antigravity", "--project-root", self.project,
                               "--append-pointer")
        self.assertEqual(code, 0)
        text = (self.project / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("Do not author a competing plan", text)
        self.assertIn("plan of record", text)

    def test_every_pointer_carries_plan_and_report_rules(self):
        for block in (ia.pointer_block(".adminwright/agents", "vendor/adminwright"),
                      ia.pointer_block(".adminwright/agents", None, ("codex", "pi"))):
            self.assertIn("plan of record", block)
            self.assertIn("completion report", block)

    def test_missing_project_root_fails_with_exit_2(self):
        code, _out, err = run("--harness", "generic", "--project-root",
                              self.project / "does-not-exist")
        self.assertEqual(code, 2)
        self.assertIn("project root", err)

    def test_bad_skill_dir_fails_with_exit_2(self):
        code, _out, err = run("--harness", "generic", "--project-root", self.project,
                              "--skill-dir", self.project)
        self.assertEqual(code, 2)
        self.assertIn("SKILL.md", err)


class TestNativeFiles(InstallBase):
    """Each harness gets files in the location and shape it actually reads (6.17)."""

    def install(self, harness, skill=None, extra=()):
        args = ["--harness", harness, "--project-root", self.project]
        if skill is not None:
            args += ["--skill-dir", skill]
        code, out, err = run(*(args + list(extra)))
        self.assertEqual(code, 0, out + err)
        return out

    def test_each_installed_file_has_the_fields_its_harness_requires(self):
        for harness, (directory, pattern, required) in NATIVE.items():
            with self.subTest(harness=harness):
                project = Path(tempfile.mkdtemp(prefix="adminwright-native-"))
                self.addCleanup(shutil.rmtree, str(project), ignore_errors=True)
                code, out, err = run("--harness", harness, "--project-root", project)
                self.assertEqual(code, 0, out + err)
                for name in ia.AGENT_ORDER:
                    path = project / directory / pattern.format(n=name)
                    self.assertTrue(path.is_file(), str(path))
                    text = path.read_text(encoding="utf-8")
                    if path.suffix == ".toml":
                        for key in required:
                            if key == "developer_instructions":
                                pattern_ = r"^developer_instructions = (?:'''|\"\"\")\n\S"
                            else:
                                pattern_ = r'^' + key + r' = "[^"]+"$'
                            self.assertRegex(text, re.compile(pattern_, re.MULTILINE),
                                             name + ": TOML key " + key)
                        self.assertIn('name = "' + name + '"', text)
                    else:
                        fields = frontmatter_of(text)
                        for key in required:
                            self.assertTrue(fields.get(key), name + ": frontmatter " + key)
                        if "name" in fields:
                            self.assertEqual(fields["name"], name)
                        if harness in ("gemini", "antigravity"):
                            # both harnesses reject or mishandle anything but a slug
                            self.assertRegex(fields["name"], r"^[a-z0-9][a-z0-9_-]*$")
                        if harness == "antigravity":
                            self.assertEqual(path.parent.name, name)
                    self.assertNotIn("${CLAUDE_PLUGIN_ROOT}", text, name)
                    self.assertIn("developer_instructions" if path.suffix == ".toml" else "You are",
                                  text, name)

    def test_opencode_frontmatter_is_only_what_opencode_understands(self):
        # opencode uses the file name as the agent name and forwards unknown
        # frontmatter keys to the model provider as options; `model` there must
        # be provider/model-id and `tools` is deprecated. A Claude-style
        # `name`, `tools` or `model` must never be carried over.
        skill = make_tricky_skill(self.scratch())
        self.install("opencode", skill)
        for name in ia.AGENT_ORDER:
            fields = frontmatter_of((self.project / ".opencode" / "agents" / (name + ".md"))
                                    .read_text(encoding="utf-8"))
            self.assertEqual(sorted(fields), ["description", "mode"], name)
            self.assertEqual(fields["mode"], "subagent")

    def test_claude_only_frontmatter_does_not_leak_and_text_round_trips(self):
        skill = make_tricky_skill(self.scratch())
        # the skill is outside the project, so the copies name the placeholder
        expected_body = TRICKY_BODY.replace(ia.TOKEN, ia.PLACEHOLDER)
        for harness in ("opencode", "gemini", "cursor", "antigravity"):
            with self.subTest(harness=harness):
                project = Path(tempfile.mkdtemp(prefix="adminwright-leak-"))
                self.addCleanup(shutil.rmtree, str(project), ignore_errors=True)
                code, out, err = run("--harness", harness, "--project-root", project,
                                     "--skill-dir", skill)
                self.assertEqual(code, 0, out + err)
                directory, pattern, _required = NATIVE[harness]
                path = project / directory / pattern.format(n="adminwright-qa")
                text = path.read_text(encoding="utf-8")
                fields = frontmatter_of(text)
                self.assertNotIn("tools", fields)
                self.assertNotIn("model", fields)
                self.assertEqual(json.loads(fields["description"]), TRICKY_DESCRIPTION)
                self.assertEqual(text.split("\n---\n\n", 1)[1], expected_body)

    def test_claude_code_and_neutral_copies_keep_the_source_verbatim(self):
        skill = make_tricky_skill(self.scratch())
        self.install("claude-code", skill)
        text = (self.project / ".claude" / "agents" / "adminwright-qa.md").read_text(encoding="utf-8")
        self.assertIn("tools: Read, Grep, Bash", text)
        self.assertIn("model: sonnet", text)
        self.assertNotIn(ia.TOKEN, text)

    @unittest.skipIf(tomllib is None, "tomllib needs Python 3.11+")
    def test_codex_toml_parses_and_round_trips(self):
        skill = make_tricky_skill(self.scratch())
        self.install("codex", skill)
        expected_body = TRICKY_BODY.replace(ia.TOKEN, ia.PLACEHOLDER)
        for name in ia.AGENT_ORDER:
            data = tomllib.loads((self.project / ".codex" / "agents" / (name + ".toml"))
                                 .read_text(encoding="utf-8"))
            self.assertEqual(sorted(data), ["description", "developer_instructions", "name"])
            self.assertEqual(data["name"], name)
            self.assertEqual(data["description"], TRICKY_DESCRIPTION)
            self.assertEqual(data["developer_instructions"], expected_body)

    @unittest.skipIf(tomllib is None, "tomllib needs Python 3.11+")
    def test_codex_toml_parses_for_the_real_agents(self):
        self.install("codex")
        for name in ia.AGENT_ORDER:
            data = tomllib.loads((self.project / ".codex" / "agents" / (name + ".toml"))
                                 .read_text(encoding="utf-8"))
            self.assertTrue(data["description"], name)
            self.assertTrue(data["developer_instructions"].startswith("You are"), name)

    def test_harnesses_with_a_native_dir_also_get_the_neutral_copy(self):
        for harness in ("codex", "antigravity", "gemini", "cursor"):
            with self.subTest(harness=harness):
                project = Path(tempfile.mkdtemp(prefix="adminwright-neutral-"))
                self.addCleanup(shutil.rmtree, str(project), ignore_errors=True)
                code, out, err = run("--harness", harness, "--project-root", project)
                self.assertEqual(code, 0, out + err)
                neutral = sorted(p.name for p in (project / ".adminwright" / "agents").glob("*.md"))
                self.assertEqual(neutral, sorted(n + ".md" for n in ia.AGENT_ORDER))

    def test_claude_code_and_opencode_write_no_neutral_copy(self):
        for harness in ("claude-code", "opencode"):
            with self.subTest(harness=harness):
                project = Path(tempfile.mkdtemp(prefix="adminwright-noneutral-"))
                self.addCleanup(shutil.rmtree, str(project), ignore_errors=True)
                code, out, err = run("--harness", harness, "--project-root", project)
                self.assertEqual(code, 0, out + err)
                self.assertFalse((project / ".adminwright" / "agents").exists())
                # the skill (this repository) is outside the project, so the one
                # thing under .adminwright is this machine's skill path
                self.assertEqual(sorted(p.name for p in (project / ".adminwright").iterdir()),
                                 ["skill-dir"])

    def test_pi_and_generic_have_no_native_location_and_invent_none(self):
        # Pi documents no agents concept ("build your own with extensions"), so
        # only the neutral copy and the AGENTS.md pointer exist for it.
        for harness in ("pi", "generic"):
            with self.subTest(harness=harness):
                self.assertIsNone(ia.HARNESSES[harness].native_dir)
                project = Path(tempfile.mkdtemp(prefix="adminwright-nonative-"))
                self.addCleanup(shutil.rmtree, str(project), ignore_errors=True)
                code, out, err = run("--harness", harness, "--project-root", project,
                                     "--append-pointer")
                self.assertEqual(code, 0, out + err)
                top = sorted(p.name for p in project.iterdir())
                # .gitignore ignores the per-machine skill path (the skill is
                # outside the project); nothing else beyond the neutral copy
                self.assertEqual(top, [".adminwright", ".gitignore"]
                                 + (["AGENTS.md"] if harness == "pi" else []))

    def test_cursor_gets_agents_and_the_rule(self):
        self.install("cursor")
        self.assertEqual(len(list((self.project / ".cursor" / "agents").glob("*.md"))),
                         len(ia.AGENT_ORDER))
        fields = frontmatter_of((self.project / ".cursor" / "rules" / "adminwright.mdc")
                                .read_text(encoding="utf-8"))
        self.assertEqual(fields["alwaysApply"], "true")
        self.assertTrue(fields["description"])

    def test_every_harness_cites_the_docs_it_was_verified_against(self):
        for name, spec in ia.HARNESSES.items():
            with self.subTest(harness=name):
                self.assertEqual(spec.checked, "2026-09-24")
                if name != "generic":
                    self.assertTrue(spec.docs, "no documentation URL recorded")
                for url in spec.docs:
                    self.assertTrue(url.startswith("https://"), url)
                # the table and the hand-written expectations must not drift apart
                if name in NATIVE:
                    self.assertEqual(spec.native_dir, NATIVE[name][0])

    def test_docstring_names_every_native_directory(self):
        doc = ia.__doc__
        for spec in ia.HARNESSES.values():
            if spec.native_dir:
                self.assertIn(spec.native_dir + "/", doc)
        self.assertIn(".cursor/rules/adminwright.mdc", doc)
        self.assertNotIn(".opencode/agent/", doc, "opencode reads .opencode/agents, plural")

    def test_frontmatter_scalar_forms_all_decode(self):
        # The source agents may be plain (today), quoted (once the unquoted-colon
        # fix lands) or block scalars, and may carry list-valued keys.
        block = "\n".join([
            "name: adminwright-qa",
            "description: Owns the gate: validate and coverage # kept, not a comment",
            "tools:",
            "  - Read",
            "  - Bash",
            "other: 'it''s single quoted'",
            "folded: >-",
            "  first line",
            "  second line",
            "literal: |",
            "  keep",
            "  lines",
            'wrapped: "one',
            '  two"',
        ])
        fields = ia.frontmatter_fields(block)
        self.assertEqual(fields["description"],
                         "Owns the gate: validate and coverage # kept, not a comment")
        self.assertEqual(fields["other"], "it's single quoted")
        self.assertEqual(fields["folded"], "first line second line")
        self.assertEqual(fields["literal"], "keep\nlines")
        self.assertEqual(fields["wrapped"], "one two")
        self.assertIn("tools", fields)

    def test_source_frontmatter_problems_are_reported_not_tracebacks(self):
        skill = make_tricky_skill(self.scratch())
        bad = skill / "agents" / "adminwright-qa.md"
        bad.write_text("no frontmatter here\n", encoding="utf-8")
        code, _out, err = run("--harness", "gemini", "--project-root", self.project,
                              "--skill-dir", skill)
        self.assertEqual(code, 2)
        self.assertIn("adminwright-qa.md", err)
        self.assertNotIn("Traceback", err)

    def test_gemini_subagent_bodies_hold_no_template_placeholders(self):
        # Gemini CLI runs a subagent's system prompt through templateString, which
        # throws on any ${name} it has no input for; the subagent then fails at
        # dispatch, long after the install reported success.
        inside = make_skill(self.project / ".agents" / "skills" / "adminwright")
        for skill in (None, inside):
            with self.subTest(skill="outside" if skill is None else "inside"):
                self.install("gemini", skill)
                for path in (self.project / ".gemini" / "agents").glob("*.md"):
                    body = path.read_text(encoding="utf-8").split("\n---\n", 1)[1]
                    self.assertEqual(ia.GEMINI_TEMPLATE.findall(body), [], path.name)

    def test_a_body_gemini_would_fail_to_template_is_refused_for_gemini_only(self):
        skill = make_tricky_skill(self.scratch())
        qa = skill / "agents" / "adminwright-qa.md"
        with open(str(qa), "a", encoding="utf-8", newline="\n") as handle:
            handle.write("Keep state in ${ADMINWRIGHT_HOME}.\n")
        code, _out, err = run("--harness", "gemini", "--project-root", self.project,
                              "--skill-dir", skill)
        self.assertEqual(code, 2)
        self.assertIn("adminwright-qa.md", err)
        self.assertIn("${ADMINWRIGHT_HOME}", err)
        self.assertNotIn("Traceback", err)
        self.assertFalse((self.project / ".gemini").exists(), "nothing is written on refusal")
        self.install("codex", skill)  # no template step there


class TestPointerPaths(InstallBase):
    """The pointer never bakes a machine path into a committed file (6.18)."""

    def pointer(self, harness="codex", skill=None, extra=("--append-pointer",)):
        args = ["--harness", harness, "--project-root", self.project]
        if skill is not None:
            args += ["--skill-dir", skill]
        code, out, err = run(*(args + list(extra)))
        self.assertEqual(code, 0, out + err)
        return out

    def absolutes(self, path):
        return {path.resolve().as_posix().lower(), str(path).lower().replace("\\", "/")}

    def test_pointer_is_project_relative_when_the_skill_is_inside_the_project(self):
        skill = make_skill(self.project / ".claude" / "skills" / "adminwright")
        self.pointer(skill=skill)
        text = (self.project / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("`.claude/skills/adminwright`", text)
        self.assertNotIn(ia.TOKEN, text)
        markers_removed = re.sub(r"<!-- adminwright-harness: [a-z0-9_-]+ -->", "",
                                 text.replace(ia.MARKER_START, "").replace(ia.MARKER_END, ""))
        self.assertNotIn("<!--", markers_removed,
                         "no absolute-path comment is needed when the path is relative")
        for absolute in self.absolutes(self.project):
            self.assertNotIn(absolute, text.lower().replace("\\", "/"))

    def test_agent_copies_are_project_relative_when_the_skill_is_inside(self):
        skill = make_skill(self.project / "vendor" / "adminwright")
        self.pointer(skill=skill)
        for directory in (".adminwright/agents", ".codex/agents"):
            for path in (self.project / directory).iterdir():
                raw = path.read_text(encoding="utf-8")
                self.assertEqual(named_skill_dir(raw), "vendor/adminwright", path.name)
                self.assertNotIn(ia.TOKEN, raw, path.name)
                text = raw.replace("\\", "/").lower()
                for absolute in self.absolutes(self.project):
                    self.assertNotIn(absolute, text, path.name)

    def test_no_absolute_path_reaches_the_pointer_when_the_skill_is_outside(self):
        # AGENTS.md is committed; the machine path used to sit in an HTML comment
        # there. The block now sends the reader to the git-ignored file instead.
        self.pointer(skill=SKILL_ROOT)
        text = (self.project / "AGENTS.md").read_text(encoding="utf-8")
        flat = text.replace("\\", "/").lower()
        for needle in machine_needles(SKILL_ROOT):
            self.assertNotIn(needle, flat)
        self.assertIn("`.adminwright/skill-dir`", text)
        self.assertNotIn(ia.TOKEN, text)
        self.assertEqual(machine_path(self.project), SKILL_ROOT.resolve().as_posix())

    def test_the_project_root_is_never_written_into_the_pointer(self):
        self.pointer(skill=SKILL_ROOT)
        text = (self.project / "AGENTS.md").read_text(encoding="utf-8").lower().replace("\\", "/")
        for absolute in self.absolutes(self.project):
            self.assertNotIn(absolute, text)

    def test_a_hostile_skill_path_never_reaches_a_committed_file(self):
        # The path used to be escaped into an HTML comment; now it is not written
        # at all, whatever characters it holds. Only the per-machine file has it.
        skill = make_skill(self.scratch() / "aw --`$x` dir")  # `<` `>` are invalid on Windows
        self.pointer(skill=skill)
        for path in committed_files(self.project):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("--`$x`", text, str(path))
        self.assertEqual(machine_path(self.project), skill.resolve().as_posix())

    def test_reinstall_after_the_skill_moved_rewrites_the_pointer(self):
        agents_md = self.project / "AGENTS.md"
        agents_md.write_text("# Team rules\n\nBe kind.\n", encoding="utf-8")
        first = make_skill(self.project / "tools" / "aw-a")
        self.pointer(skill=first)
        with open(str(agents_md), "a", encoding="utf-8", newline="\n") as handle:
            handle.write("\n## Footer\n\nkeep me\n")
        (self.project / "vendor").mkdir()
        second = self.project / "vendor" / "aw-b"
        shutil.move(str(first), str(second))
        out = self.pointer(skill=second)
        self.assertIn("Updated pointer block", out)
        text = agents_md.read_text(encoding="utf-8")
        self.assertIn("`vendor/aw-b`", text)
        self.assertNotIn("tools/aw-a", text)
        self.assertEqual(text.count(ia.MARKER_START), 1)
        self.assertEqual(text.count(ia.MARKER_END), 1)
        self.assertIn("# Team rules\n\nBe kind.\n", text)
        self.assertTrue(text.endswith("## Footer\n\nkeep me\n"))
        copy = (self.project / ".adminwright" / "agents" / "adminwright-qa.md").read_text(encoding="utf-8")
        self.assertIn("vendor/aw-b", copy)
        self.assertNotIn("tools/aw-a", copy)

    def test_reinstall_from_inside_to_outside_swaps_relative_for_the_machine_file(self):
        inside = make_skill(self.project / "tools" / "aw-a")
        self.pointer(skill=inside)
        self.pointer(skill=SKILL_ROOT)
        text = (self.project / "AGENTS.md").read_text(encoding="utf-8")
        self.assertNotIn("tools/aw-a", text)
        self.assertIn("`.adminwright/skill-dir`", text)
        self.assertEqual(text.count(ia.MARKER_START), 1)
        for path in (self.project / ".codex" / "agents").iterdir():
            copy = path.read_text(encoding="utf-8")
            self.assertNotIn("tools/aw-a", copy, path.name)
            self.assertEqual(named_skill_dir(copy), ia.PLACEHOLDER, path.name)

    def test_an_old_marked_block_with_absolute_paths_is_replaced_in_place(self):
        # What the installer wrote before 6.18 was finished: the machine path in an
        # HTML comment inside the block.
        old = "\n".join([
            ia.MARKER_START,
            "## Adminwright agent passes",
            "",
            "<!-- adminwright skill directory on the machine that ran the installer (not "
            "portable): /old/machine/adminwright -->",
            "",
            "This project builds or audits its admin console under the adminwright skill,",
            "whose directory is `${CLAUDE_PLUGIN_ROOT}` (start from its `SKILL.md`). The role",
            "prompts in `/old/project/.adminwright/agents/` define six passes.",
            "This harness also discovers the same prompts natively in `.codex/agents/`.",
            "",
            "Codex note: run the passes sequentially in one session, adopting one role file at",
            "a time.",
            ia.MARKER_END,
        ]) + "\n"
        agents_md = self.project / "AGENTS.md"
        agents_md.write_text("# Rules\n\nAbove.\n\n" + old + "\n## Tail\n\nBelow.\n", encoding="utf-8")
        out = self.pointer(skill=SKILL_ROOT)
        self.assertIn("Updated pointer block", out)
        text = agents_md.read_text(encoding="utf-8")
        self.assertNotIn("/old/machine", text)
        self.assertNotIn("/old/project", text)
        self.assertEqual(text.count(ia.MARKER_START), 1)
        self.assertTrue(text.startswith("# Rules\n\nAbove.\n\n" + ia.MARKER_START))
        self.assertTrue(text.endswith("\n## Tail\n\nBelow.\n"))

    def test_an_unmarked_old_pointer_section_is_migrated_not_duplicated(self):
        agents_md = self.project / "AGENTS.md"
        agents_md.write_text(
            "# Rules\n\nAbove.\n\n## Adminwright agent passes\n\nOld text at `/old/skill`.\n\n"
            "More old text.\n\n## Other section\n\nKeep this.\n", encoding="utf-8")
        out = self.pointer(skill=SKILL_ROOT)
        self.assertIn("older unmarked", out)
        text = agents_md.read_text(encoding="utf-8")
        self.assertNotIn("/old/skill", text)
        self.assertNotIn("More old text.", text)
        self.assertEqual(text.count("## Adminwright agent passes"), 1)
        self.assertEqual(text.count(ia.MARKER_START), 1)
        self.assertIn(ia.MARKER_END + "\n\n## Other section\n\nKeep this.\n", text)
        self.assertTrue(text.startswith("# Rules\n\nAbove.\n\n" + ia.MARKER_START))
        # and a second run is now a no-op
        out = self.pointer(skill=SKILL_ROOT)
        self.assertIn("already present", out)

    def test_unbalanced_markers_are_refused_and_the_file_is_untouched(self):
        agents_md = self.project / "AGENTS.md"
        original = "top\n" + ia.MARKER_START + "\nno end marker\n"
        agents_md.write_text(original, encoding="utf-8")
        code, _out, err = run("--harness", "codex", "--project-root", self.project,
                              "--append-pointer")
        self.assertEqual(code, 2)
        self.assertIn("unbalanced", err)
        self.assertNotIn("Traceback", err)
        self.assertEqual(agents_md.read_text(encoding="utf-8"), original)

    def test_without_the_flag_an_existing_file_is_never_touched(self):
        agents_md = self.project / "AGENTS.md"
        first = make_skill(self.project / "tools" / "aw-a")
        self.pointer(skill=first)
        before = agents_md.read_text(encoding="utf-8")
        second = self.project / "vendor" / "aw-b"
        second.parent.mkdir()
        shutil.move(str(first), str(second))
        out = self.pointer(skill=second, extra=())
        self.assertEqual(agents_md.read_text(encoding="utf-8"), before)
        self.assertIn("out-of-date", out)
        self.assertIn("--append-pointer", out)

    def test_a_crlf_instruction_file_stays_crlf(self):
        agents_md = self.project / "AGENTS.md"
        agents_md.write_bytes(b"# Rules\r\n\r\nWindows editor wrote this.\r\n")
        self.pointer(skill=SKILL_ROOT)
        data = agents_md.read_bytes()
        self.assertEqual(data.count(b"\n"), data.count(b"\r\n"), "mixed line endings")
        self.assertIn(ia.MARKER_START.encode("utf-8"), data)
        self.pointer(skill=SKILL_ROOT)
        self.assertEqual(agents_md.read_bytes(), data)

    def test_a_non_utf8_instruction_file_is_refused_not_mangled(self):
        agents_md = self.project / "AGENTS.md"
        agents_md.write_bytes(b"caf\xe9 rules\n")
        code, _out, err = run("--harness", "codex", "--project-root", self.project,
                              "--append-pointer")
        self.assertEqual(code, 2)
        self.assertIn("UTF-8", err)
        self.assertNotIn("Traceback", err)
        self.assertEqual(agents_md.read_bytes(), b"caf\xe9 rules\n")


def harness_sections(text):
    """{harness: section text} for every section inside the one pointer block."""
    block = text[text.index(ia.MARKER_START):text.index(ia.MARKER_END)]
    parts = re.split(r"<!-- adminwright-harness: ([a-z0-9_-]+) -->\n", block)
    return dict(zip(parts[1::2], parts[2::2]))


# The field-tested line each harness's note must keep, wherever the block moves.
NOTE_PROOF = {
    "antigravity": "Do not author a competing plan",
    "codex": "custom agent of the same name",
    "pi": "Pi note: run the passes sequentially",
}


class TestSharedInstructionFiles(InstallBase):
    """Codex, Antigravity and Pi all read AGENTS.md: one block, a section each,
    and no install order loses a section (ocr-installer#9)."""

    def install(self, harness, project, extra=("--append-pointer",)):
        code, out, err = run(*(["--harness", harness, "--project-root", project] + list(extra)))
        self.assertEqual(code, 0, out + err)
        return out

    def assert_sections(self, text, harnesses):
        self.assertEqual(text.count(ia.MARKER_START), 1)
        self.assertEqual(text.count(ia.MARKER_END), 1)
        sections = harness_sections(text)
        self.assertEqual(sorted(sections), sorted(harnesses))
        for name in harnesses:
            self.assertEqual(text.count(NOTE_PROOF[name]), 1, name)
            self.assertIn(NOTE_PROOF[name], sections[name])

    def test_every_install_order_keeps_every_section(self):
        orders = (
            ("codex", "antigravity", "codex"),          # the reported loss
            ("antigravity", "codex", "antigravity"),
            ("pi", "codex", "antigravity", "pi"),
            ("antigravity", "pi", "codex"),
            ("codex", "pi"),
        )
        finals = {}
        for order in orders:
            with self.subTest(order=order):
                project = Path(tempfile.mkdtemp(prefix="adminwright-shared-"))
                self.addCleanup(shutil.rmtree, str(project), ignore_errors=True)
                (project / "AGENTS.md").write_bytes(b"# Team rules\n\nBe kind.\n")
                for harness in order:
                    self.install(harness, project)
                text = (project / "AGENTS.md").read_text(encoding="utf-8")
                self.assertTrue(text.startswith("# Team rules\n\nBe kind.\n"))
                self.assert_sections(text, set(order))
                finals.setdefault(frozenset(order), set()).add(text)
                for harness in set(order):  # a rerun of any of them changes nothing
                    out = self.install(harness, project)
                    self.assertIn("already present", out, harness)
                    self.assertEqual((project / "AGENTS.md").read_text(encoding="utf-8"), text)
        for harnesses, texts in finals.items():
            self.assertEqual(len(texts), 1, "install order changed the block for "
                             + ", ".join(sorted(harnesses)))

    def test_a_block_written_before_section_marks_keeps_its_harness(self):
        # A d6c64a8-era Antigravity block has no section marks; the next Codex
        # install used to replace it wholesale and drop the Antigravity note.
        legacy = "\n".join([
            ia.MARKER_START,
            "## Adminwright agent passes",
            "",
            "The role prompts in `.adminwright/agents/` define six passes.",
            "This harness also discovers the same prompts natively in `.agents/agents/`.",
            "",
        ] + ia.HARNESS_NOTES["antigravity"] + [ia.MARKER_END]) + "\n"
        agents_md = self.project / "AGENTS.md"
        agents_md.write_bytes(("# Rules\n\n" + legacy).encode("utf-8"))
        out = self.install("codex", self.project)
        self.assertIn("Updated pointer block", out)
        text = agents_md.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("# Rules\n\n" + ia.MARKER_START))
        self.assert_sections(text, {"antigravity", "codex"})
        self.assertNotIn("This harness also discovers", text)

    def test_an_unmarked_section_keeps_its_harness_too(self):
        agents_md = self.project / "AGENTS.md"
        agents_md.write_bytes(
            b"## Adminwright agent passes\n\nOld text.\n\nPi note: old wording.\n\n## Next\n\nKeep.\n")
        out = self.install("antigravity", self.project)
        self.assertIn("older unmarked", out)
        text = agents_md.read_text(encoding="utf-8")
        self.assert_sections(text, {"antigravity", "pi"})
        self.assertNotIn("old wording", text)
        self.assertTrue(text.endswith(ia.MARKER_END + "\n\n## Next\n\nKeep.\n"))

    def test_without_the_flag_the_printed_block_keeps_the_other_sections(self):
        self.install("antigravity", self.project)
        before = (self.project / "AGENTS.md").read_bytes()
        out = self.install("codex", self.project, extra=())
        self.assertEqual((self.project / "AGENTS.md").read_bytes(), before)
        self.assertIn("out-of-date", out)
        self.assertIn(NOTE_PROOF["antigravity"], out)
        self.assertIn(NOTE_PROOF["codex"], out)

    def test_the_codex_section_dispatches_its_native_custom_agents(self):
        # Codex spawns the .codex/agents custom agents when AGENTS.md asks; the
        # old note ("run the passes sequentially in one session") contradicted
        # the block's own "discovers the same prompts natively" line.
        self.install("codex", self.project)
        codex = harness_sections((self.project / "AGENTS.md").read_text(encoding="utf-8"))["codex"]
        self.assertIn("`.codex/agents/`", codex)
        self.assertIn("custom agent of the same name", codex)
        self.assertNotIn("run the passes sequentially in one session", codex)
        self.assertIn("enabled = false", codex, "the sequential fallback names its condition")

    def test_each_native_harness_section_names_its_directory(self):
        for harness in ("codex", "antigravity", "gemini", "cursor"):
            with self.subTest(harness=harness):
                project = Path(tempfile.mkdtemp(prefix="adminwright-section-"))
                self.addCleanup(shutil.rmtree, str(project), ignore_errors=True)
                self.install(harness, project)
                spec = ia.HARNESSES[harness]
                text = (project / spec.instruction_file).read_text(encoding="utf-8")
                sections = harness_sections(text)
                self.assertEqual(sorted(sections), [harness])
                self.assertIn("`" + spec.native_dir + "/`", sections[harness])
                self.assertNotIn("run the passes sequentially, one role file at a time, and",
                                 sections[harness], "native harnesses dispatch subagents first")

    def test_a_refused_pointer_writes_nothing(self):
        # Unbalanced markers are refused; the refusal must come before any agent
        # file is written, not after.
        agents_md = self.project / "AGENTS.md"
        agents_md.write_bytes(b"top\n" + ia.MARKER_START.encode("utf-8") + b"\n")
        code, _out, err = run("--harness", "codex", "--project-root", self.project,
                              "--append-pointer")
        self.assertEqual(code, 2)
        self.assertIn("unbalanced", err)
        self.assertEqual(sorted(p.name for p in self.project.iterdir()), ["AGENTS.md"])


def locate_section(text):
    """The "Locate the skill" section of one installed agent file (Markdown, or
    the developer_instructions of a Codex TOML file)."""
    start = text.index("## Locate the skill")
    end = text.find("\n## ", start + 1)
    return text[start:end if end != -1 else len(text)]


def named_skill_dir(text):
    """The skill directory an installed agent's "Locate the skill" section names
    (what the installer wrote where the source says ${CLAUDE_PLUGIN_ROOT})."""
    match = re.search(r"`([^`\n]+)` is the adminwright skill directory", locate_section(text))
    assert match, "the Locate the skill section names no directory"
    return match.group(1)


def installed_agent_files(project, harness):
    """Every agent prompt the install wrote for `harness`: its native files and
    the neutral copies."""
    files = []
    spec = ia.HARNESSES[harness]
    if spec.native_dir:
        files += [p for p in (project / spec.native_dir).rglob("*") if p.is_file()]
    if spec.neutral_copy:
        files += sorted((project / ".adminwright" / "agents").glob("*.md"))
    return files


class TestMachinePaths(InstallBase):
    """No file a project commits holds a machine's path; the path lives in the
    git-ignored .adminwright/skill-dir instead, and the agents find it (6.18)."""

    def install(self, harness, skill, project=None, pointer=True):
        args = ["--harness", harness, "--project-root", project or self.project,
                "--skill-dir", skill]
        code, out, err = run(*(args + (["--append-pointer"] if pointer else [])))
        self.assertEqual(code, 0, out + err)
        return out, err

    def test_no_committed_file_holds_a_machine_path_for_any_harness(self):
        # Before the fix every native and neutral copy carried the absolute
        # skill path 6-12 times, in files teams commit (.claude/agents etc.).
        skill = make_skill(self.scratch() / "adminwright")
        for harness in sorted(ia.HARNESSES):
            with self.subTest(harness=harness):
                project = Path(tempfile.mkdtemp(prefix="adminwright-commit-"))
                self.addCleanup(shutil.rmtree, str(project), ignore_errors=True)
                self.install(harness, skill, project)
                self.assertTrue(committed_files(project))
                self.assertEqual(leaks(project, machine_needles(skill, project)), [])
                for path in installed_agent_files(project, harness):
                    text = path.read_text(encoding="utf-8")
                    self.assertEqual(named_skill_dir(text), ia.PLACEHOLDER, str(path))
                    self.assertNotIn(ia.TOKEN, text, str(path))

    def test_the_machine_path_file_is_written_and_git_ignored(self):
        skill = make_skill(self.scratch() / "adminwright")
        for harness in sorted(ia.HARNESSES):
            with self.subTest(harness=harness):
                project = Path(tempfile.mkdtemp(prefix="adminwright-ignored-"))
                self.addCleanup(shutil.rmtree, str(project), ignore_errors=True)
                out, _err = self.install(harness, skill, project)
                self.assertEqual(machine_path(project), skill.resolve().as_posix())
                gitignore = (project / ".gitignore").read_text(encoding="utf-8")
                self.assertIn("\n" + ia.GITIGNORE_ENTRY + "\n", "\n" + gitignore)
                self.assertIn(".adminwright/skill-dir", out, "the install must say where the path went")
                self.assertIn(".gitignore", out)
                ignored = git_ignores(project, ".adminwright/skill-dir")
                if ignored is not None:
                    self.assertTrue(ignored, "git must ignore the per-machine path file")
                    self.assertFalse(git_ignores(project, ".adminwright/agents/adminwright-qa.md"),
                                     "only the per-machine file is ignored, not the agents")

    def test_the_installed_locate_section_resolves_through_the_machine_file(self):
        # Every copy names the placeholder, which never exists as a path, so an
        # agent following its "Locate the skill" section must reach step 1: the
        # per-machine file. That file must name a skill whose script runs.
        skill = make_runnable_skill(self.scratch() / "adminwright")
        for harness in sorted(ia.HARNESSES):
            with self.subTest(harness=harness):
                project = Path(tempfile.mkdtemp(prefix="adminwright-locate-"))
                self.addCleanup(shutil.rmtree, str(project), ignore_errors=True)
                self.install(harness, skill, project)
                files = installed_agent_files(project, harness)
                self.assertEqual(len(files) % len(ia.AGENT_ORDER), 0, files)
                for path in files:
                    text = path.read_text(encoding="utf-8")
                    section = locate_section(text)
                    named = named_skill_dir(text)
                    self.assertEqual(named, ia.PLACEHOLDER, str(path))
                    self.assertFalse((project / named / "scripts").exists())
                    order = [section.find(".adminwright/skill-dir"),
                             section.find("dispatch prompt"),
                             section.find(".claude/skills/adminwright"),
                             section.find("~/.claude/plugins/marketplaces/tarikai"),
                             section.find("search for `admin_console_manifest.py`")]
                    self.assertNotIn(-1, order, str(path) + ": a lookup step is missing")
                    self.assertEqual(order, sorted(order),
                                     str(path) + ": the per-machine file must come first")
                    # Claude Code deletes a versioned cache directory ~14 days after
                    # an update; the lookup must not send an agent there.
                    self.assertNotIn("plugins/cache", section, str(path))
                    self.assertIn("STATUS: BLOCKED", section, str(path))
                resolved = Path(machine_path(project))
                script = resolved / "scripts" / "admin_console_manifest.py"
                self.assertTrue(script.is_file())
                proc = subprocess.run([sys.executable, str(script), "--version"],
                                      capture_output=True, text=True)
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_moving_the_skill_outside_the_project_rewrites_only_the_machine_file(self):
        first = make_skill(self.scratch() / "aw-a")
        for harness in sorted(ia.HARNESSES):
            self.install(harness, first)
        before = {p: p.read_bytes() for p in committed_files(self.project)}
        second = self.scratch() / "aw-b"
        shutil.move(str(first), str(second))
        for harness in sorted(ia.HARNESSES):
            self.install(harness, second)
        after = {p: p.read_bytes() for p in committed_files(self.project)}
        self.assertEqual(after, before, "committed files must not change when the skill moves")
        self.assertEqual(machine_path(self.project), second.resolve().as_posix())
        self.assertEqual(leaks(self.project, machine_needles(first, second, self.project)), [])

    def test_moving_the_skill_inside_the_project_rewrites_every_copy(self):
        first = make_skill(self.project / "tools" / "aw-a")
        for harness in sorted(ia.HARNESSES):
            self.install(harness, first)
        (self.project / "vendor").mkdir()
        second = self.project / "vendor" / "aw-b"
        shutil.move(str(first), str(second))
        for harness in sorted(ia.HARNESSES):
            self.install(harness, second)
        for path in committed_files(self.project):
            if "vendor" in path.relative_to(self.project).parts:
                continue  # the skill itself
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("tools/aw-a", text, str(path))
        for harness in sorted(ia.HARNESSES):
            for path in installed_agent_files(self.project, harness):
                self.assertEqual(named_skill_dir(path.read_text(encoding="utf-8")),
                                 "vendor/aw-b", str(path))
        self.assertFalse(self.project.joinpath(*MACHINE_FILE).exists(),
                         "a skill inside the project needs no per-machine file")
        self.assertFalse((self.project / ".gitignore").exists())

    def test_an_inside_install_refreshes_a_machine_file_an_outside_one_left(self):
        outside = make_skill(self.scratch() / "adminwright")
        self.install("codex", outside)
        inside = make_skill(self.project / "vendor" / "adminwright")
        self.install("codex", inside)
        self.assertEqual(machine_path(self.project), inside.resolve().as_posix(),
                         "the per-machine file must never point at a stale location")

    def test_an_existing_gitignore_gains_the_entry_once_and_keeps_its_rules(self):
        skill = make_skill(self.scratch() / "adminwright")
        gitignore = self.project / ".gitignore"
        gitignore.write_bytes(b"node_modules/\n!keep.log")  # no trailing newline
        self.install("gemini", skill)
        self.install("codex", skill)
        data = gitignore.read_bytes()
        self.assertTrue(data.startswith(b"node_modules/\n!keep.log\n"), data)
        self.assertEqual(data.count(ia.GITIGNORE_ENTRY.encode("utf-8")), 1)
        self.assertTrue(data.endswith((ia.GITIGNORE_ENTRY + "\n").encode("utf-8")))

    def test_a_crlf_gitignore_stays_crlf(self):
        skill = make_skill(self.scratch() / "adminwright")
        gitignore = self.project / ".gitignore"
        gitignore.write_bytes(b"dist/\r\n")
        self.install("pi", skill)
        data = gitignore.read_bytes()
        self.assertEqual(data.count(b"\n"), data.count(b"\r\n"), "mixed line endings")
        self.assertIn(ia.GITIGNORE_ENTRY.encode("utf-8") + b"\r\n", data)

    def test_a_non_utf8_gitignore_is_left_alone_with_a_warning(self):
        skill = make_skill(self.scratch() / "adminwright")
        gitignore = self.project / ".gitignore"
        gitignore.write_bytes(b"caf\xe9/\n")
        _out, err = self.install("cursor", skill)
        self.assertEqual(gitignore.read_bytes(), b"caf\xe9/\n")
        self.assertIn("WARNING", err)
        self.assertIn(ia.GITIGNORE_ENTRY, err)
        self.assertTrue(self.project.joinpath(*MACHINE_FILE).is_file())


def readme_section(path, heading):
    """The text of one `## ` section of a Markdown file, subsections included."""
    text = path.read_text(encoding="utf-8")
    start = text.index(heading)
    end = text.find("\n## ", start + 1)
    return text[start:end if end != -1 else len(text)]


# How agents/README.md names each harness in its tables.
README_LABELS = {
    "claude-code": "Claude Code",
    "opencode": "opencode",
    "codex": "Codex",
    "gemini": "Gemini CLI",
    "cursor": "Cursor",
    "antigravity": "Antigravity",
    "pi": "Pi",
}


class TestInstallDocs(unittest.TestCase):
    """What agents/README.md says the installer does must match the sources and
    the harnesses (ocr-installer#10)."""

    def install_section(self):
        return readme_section(SKILL_ROOT / "agents" / "README.md",
                              "## Installing the agents into any harness")

    def test_the_readme_names_every_claude_only_key_the_sources_carry(self):
        # It said "`tools` and `model` ... are not carried over"; no source has
        # `tools`, and the reviewers' `disallowedTools` went unmentioned.
        keys = set()
        for name in ia.AGENT_ORDER:
            text = (SKILL_ROOT / "agents" / (name + ".md")).read_text(encoding="utf-8")
            keys |= set(ia.frontmatter_fields(ia.FRONTMATTER.match(text).group(1)))
        keys -= {"name", "description"}
        self.assertIn("disallowedTools", keys)
        section = self.install_section()
        for key in sorted(keys):
            self.assertIn("`" + key + "`", section,
                          key + " reaches only Claude Code; agents/README.md must say so")

    def test_the_readme_states_the_reviewer_guard_for_every_harness(self):
        self.assertEqual(set(README_LABELS), set(ia.HARNESSES) - {"generic"},
                         "a new harness needs a row in the reviewer-guard table")
        section = self.install_section()
        rows = dict(re.findall(r"^\| ([^|]+?) \| (Enforced|Not enforced|Not applicable)",
                               section, re.MULTILINE))
        for harness, label in README_LABELS.items():
            self.assertIn(label, rows, harness + ": no reviewer-guard row")
        self.assertEqual(rows["Claude Code"], "Enforced")
        self.assertEqual([label for label, state in rows.items() if state == "Enforced"],
                         ["Claude Code"], "only Claude Code enforces disallowedTools")


class TestLineEndingsAndConsole(InstallBase):
    def test_every_written_file_uses_lf_only(self):
        # Path.write_text and open(..., "w") translate \n to \r\n on Windows,
        # which leaves generated files in a repo that is eol=lf mixed or dirty.
        for harness in sorted(ia.HARNESSES):
            with self.subTest(harness=harness):
                project = Path(tempfile.mkdtemp(prefix="adminwright-lf-"))
                self.addCleanup(shutil.rmtree, str(project), ignore_errors=True)
                code, out, err = run("--harness", harness, "--project-root", project,
                                     "--append-pointer")
                self.assertEqual(code, 0, out + err)
                files = [p for p in project.rglob("*") if p.is_file()]
                self.assertTrue(files)
                for path in files:
                    self.assertNotIn(b"\r", path.read_bytes(), str(path.relative_to(project)))

    def test_piped_cp1252_stdout_with_a_non_ascii_project_path_does_not_crash(self):
        # An agent harness pipes stdout; on Windows that is cp1252, which cannot
        # encode most project names. The install and its prints must survive.
        base = Path(tempfile.mkdtemp(prefix="adminwright-üni-"))
        self.addCleanup(shutil.rmtree, str(base), ignore_errors=True)
        project = base / "Schlüssel \U0001F511 项目"
        project.mkdir()
        code, out, err = run_piped_cp1252("--harness", "codex", "--project-root", project,
                                          "--append-pointer")
        self.assertEqual(code, 0, err.decode("utf-8", "replace"))
        self.assertNotIn(b"Traceback", err)
        self.assertIn("Schlüssel \U0001F511".encode("utf-8"), out)
        self.assertTrue((project / "AGENTS.md").is_file())

    def test_piped_cp1252_error_output_does_not_crash_either(self):
        missing = self.project / "nächste \U0001F511"
        code, _out, err = run_piped_cp1252("--harness", "generic", "--project-root", missing)
        self.assertEqual(code, 2)
        self.assertNotIn(b"Traceback", err)
        self.assertIn("nächste".encode("utf-8"), err)


if __name__ == "__main__":
    unittest.main()
