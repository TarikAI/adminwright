# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TarikAI
"""Regression tests for install_agents.py.

Standard library only (unittest). Run: python -m unittest discover -s tests -v
"""

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

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


class InstallBase(unittest.TestCase):
    def setUp(self):
        self.project = Path(tempfile.mkdtemp(prefix="adminwright-install-"))
        self.addCleanup(shutil.rmtree, self.project, ignore_errors=True)


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
        for path in (self.project / ".opencode" / "agent").glob("*.md"):
            frontmatter = path.read_text(encoding="utf-8").split("\n---", 1)[0]
            self.assertIn("mode: subagent", frontmatter, path.name)

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


if __name__ == "__main__":
    unittest.main()
