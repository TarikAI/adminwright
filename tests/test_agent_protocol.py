# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TarikAI
"""Every manifest command the documentation tells an agent to run must be accepted.

Agents follow the docs literally. The first round of fixes left documented steps that
exit 2 when followed: a second `init` in the architect's Model step, and
`claim --role architect` with no capability. tests/test_doc_examples.py runs only the
add/set examples that need no placeholder. This test parses every other documented
command with the CLI's own argument parser, checks that shorthand commands in prose
use only options that exist, and walks the role protocol the agent files describe
against a scratch manifest, asserting each step's exit code.
"""

import contextlib
import glob
import io
import json
import re
import shlex
import shutil
import subprocess
import unittest
from pathlib import Path

from cli_helpers import SKILL_ROOT, TempProject, acm, run

DOC_GLOBS = ("SKILL.md", "INTEGRATIONS.md", "README.md", "references/*.md", "agents/*.md")
PARSER = acm.build_parser()
SUBPARSERS = next(
    action for action in PARSER._actions if action.__class__.__name__ == "_SubParsersAction"
).choices
# A real value for each option whose placeholder must be one of its choices.
CHOICE_VALUES = {
    "--role": "qa", "--kind": "gap", "--phase": "plan", "--format": "gap-report",
    "--profile": "standard", "--category": "gap", "--harness": "generic",
}
PLACEHOLDER = re.compile(r"<[^<>\s][^<>]*>")
INLINE = re.compile(r"`((?:" + "|".join(re.escape(name) for name in SUBPARSERS) + r") --[^`]*)`")


def doc_files():
    seen = []
    for pattern in DOC_GLOBS:
        for name in sorted(glob.glob(str(SKILL_ROOT / pattern))):
            if Path(name) not in seen:
                seen.append(Path(name))
    return seen


def fenced_commands(path):
    """(line, command) for every fenced line that invokes the manifest script."""
    lines = path.read_text(encoding="utf-8").splitlines()
    fenced = False
    pending, start = "", 0
    for number, line in enumerate(lines, 1):
        if line.strip().startswith("```"):
            fenced = not fenced
            continue
        if not fenced:
            continue
        if pending:
            pending += " " + line.strip()
        elif "admin_console_manifest.py" in line:
            pending, start = line.split("admin_console_manifest.py", 1)[1].strip(), number
        else:
            continue
        if pending.endswith("\\"):
            pending = pending[:-1].rstrip()
            continue
        yield start, pending
        pending = ""


def inline_commands(path):
    fenced = False
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip().startswith("```"):
            fenced = not fenced
            continue
        if not fenced:
            for match in INLINE.finditer(line):
                yield number, match.group(1)


def tokens_of(command):
    """shlex tokens with optional brackets and '...' removed and placeholders made real."""
    command = command.replace('"..."', "<value>").replace("'...'", "<value>")
    command = re.sub(r"\[(--[^\[\]]*)\]", r"\1", command).replace("...", " ")
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError as exc:
        return ["<unparseable: " + str(exc) + ">"]
    real = []
    for index, token in enumerate(tokens):
        if PLACEHOLDER.fullmatch(token):
            previous = tokens[index - 1] if index else ""
            if previous in CHOICE_VALUES:
                token = CHOICE_VALUES[previous]
            elif "|" in token:
                token = token.strip("<>").split("|")[0]
            else:
                token = "x"
        real.append(PLACEHOLDER.sub("x", token))
    return real


def parse(argv):
    """The argparse error for argv, or '' when it parses."""
    captured = io.StringIO()
    try:
        with contextlib.redirect_stderr(captured):
            PARSER.parse_args(argv)
    except SystemExit as exc:
        if exc.code:
            return captured.getvalue().strip().splitlines()[-1] if captured.getvalue().strip() else "exit " + str(exc.code)
    return ""


def option_problems(argv):
    """Options a shorthand command uses that its subcommand does not accept."""
    sub = SUBPARSERS.get(argv[0])
    if sub is None:
        return ["unknown command " + argv[0]]
    nested = [a for a in sub._actions if a.__class__.__name__ == "_SubParsersAction"]
    if nested and len(argv) > 1 and argv[1] in nested[0].choices:
        sub, argv = nested[0].choices[argv[1]], argv[1:]
    known = sub._option_string_actions
    problems = []
    for index, token in enumerate(argv[1:], 1):
        if not token.startswith("--"):
            continue
        flag = token.split("=", 1)[0]
        if flag not in known:
            problems.append("no option " + flag)
            continue
        action = known[flag]
        if action.choices and index + 1 < len(argv) and not argv[index + 1].startswith("--"):
            # A table cell may list alternatives: `validate --phase plan|release`.
            for value in argv[index + 1].split("|"):
                if value not in action.choices:
                    problems.append(flag + " " + value + " is not one of " + ", ".join(action.choices))
    return problems


class TestDocumentedCommandsParse(unittest.TestCase):
    def test_every_documented_command_line_is_accepted(self):
        failures, checked = [], 0
        for path in doc_files():
            for line, command in fenced_commands(path):
                argv = tokens_of(command)
                if not argv or argv[0] not in SUBPARSERS:
                    continue  # '<command> ...', the generic form
                checked += 1
                error = parse(argv)
                if error:
                    failures.append("%s:%d  %s\n    %s" % (
                        path.relative_to(SKILL_ROOT).as_posix(), line, command[:160], error))
        self.assertGreater(checked, 25)
        self.assertEqual(failures, [], "documented commands the CLI refuses:\n" + "\n".join(failures))

    def test_shorthand_commands_use_only_options_that_exist(self):
        failures, checked = [], 0
        for path in doc_files():
            for line, command in inline_commands(path):
                checked += 1
                problems = option_problems(tokens_of(command))
                if problems:
                    failures.append("%s:%d  `%s`: %s" % (
                        path.relative_to(SKILL_ROOT).as_posix(), line, command, "; ".join(problems)))
        self.assertGreater(checked, 10)
        self.assertEqual(failures, [], "\n".join(failures))

    def test_the_checker_catches_a_bad_command(self):
        self.assertTrue(parse(tokens_of("claim --manifest m.json --agent a --role harvester --capability c")))
        self.assertTrue(option_problems(tokens_of("set --manifest m --path p --values x")))
        self.assertEqual(parse(tokens_of("claim --manifest <m> --agent <id> --role <role> --capability <id>")), "")


class TestRoleProtocolWalk(TempProject):
    """The protocol the agent files describe, step by step, as the CLI answers it."""

    profile = "standard"

    def git(self, *args):
        done = subprocess.run(["git", "-C", str(self.root)] + list(args), capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)

    def step(self, expected, *args):
        code, out, err = self.cli(*args)
        self.assertEqual(code, expected, " ".join(str(a) for a in args) + "\n" + out + err)
        return out + err

    def test_a_run_from_architect_to_release_review(self):
        cap = "entities[user].capabilities[user.list]"
        data = self.build_honest_manifest()
        for capability in data["entities"][0]["capabilities"]:
            capability.update({"reviewStatus": "unreviewed", "reviewedBy": "", "owner": "",
                               "implementedBy": "", "status": "planned"})
        self.write(data)
        # The architect never runs init twice.
        code, _out, err = self.run_init_again()
        self.assertEqual(code, 2, err)
        self.step(0, "add", "--kind", "agent", "--json", json.dumps(
            {"id": "architect-1", "role": "architect", "ownsCapabilities": [], "ownsScreens": [],
             "status": "done"}))
        self.step(0, "validate", "--phase", "plan")
        # One implementer builds; a second builder is refused with exit 3.
        self.step(0, "claim", "--agent", "impl-1", "--role", "implementer", "--capability", "user.list")
        self.step(3, "claim", "--agent", "impl-2", "--role", "implementer", "--capability", "user.list")
        # A reviewer cannot claim while the builder holds it.
        self.step(3, "claim", "--agent", "ux-1", "--role", "ux-reviewer", "--capability", "user.list")
        self.step(0, "set", "--path", cap + ".status", "--value", "implemented")
        self.step(0, "release-claim", "--agent", "impl-1")
        # ux and security review in parallel; two qa agents do not.
        self.step(0, "claim", "--agent", "ux-1", "--role", "ux-reviewer", "--capability", "user.list")
        self.step(0, "claim", "--agent", "sec-1", "--role", "security", "--capability", "user.list")
        self.step(0, "claim", "--agent", "qa-1", "--role", "qa", "--capability", "user.list")
        self.step(3, "claim", "--agent", "qa-2", "--role", "qa", "--capability", "user.list")
        # security files a blocking gap; qa cannot sign until it is fixed.
        self.step(0, "add", "--kind", "gap", "--json", json.dumps(
            {"id": "gap.scope", "severity": "high", "description": "List ignores tenant scope",
             "status": "open", "rationale": "", "evidence": [], "capability": "user.list"}))
        self.step(0, "set", "--path", cap + ".reviewedBy", "--value", "qa-1")
        self.assertIn("[reviewed-with-blocking-gap]", self.step(2, "set", "--path", cap + ".reviewStatus",
                                                               "--value", "reviewed"))
        self.step(0, "set", "--path", cap + ".reviewStatus", "--value", "contested")
        # The implementer fixes it and returns the capability to unreviewed.
        self.step(0, "set", "--path", "gaps[gap.scope].evidence", "--value", "evidence/tests.log")
        self.step(0, "set", "--path", "gaps[gap.scope].status", "--value", "fixed")
        self.step(0, "set", "--path", cap + ".reviewStatus", "--value", "unreviewed")
        # qa signs; every reviewer releases.
        self.step(0, "set", "--path", cap + ".reviewStatus", "--value", "reviewed")
        for agent in ("ux-1", "sec-1", "qa-1"):
            self.step(0, "release-claim", "--agent", agent)
        self.step(0, "validate", "--phase", "release", "--project-root", self.root)

    def run_init_again(self):
        return run("init", "--project-root", self.root, "--name", "Again", "--archetype", "b2b-saas",
                   "--profile", "standard")

    @unittest.skipIf(shutil.which("git") is None, "git is not installed")
    def test_a_review_pass_is_checked_from_git(self):
        self.build_honest_manifest()
        self.git("init", "-q")
        self.git("add", "-A")
        self.git("-c", "user.email=t@example.test", "-c", "user.name=t", "-c", "commit.gpgsign=false",
                 "commit", "-q", "--no-verify", "-m", "base")
        self.step(0, "review-scope", "--agent", "solo-ux", "--role", "ux-reviewer", "--start")
        (self.root / "evidence" / "ux-review.md").write_text("Walked 4 states as ops\n", encoding="utf-8")
        self.step(0, "set", "--path", "crossCutting.experience.evidence", "--append",
                  "--value", "evidence/ux-review.md")
        self.step(0, "review-scope", "--agent", "solo-ux", "--role", "ux-reviewer")


if __name__ == "__main__":
    unittest.main()
