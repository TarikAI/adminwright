# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TarikAI
"""Every documented `add` / `set` example must actually run.

Four of them were once refused verbatim by the tool they documented: an uppercase
id (`FB-003`), a decision id with a capital, `appliesTo: ["*"]`, and a feedback
entry with no id. An agent following the docs to the letter hit a wall the docs
never mentioned. This test extracts every fenced command that invokes
`admin_console_manifest.py add` or `set` from the documentation, runs it against a
fresh `init` manifest (in document order, per file) and asserts exit 0.

Two kinds of block are not run:

* one containing an unresolved `<placeholder>` or a bare `...` -- it is a template,
  not an example, and the angle brackets say so;
* one with `<!-- doc-test: skip -->` on the line above its opening fence.
"""

import glob
import json
import re
import shlex
import tempfile
import unittest
from pathlib import Path

from cli_helpers import SKILL_ROOT, run

DOC_GLOBS = (
    "SKILL.md",
    "README.md",
    "INTEGRATIONS.md",
    "lessons/README.md",
    "references/*.md",
    "agents/*.md",
)
SKIP_MARKER = "<!-- doc-test: skip -->"
PLACEHOLDER = re.compile(r"<[A-Za-z][^<>\s]*>")
# SKILL.md's own examples (four of them) run in a separate, unseeded test and count there.
MINIMUM_EXECUTED = 4


def doc_files():
    seen = []
    for pattern in DOC_GLOBS:
        for name in sorted(glob.glob(str(SKILL_ROOT / pattern))):
            path = Path(name)
            if path not in seen:
                seen.append(path)
    return seen


def fenced_blocks(path):
    """Yield (start_line, skipped, lines) for every fenced block in a markdown file."""
    lines = path.read_text(encoding="utf-8").splitlines()
    index = 0
    while index < len(lines):
        if lines[index].strip().startswith("```"):
            opening = index
            previous = opening - 1
            while previous >= 0 and not lines[previous].strip():
                previous -= 1
            skipped = previous >= 0 and lines[previous].strip() == SKIP_MARKER
            body = []
            index += 1
            while index < len(lines) and not lines[index].strip().startswith("```"):
                body.append(lines[index])
                index += 1
            yield opening + 1, skipped, body
        index += 1


def commands_in(block):
    """Split a block into shell commands, joining `\\` continuations and open quotes."""
    commands = []
    current = ""
    for raw in block:
        line = raw.rstrip()
        if not current and not line.strip():
            continue
        if current:
            current += "\n" + line
        else:
            current = line
        stripped = current.rstrip()
        if stripped.endswith("\\"):
            current = stripped[:-1]
            continue
        try:
            shlex.split(current, posix=True)
        except ValueError:
            continue  # an open quote: the command continues on the next line
        commands.append(current)
        current = ""
    if current.strip():
        commands.append(current)
    return commands


INLINE_CODE = re.compile(r"`((?:add|set) [^`]*--manifest[^`]*)`")


def inline_commands(path):
    """Yield (line_number, command) for `add ...` / `set ...` spans in prose and tables.

    Three of the four refused examples lived in prose, not in a fenced block.
    """
    fenced = False
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip().startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            continue
        for match in INLINE_CODE.finditer(line):
            yield number, match.group(1)


def parse_command(command, root, manifest):
    """Return the script's argv (after the script path) or None if not a runnable add/set."""
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return None
    for position, token in enumerate(tokens):
        if token.endswith("admin_console_manifest.py"):
            args = tokens[position + 1:]
            break
    else:
        args = tokens  # an inline span such as `add --manifest <path> --kind ...`
    if not args or args[0] not in ("add", "set"):
        return None
    resolved = []
    for token in args:
        token = token.replace("<project-root>", str(root))
        token = str(manifest) if token in ("<path>", "<manifest>") else token
        resolved.append(token)
    return resolved


def runnable_commands(path, root, manifest):
    """(line, argv) for every add/set example in a file, in document order."""
    found = [
        (start, command)
        for start, skipped, body in fenced_blocks(path)
        if not skipped
        for command in commands_in(body)
    ] + list(inline_commands(path))
    found.sort(key=lambda pair: pair[0])
    for start, command in found:
        argv = parse_command(command, root, manifest)
        if argv is None or any(PLACEHOLDER.search(a) or a == "..." for a in argv):
            continue
        yield start, argv


def fresh_manifest(work):
    root = work / "project"
    root.mkdir()
    code, _out, err = run(
        "init", "--project-root", root, "--name", "DocExamples",
        "--archetype", "b2b-saas", "--profile", "standard",
    )
    assert code == 0, err
    return root, root / ".admin-console" / "manifest.json"


WORLD = (
    ("role", {"id": "ops", "name": "Operations", "responsibilities": ["Admin"], "scopes": ["all"]}),
    ("entity", {"id": "user", "name": "User", "sourceOfTruth": "postgres:users",
                "lifecycleStates": ["active"], "retention": "7 years"}),
    ("feedback", {"id": "fb-003", "observation": "o", "category": "gap", "scope": "SKILL.md",
                  "proposedChange": "c", "evidence": ["evidence/tests.log"]}),
)


def seed_documentation_world(manifest):
    """The examples talk about a user entity and a feedback entry; give them one."""
    for kind, obj in WORLD:
        code, _out, err = run("add", "--manifest", manifest, "--kind", kind,
                              "--json", json.dumps(obj))
        assert code == 0, err
    code, _out, err = run(
        "add", "--manifest", manifest, "--kind", "capability", "--entity", "user", "--json",
        json.dumps({"id": "user.suspend", "outcome": "Suspend an account", "kind": "command",
                    "roles": ["ops"], "risk": "high"}),
    )
    assert code == 0, err


class TestDocumentedExamples(unittest.TestCase):
    def test_every_documented_add_and_set_example_runs(self):
        executed = 0
        failures = []
        for path in doc_files():
            if path == SKILL_ROOT / "SKILL.md":
                continue  # runs unseeded, from its own role and entity, in the next test
            work = Path(tempfile.mkdtemp(prefix="adminwright-docs-"))
            root = work / "project"
            root.mkdir()
            code, _out, err = run(
                "init", "--project-root", root, "--name", "DocExamples",
                "--archetype", "b2b-saas", "--profile", "standard",
            )
            self.assertEqual(code, 0, err)
            manifest = root / ".admin-console" / "manifest.json"
            seed_documentation_world(manifest)
            try:
                found = [
                    (start, command)
                    for start, skipped, body in fenced_blocks(path)
                    if not skipped
                    for command in commands_in(body)
                ] + list(inline_commands(path))
                found.sort(key=lambda pair: pair[0])
                for start, command in found:
                    argv = parse_command(command, root, manifest)
                    if argv is None:
                        continue
                    if any(PLACEHOLDER.search(a) or a == "..." for a in argv):
                        continue
                    code, out, err = run(*argv)
                    executed += 1
                    if code != 0:
                        failures.append(
                            "%s:%d exit %d\n    %s\n    %s"
                            % (path.relative_to(SKILL_ROOT).as_posix(), start, code,
                               " ".join(argv)[:240], (err or out).strip()[:400])
                        )
            finally:
                import shutil
                shutil.rmtree(work, ignore_errors=True)
        self.assertGreaterEqual(
            executed, MINIMUM_EXECUTED,
            "too few documented examples were runnable; the extractor is probably broken",
        )
        self.assertEqual(
            failures, [],
            "documented examples that the tool refuses (fix the doc, or mark the block "
            "with '" + SKIP_MARKER + "' if it is deliberately illustrative):\n"
            + "\n".join(failures),
        )

    def test_skill_md_examples_run_on_a_fresh_manifest_without_help(self):
        """SKILL.md's first writes must work straight after `init`, in the order shown.

        The test above seeds a role and an entity first, which hid that SKILL.md's first
        `add` (a capability for role `ops` on entity `user`) was refused on a fresh
        manifest: nothing told the agent to add the role and the entity before it.
        """
        work = Path(tempfile.mkdtemp(prefix="adminwright-skill-"))
        try:
            root, manifest = fresh_manifest(work)
            executed = 0
            for start, argv in runnable_commands(SKILL_ROOT / "SKILL.md", root, manifest):
                code, out, err = run(*argv)
                executed += 1
                self.assertEqual(code, 0, "SKILL.md:%d exit %d\n%s" % (start, code, (err or out)[:400]))
            self.assertGreaterEqual(executed, 4, "SKILL.md lost its runnable role, entity, capability and set examples")
        finally:
            import shutil
            shutil.rmtree(work, ignore_errors=True)


CLOSE_STEP_PENDING = set()
HAND_PROMOTE = re.compile(r"feedback\[[^\]]*\]\.status'?\s+--value\s+promoted")


class TestFeedbackCloseStep(unittest.TestCase):
    def test_no_doc_tells_the_agent_to_promote_a_feedback_entry_by_hand(self):
        """`harvest` skips an entry already set to `promoted`, so a hand-set status loses it.

        Two documents told the agent to "close the project entry" with
        `set ... feedback[fb-003].status --value promoted`; done before `harvest`, the
        observation never reached the cross-project store.
        """
        offenders = []
        for path in doc_files():
            name = path.relative_to(SKILL_ROOT).as_posix()
            if name in CLOSE_STEP_PENDING:
                continue
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if HAND_PROMOTE.search(line):
                    offenders.append("%s:%d" % (name, number))
        self.assertEqual(offenders, [], "close a feedback entry with `harvest`, not by hand")

    def test_harvest_is_the_step_that_closes_the_entry(self):
        """The documented close step collects the entry and marks it `promoted` itself."""
        work = Path(tempfile.mkdtemp(prefix="adminwright-close-"))
        try:
            _root, manifest = fresh_manifest(work)
            code, _out, err = run("add", "--manifest", manifest, "--kind", "feedback",
                                  "--json", json.dumps(dict(WORLD[2][1])))
            self.assertEqual(code, 0, err)
            code, out, err = run("harvest", "--manifest", manifest, "--date", "2026-11-17",
                                 env={"ADMINWRIGHT_HOME": str(work / "store")})
            self.assertEqual(code, 0, err)
            self.assertIn("Harvested 1 observation", out)
            status = json.loads(manifest.read_text(encoding="utf-8"))["feedback"][0]["status"]
            self.assertEqual(status, "promoted")
        finally:
            import shutil
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
