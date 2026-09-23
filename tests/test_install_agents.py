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


# Where each harness's own documentation says custom agents live, written out
# here independently of ia.HARNESSES (checked 2026-09-23; see the URLs beside the
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

    def test_generic_install_replaces_token_everywhere(self):
        code, out, _err = run("--harness", "generic", "--project-root", self.project)
        self.assertEqual(code, 0, out)
        target = self.project / ".adminwright" / "agents"
        files = sorted(p.name for p in target.glob("*.md"))
        self.assertEqual(len(files), len(ia.AGENT_ORDER))
        for path in target.glob("*.md"):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("${CLAUDE_PLUGIN_ROOT}", text, path.name)
            self.assertIn(SKILL_ROOT.as_posix(), text, path.name)

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
        block = ia.pointer_block("/agents", "/skill", None)
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
        expected_body = TRICKY_BODY.replace(ia.TOKEN, skill.resolve().as_posix())
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
        expected_body = TRICKY_BODY.replace(ia.TOKEN, skill.resolve().as_posix())
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
                self.assertFalse((project / ".adminwright").exists())

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
                self.assertEqual(top, [".adminwright"] + (["AGENTS.md"] if harness == "pi" else []))

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
                self.assertEqual(spec.checked, "2026-09-23")
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
        self.assertNotIn("<!--", text.replace(ia.MARKER_START, "").replace(ia.MARKER_END, ""),
                         "no absolute-path comment is needed when the path is relative")
        for absolute in self.absolutes(self.project):
            self.assertNotIn(absolute, text.lower().replace("\\", "/"))

    def test_agent_copies_are_project_relative_when_the_skill_is_inside(self):
        skill = make_skill(self.project / "vendor" / "adminwright")
        self.pointer(skill=skill)
        for directory in (".adminwright/agents", ".codex/agents"):
            for path in (self.project / directory).iterdir():
                text = path.read_text(encoding="utf-8").replace("\\", "/").lower()
                self.assertIn("python vendor/adminwright/scripts/admin_console_manifest.py", text,
                              path.name)
                for absolute in self.absolutes(self.project):
                    self.assertNotIn(absolute, text, path.name)

    def test_absolute_path_appears_only_in_a_comment_when_the_skill_is_outside(self):
        self.pointer(skill=SKILL_ROOT)
        text = (self.project / "AGENTS.md").read_text(encoding="utf-8")
        absolute = SKILL_ROOT.resolve().as_posix()
        self.assertIn(ia.TOKEN, text)
        self.assertIn(absolute, text)
        comments = re.findall(r"<!--(.*?)-->", text, flags=re.DOTALL)
        holders = [c for c in comments if absolute in c]
        self.assertEqual(len(holders), 1, "the absolute path must be inside exactly one comment")
        self.assertNotIn(absolute, re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL))

    def test_the_project_root_is_never_written_into_the_pointer(self):
        self.pointer(skill=SKILL_ROOT)
        text = (self.project / "AGENTS.md").read_text(encoding="utf-8").lower().replace("\\", "/")
        for absolute in self.absolutes(self.project):
            self.assertNotIn(absolute, text)

    def test_a_hostile_path_cannot_close_the_comment_early(self):
        block = ia.pointer_block(".adminwright/agents", ia.TOKEN, "codex", "/tmp/a-->b")
        # start marker + the hint's own close + end marker; an unescaped `-->` in
        # the path would make it four and end the comment early
        self.assertEqual(block.count("-->"), 3)
        self.assertIn("/tmp/a--%3Eb", block)

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

    def test_reinstall_from_inside_to_outside_swaps_relative_for_variable(self):
        inside = make_skill(self.project / "tools" / "aw-a")
        self.pointer(skill=inside)
        self.pointer(skill=SKILL_ROOT)
        text = (self.project / "AGENTS.md").read_text(encoding="utf-8")
        self.assertNotIn("tools/aw-a", text)
        self.assertIn(ia.TOKEN, text)
        self.assertEqual(text.count(ia.MARKER_START), 1)

    def test_an_old_marked_block_with_absolute_paths_is_replaced_in_place(self):
        old = ia.pointer_block("/old/project/.adminwright/agents", "/old/machine/adminwright", "codex")
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
