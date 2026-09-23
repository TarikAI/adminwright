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
MINIMUM_EXECUTED = 6


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
        token = token.replace("<path>", str(manifest)) if token == "<path>" else token
        resolved.append(token)
    return resolved


WORLD = (
    ("role", {"id": "ops", "name": "Operations", "responsibilities": ["Admin"], "scopes": ["all"]}),
    ("entity", {"id": "user", "name": "User", "sourceOfTruth": "postgres:users",
                "lifecycleStates": ["active"], "retention": "7 years"}),
    ("feedback", {"id": "fb-003", "observation": "o", "category": "gap",
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


if __name__ == "__main__":
    unittest.main()
