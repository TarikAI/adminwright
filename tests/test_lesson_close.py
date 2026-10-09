# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TarikAI
"""`lesson adopt` and `lesson reject` close a proposed lesson and change nothing else.

A lesson is a file other people review and git diffs. The two commands rewrite it in
place, so each test here is a way that could go wrong quietly: an adoption recorded for an
edit that never landed, a line ending flipped, a field forged through an argument, an
index that no longer matches the files, a "dry run" that wrote.

Every test runs the real script from a throw-away copy of the skill that is its own git
checkout; lessons 0001-0005 in the real tree are never touched.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from cli_helpers import SKILL_ROOT, acm

HAVE_GIT = shutil.which("git") is not None

# Repository-locating variables a test run may inherit (a git hook, a worktree wrapper);
# the scratch checkout must be found from its own path.
GIT_ENV = {k: v for k, v in os.environ.items()
           if k not in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR")}

SCOPE = "references/security-governance.md"
REASONS = ("already-covered", "stack-specific", "style-preference", "unverified-cause",
           "agent-error", "not-reproduced", "out-of-scope", "provider-specific")


def git(base, *args):
    done = subprocess.run(
        ["git", "-C", str(base), "-c", "user.name=t", "-c", "user.email=t@example.invalid"] + list(args),
        capture_output=True, text=True, encoding="utf-8", errors="replace", env=GIT_ENV)
    if done.returncode != 0:
        raise AssertionError("git " + " ".join(args) + ": " + done.stderr)
    return done.stdout.strip()


def make_copy(parent, with_git):
    """A copy of the skill; its own git checkout, one clean commit deep, only with with_git."""
    base = Path(parent)
    base.mkdir(parents=True)
    for name in ("scripts", "assets", "lessons", "references"):
        shutil.copytree(str(SKILL_ROOT / name), str(base / name),
                        ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copyfile(str(SKILL_ROOT / "SKILL.md"), str(base / "SKILL.md"))
    if with_git:
        git(base, "init", "-q")
        git(base, "config", "core.autocrlf", "false")
        git(base, "add", "-A")
        git(base, "commit", "-q", "-m", "baseline")
    return base


def run_in(base, *args, env=None):
    """(exit code, stdout, stderr) of the copy's own script, with the cp1252 pipe a Windows harness gives."""
    environment = dict(os.environ)
    environment.update(env or {})
    environment["PYTHONIOENCODING"] = "cp1252"
    done = subprocess.run(
        [sys.executable, str(base / "scripts" / "admin_console_manifest.py")] + [str(a) for a in args],
        capture_output=True, text=True, encoding="utf-8", errors="replace", env=environment)
    return done.returncode, done.stdout, done.stderr


def lesson_text(number="0006", title="Rotate keys before export", scope=SCOPE, platforms='["b2b-saas"]',
                status="proposed", proposed="Target: `%s`.\n\nAdd the rotate-first sentence.\n" % SCOPE,
                notes="", extra_front=""):
    """A lesson in exactly the shape `lesson add` writes it (LF, five headings, trailing newline)."""
    return (
        "---\n"
        'id: "%s"\n'
        'title: "%s"\n'
        'date: "2026-03-02"\n'
        "category: gap\n"
        'scope: "%s"\n'
        "status: %s\n"
        "confidence: observed-once\n"
        "platforms: %s\n"
        "%s"
        "---\n\n"
        "## Trigger\n\nAn export runs.\n\n"
        "## Rule\n\nRotate first.\n\n"
        "## Evidence\n\n- Project A: gaps[] id gap.x\n\n"
        "## Proposed edit\n\n%s\n"
        "## Review notes\n%s"
    ) % (number, title, scope, status, platforms, extra_front, proposed, notes)


@unittest.skipUnless(HAVE_GIT, "git is required to make a checkout")
class CloseCase(unittest.TestCase):
    """A scratch checkout of the skill with lesson 0006 proposed and the tree clean."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="adminwright-close-"))
        self.addCleanup(shutil.rmtree, str(self.tmp), ignore_errors=True)
        self.base = make_copy(self.tmp / "skill", with_git=True)
        self.lessons = self.base / "lessons"
        self.home = self.tmp / "home"
        self.put(lesson_text())

    # --- fixtures -------------------------------------------------------------------------

    def run_lesson(self, *args):
        return run_in(self.base, "lesson", *args, env={"ADMINWRIGHT_HOME": str(self.home)})

    def put(self, data, number="0006", slug="rotate-keys-before-export"):
        """Write a lesson file (str as LF text, bytes as given), rebuild the index, commit it."""
        path = self.lessons / (number + "-" + slug + ".md")
        path.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
        self.assertEqual(self.run_lesson("index")[0], 0)
        git(self.base, "add", "-A")
        git(self.base, "commit", "-q", "-m", "lesson " + number)
        return path

    def edit_scope(self, relative=SCOPE, line="Rotate credentials before every export.\n"):
        with open(str(self.base / relative), "a", encoding="utf-8", newline="\n") as handle:
            handle.write(line)

    def commit_scope_edit(self, relative=SCOPE):
        self.edit_scope(relative)
        git(self.base, "add", relative)
        git(self.base, "commit", "-q", "-m", "guidance edit")
        return git(self.base, "rev-parse", "HEAD")

    def snapshot(self):
        """Every file under lessons/ by name, bytes included; the lock file is process state, not content."""
        return {p.name: p.read_bytes() for p in sorted(self.lessons.iterdir())
                if p.is_file() and p.name != ".lock"}

    def lesson_path(self, number="0006"):
        return sorted(self.lessons.glob(number + "-*.md"))[0]

    def refused(self, *args, contains=None):
        """Run, expect exit 2 with a one-line reason and nothing written under lessons/."""
        before = self.snapshot()
        code, out, err = self.run_lesson(*args)
        self.assertEqual(code, 2, out + err)
        if contains:
            self.assertIn(contains, err)
        self.assertEqual(self.snapshot(), before, "a refusal writes nothing")
        self.assertEqual(len([line for line in err.splitlines() if line.startswith("ERROR")]), 1, err)
        return err


# --- adopt: what must be true before it closes anything ------------------------------------


class TestAdoptRefusals(CloseCase):
    def adopt(self, *extra):
        return ("adopt", "0006", "--by", "Reviewer", "--date", "2026-10-09") + extra

    def test_it_refuses_when_no_edit_has_landed(self):
        self.refused(*self.adopt(), contains="make the guidance edit first")

    def test_a_dry_run_makes_the_same_refusal(self):
        self.refused(*self.adopt("--dry-run"), contains="make the guidance edit first")

    def test_an_edit_to_some_other_file_is_not_the_edit(self):
        self.edit_scope("references/architecture.md")
        self.refused(*self.adopt(), contains="no edit to " + SCOPE)

    def test_a_commit_that_does_not_touch_the_scope_file_is_refused(self):
        self.edit_scope("references/architecture.md")
        git(self.base, "commit", "-q", "-a", "-m", "other")
        sha = git(self.base, "rev-parse", "HEAD")
        self.refused(*self.adopt("--commit", sha), contains="does not change " + SCOPE)

    def test_a_commit_that_does_not_exist_is_refused(self):
        self.refused(*self.adopt("--commit", "deadbeefdeadbeef"), contains="names no commit")

    def test_a_commit_must_be_a_hash_never_a_revision_or_an_option(self):
        self.commit_scope_edit()
        for bad in ("HEAD", "HEAD~1", "main", "--output=x", "abc", "1234567\n- forged line"):
            code, _out, err = self.run_lesson(*self.adopt("--commit", bad))
            self.assertEqual(code, 2, bad)

    def test_empty_platforms_are_refused(self):
        self.put(lesson_text(platforms="[]"))
        self.edit_scope()
        self.refused(*self.adopt(), contains="empty platforms")

    def test_blank_platform_names_do_not_count(self):
        self.put(lesson_text(platforms='[""]'))
        self.edit_scope()
        self.refused(*self.adopt(), contains="empty platforms")

    def test_an_empty_proposed_edit_is_refused(self):
        self.put(lesson_text(proposed=""))
        self.edit_scope()
        self.refused(*self.adopt(), contains="empty '## Proposed edit'")

    def test_a_whitespace_only_proposed_edit_is_refused(self):
        self.put(lesson_text(proposed="  \n\n  \n"))
        self.edit_scope()
        self.refused(*self.adopt(), contains="empty '## Proposed edit'")

    def test_a_scope_that_names_nothing_is_refused(self):
        self.put(lesson_text(scope="references/nope.md"))
        self.refused(*self.adopt(), contains="names nothing that exists")

    def test_a_missing_scope_is_refused(self):
        self.put(lesson_text(scope=""))
        self.refused(*self.adopt(), contains="is missing")

    def test_the_bare_references_folder_is_refused(self):
        self.put(lesson_text(scope="references"))
        self.refused(*self.adopt(), contains="name the one reference file")

    def test_a_lesson_that_is_not_proposed_is_refused(self):
        for status, hint in (("adopted", "never reverted"), ("rejected", "reopens only with new evidence"),
                             ("superseded", "closed")):
            self.put(lesson_text(status=status))
            self.edit_scope()
            err = self.refused(*self.adopt(), contains="is %s, not proposed" % status)
            self.assertIn(hint, err)

    def test_a_shipped_adopted_lesson_cannot_be_adopted_again(self):
        before = self.snapshot()
        code, _out, err = self.run_lesson("adopt", "0005", "--by", "Reviewer", "--date", "2026-10-09")
        self.assertEqual(code, 2)
        self.assertIn("is adopted, not proposed", err)
        self.assertEqual(self.snapshot(), before)

    def test_an_unknown_lesson_is_refused(self):
        self.refused("adopt", "0099", "--by", "Reviewer", contains="no lesson 0099")

    def test_the_id_is_digits_only(self):
        for bad in ("../0006", "0006-rotate-keys-before-export", "x", "", "12345", "0006\nstatus"):
            code, _out, _err = self.run_lesson("adopt", bad, "--by", "Reviewer")
            self.assertEqual(code, 2, repr(bad))

    def test_a_short_id_is_zero_padded(self):
        self.edit_scope()
        code, _out, err = self.run_lesson("adopt", "6", "--by", "Reviewer", "--date", "2026-10-09")
        self.assertEqual(code, 0, err)
        self.assertIn("status: adopted\n", self.lesson_path().read_text(encoding="utf-8"))

    def test_the_reviewer_is_required(self):
        code, _out, _err = self.run_lesson("adopt", "0006", "--date", "2026-10-09")
        self.assertEqual(code, 2)
        self.refused(*self.adopt()[:2], "--by", "   ", contains="--by is required")

    def test_a_frontmatter_id_that_disagrees_with_the_file_name_is_refused(self):
        self.put(lesson_text(number="0007"), number="0006")
        self.edit_scope()
        self.refused(*self.adopt(), contains="does not match its file name")

    def test_a_repeated_status_field_is_refused(self):
        self.put(lesson_text(extra_front="status: adopted\n"))
        self.edit_scope()
        self.refused(*self.adopt(), contains="repeats status")

    def test_a_lesson_without_review_notes_is_refused(self):
        text = lesson_text().replace("## Review notes\n", "")
        self.put(text)
        self.edit_scope()
        self.refused(*self.adopt(), contains="exactly one '## Review notes'")


class TestOutsideACheckout(unittest.TestCase):
    """A plugin cache or a vendored copy is overwritten on update; an edit made there is lost."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="adminwright-close-"))
        self.addCleanup(shutil.rmtree, str(self.tmp), ignore_errors=True)
        self.base = make_copy(self.tmp / "skill", with_git=False)
        (self.base / "lessons" / "0006-rotate-keys-before-export.md").write_text(
            lesson_text(), encoding="utf-8", newline="\n")

    def test_both_commands_refuse_and_write_nothing(self):
        before = {p.name: p.read_bytes() for p in (self.base / "lessons").iterdir()}
        for args in (("adopt", "0006", "--by", "Reviewer"), ("reject", "0006", "--reason", "agent-error")):
            code, _out, err = run_in(self.base, "lesson", *args)
            self.assertEqual(code, 2, err)
            self.assertIn("git checkout of the skill", err)
        self.assertEqual({p.name: p.read_bytes() for p in (self.base / "lessons").iterdir()}, before)

    def test_an_empty_git_directory_is_not_a_checkout(self):
        (self.base / ".git").mkdir()
        code, _out, err = run_in(self.base, "lesson", "reject", "0006", "--reason", "agent-error")
        self.assertEqual(code, 2)
        self.assertIn("git checkout of the skill", err)


# --- adopt: what it writes -----------------------------------------------------------------


class TestAdopt(CloseCase):
    def adopt(self, *extra):
        return self.run_lesson("adopt", "0006", "--by", "Reviewer One", "--date", "2026-10-09", *extra)

    def original(self):
        return self.lesson_path().read_bytes()

    def test_a_working_tree_edit_is_enough_and_only_three_things_change(self):
        original = self.original()
        self.edit_scope()
        code, out, err = self.adopt()
        self.assertEqual(code, 0, err)
        expected = original.replace(b"status: proposed\n", b"status: adopted\n") \
            + b"\n- 2026-10-09 adopted by Reviewer One; edit in working tree\n"
        self.assertEqual(self.original(), expected)
        self.assertIn("proposed -> adopted", out)

    def test_a_staged_edit_counts_as_uncommitted(self):
        self.edit_scope()
        git(self.base, "add", SCOPE)
        code, _out, err = self.adopt()
        self.assertEqual(code, 0, err)

    def test_a_commit_is_recorded_in_full(self):
        original = self.original()
        sha = self.commit_scope_edit()
        code, _out, err = self.adopt("--commit", sha[:9].upper())
        self.assertEqual(code, 0, err)
        self.assertEqual(self.original(),
                         original.replace(b"status: proposed\n", b"status: adopted\n")
                         + ("\n- 2026-10-09 adopted by Reviewer One; edit in %s\n" % sha).encode("utf-8"))

    def test_a_commit_wins_over_a_working_tree_edit(self):
        sha = self.commit_scope_edit()
        self.edit_scope(line="more\n")
        code, _out, err = self.adopt("--commit", sha)
        self.assertEqual(code, 0, err)
        self.assertIn("edit in " + sha + "\n", self.lesson_path().read_text(encoding="utf-8"))

    def test_the_index_is_rebuilt_under_the_new_guidance_level(self):
        self.edit_scope()
        index = self.lessons / "index.md"
        self.assertIn("Guidance level: 0005", index.read_text(encoding="utf-8"))
        code, out, err = self.adopt()
        self.assertEqual(code, 0, err)
        text = index.read_text(encoding="utf-8")
        self.assertIn("Guidance level: 0006", text)
        row = [line for line in text.splitlines() if line.startswith("| [0006]")]
        self.assertEqual(len(row), 1)
        self.assertIn("| adopted |", row[0])
        self.assertIn("guidance level 0006", out)
        self.assertEqual(acm.read_lesson_frontmatter(self.lesson_path())["status"], "adopted")

    def test_the_index_equals_a_fresh_rebuild(self):
        self.edit_scope()
        self.adopt()
        written = (self.lessons / "index.md").read_bytes()
        self.assertEqual(self.run_lesson("index")[0], 0)
        self.assertEqual((self.lessons / "index.md").read_bytes(), written)

    def test_existing_review_notes_are_kept_and_the_new_one_follows_them(self):
        notes = "\n- 2026-09-01 reviewer: waiting on a second sighting\n- 2026-09-20 reviewer: second sighting seen\n"
        self.put(lesson_text(notes=notes))
        original = self.original()
        self.edit_scope()
        self.assertEqual(self.adopt()[0], 0)
        self.assertEqual(self.original(),
                         original.replace(b"status: proposed\n", b"status: adopted\n")
                         + b"- 2026-10-09 adopted by Reviewer One; edit in working tree\n")

    def test_blank_lines_after_the_last_note_stay_after_it(self):
        self.put(lesson_text(notes="\n- 2026-09-01 reviewer: note\n\n\n"))
        self.edit_scope()
        self.assertEqual(self.adopt()[0], 0)
        self.assertTrue(self.original().endswith(
            b"- 2026-09-01 reviewer: note\n- 2026-10-09 adopted by Reviewer One; edit in working tree\n\n\n"))

    def test_an_unknown_date_is_recorded_as_unknown_with_a_warning(self):
        self.edit_scope()
        code, _out, err = self.run_lesson("adopt", "0006", "--by", "Reviewer One")
        self.assertEqual(code, 0, err)
        self.assertIn("WARN: no --date supplied", err)
        self.assertIn("- unknown adopted by Reviewer One;", self.lesson_path().read_text(encoding="utf-8"))

    def test_it_reminds_the_reviewer_not_to_be_the_author(self):
        self.edit_scope()
        _code, _out, err = self.adopt()
        self.assertIn("records no author", err)

    def test_a_non_ascii_reviewer_is_written_as_utf8(self):
        self.edit_scope()
        code, _out, err = self.run_lesson("adopt", "0006", "--by", "Zoë Ångström", "--date", "2026-10-09")
        self.assertEqual(code, 0, err)
        self.assertIn("adopted by Zoë Ångström;", self.lesson_path().read_text(encoding="utf-8"))

    def test_an_area_scope_needs_the_file_named_in_proposed_edit(self):
        self.put(lesson_text(scope="scripts", proposed="Tighten the check.\n"))
        self.edit_scope("scripts/code_review.py", "# edit\n")
        self.refused("adopt", "0006", "--by", "Reviewer", contains="must name the one file under it")

    def test_an_area_scope_with_two_files_named_is_refused(self):
        self.put(lesson_text(scope="scripts",
                             proposed="Edit `scripts/code_review.py` and `scripts/install_agents.py`.\n"))
        self.edit_scope("scripts/code_review.py", "# edit\n")
        self.refused("adopt", "0006", "--by", "Reviewer", contains="one lesson changes one file")

    def test_an_area_scope_is_proved_by_the_named_file_only(self):
        self.put(lesson_text(scope="scripts", proposed="Edit `scripts/code_review.py`, near the end.\n"))
        self.edit_scope("scripts/install_agents.py", "# edit\n")
        self.refused("adopt", "0006", "--by", "Reviewer", contains="no edit to scripts/code_review.py")
        self.edit_scope("scripts/code_review.py", "# edit\n")
        code, _out, err = self.run_lesson("adopt", "0006", "--by", "Reviewer", "--date", "2026-10-09")
        self.assertEqual(code, 0, err)

    def test_a_file_named_outside_the_area_does_not_count(self):
        self.put(lesson_text(scope="scripts", proposed="Edit `%s`.\n" % SCOPE))
        self.edit_scope()
        self.refused("adopt", "0006", "--by", "Reviewer", contains="must name the one file under it")


# --- reject --------------------------------------------------------------------------------


class TestReject(CloseCase):
    def reject(self, reason="not-reproduced", *extra, number="0006"):
        return self.run_lesson("reject", number, "--reason", reason, "--date", "2026-10-09", *extra)

    def test_every_reason_is_recorded_after_status_and_nothing_else_changes(self):
        for offset, reason in enumerate(REASONS):
            number = "%04d" % (7 + offset)
            self.put(lesson_text(number=number), number=number)
        for offset, reason in enumerate(REASONS):
            number = "%04d" % (7 + offset)
            original = self.lesson_path(number).read_bytes()
            code, out, err = self.reject(reason, number=number)
            self.assertEqual(code, 0, reason + err)
            self.assertEqual(
                self.lesson_path(number).read_bytes(),
                original.replace(b"status: proposed\n",
                                 b"status: rejected\nrejected-reason: " + reason.encode() + b"\n")
                + ("\n- 2026-10-09 rejected (%s)\n" % reason).encode("utf-8"), reason)
            fields = acm.read_lesson_frontmatter(self.lesson_path(number))
            self.assertEqual((fields["status"], fields["rejected-reason"]), ("rejected", reason))
            self.assertIn("proposed -> rejected", out)

    def test_the_note_follows_the_reason(self):
        original = self.lesson_path().read_bytes()
        code, _out, err = self.reject("agent-error", "--note", "the run skipped Phase 3")
        self.assertEqual(code, 0, err)
        self.assertTrue(self.lesson_path().read_bytes().endswith(
            b"\n- 2026-10-09 rejected (agent-error): the run skipped Phase 3\n"))
        self.assertEqual(self.lesson_path().read_bytes().count(b"\n") - original.count(b"\n"), 3)

    def test_a_blank_note_is_the_same_as_none(self):
        code, _out, err = self.reject("agent-error", "--note", "   ")
        self.assertEqual(code, 0, err)
        self.assertTrue(self.lesson_path().read_bytes().endswith(b"\n- 2026-10-09 rejected (agent-error)\n"))

    def test_a_reason_outside_the_vocabulary_is_refused(self):
        before = self.snapshot()
        for bad in ("banana", "Agent-Error", "agent_error", "", "agent-error\nstatus: adopted"):
            code, _out, err = self.reject(bad)
            self.assertEqual(code, 2, repr(bad))
        self.assertEqual(self.snapshot(), before)

    def test_a_reason_is_required(self):
        code, _out, _err = self.run_lesson("reject", "0006", "--date", "2026-10-09")
        self.assertEqual(code, 2)

    def test_only_a_proposed_lesson_can_be_rejected(self):
        for status in ("adopted", "rejected", "superseded"):
            self.put(lesson_text(status=status))
            self.refused("reject", "0006", "--reason", "agent-error", contains="is %s, not proposed" % status)

    def test_a_shipped_adopted_lesson_cannot_be_rejected(self):
        before = self.snapshot()
        code, _out, err = self.run_lesson("reject", "0001", "--reason", "agent-error")
        self.assertEqual(code, 2)
        self.assertIn("is adopted, not proposed", err)
        self.assertEqual(self.snapshot(), before)

    def test_the_index_row_changes_and_the_guidance_level_does_not(self):
        code, _out, err = self.reject()
        self.assertEqual(code, 0, err)
        text = (self.lessons / "index.md").read_text(encoding="utf-8")
        self.assertIn("Guidance level: 0005", text)
        row = [line for line in text.splitlines() if line.startswith("| [0006]")]
        self.assertEqual(len(row), 1)
        self.assertIn("| rejected |", row[0])
        self.assertEqual(self.run_lesson("index")[0], 0)
        self.assertEqual((self.lessons / "index.md").read_text(encoding="utf-8"), text)

    def test_a_stale_reason_from_an_earlier_rejection_is_replaced_not_repeated(self):
        self.put(lesson_text(extra_front="rejected-reason: already-covered\n"))
        original = self.lesson_path().read_bytes()
        code, _out, err = self.reject("not-reproduced")
        self.assertEqual(code, 0, err)
        self.assertEqual(
            self.lesson_path().read_bytes(),
            original.replace(b"status: proposed\n", b"status: rejected\n")
            .replace(b"rejected-reason: already-covered\n", b"rejected-reason: not-reproduced\n")
            + b"\n- 2026-10-09 rejected (not-reproduced)\n")

    def test_a_title_with_three_dashes_is_not_taken_for_the_end_of_the_frontmatter(self):
        self.put(lesson_text(title="Use A --- B split"))
        code, _out, err = self.reject()
        self.assertEqual(code, 0, err)
        fields = acm.read_lesson_frontmatter(self.lesson_path())
        self.assertEqual((fields["title"], fields["status"]), ("Use A --- B split", "rejected"))

    def test_two_rejections_at_once_leave_two_rows_in_the_index(self):
        self.put(lesson_text(number="0007", title="Second lesson"), number="0007", slug="second-lesson")
        environment = dict(os.environ, PYTHONIOENCODING="utf-8", ADMINWRIGHT_HOME=str(self.home))
        script = str(self.base / "scripts" / "admin_console_manifest.py")
        procs = [subprocess.Popen([sys.executable, script, "lesson", "reject", number, "--reason",
                                   "out-of-scope", "--date", "2026-10-09"],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment)
                 for number in ("0006", "0007")]
        for proc in procs:
            _out, err = proc.communicate()
            self.assertEqual(proc.returncode, 0, err.decode("utf-8", "replace"))
        text = (self.lessons / "index.md").read_text(encoding="utf-8")
        self.assertEqual(sum(1 for line in text.splitlines() if "| rejected |" in line), 2, text)


# --- what an argument can and cannot write -------------------------------------------------


class TestInjection(CloseCase):
    ATTACKS = (
        "x\nstatus: adopted",
        "x\r\n## Rule",
        "x\n---\n",
        "x status: adopted",
        "x\u0085## Injected",
        "x\x0cstatus: adopted",
        "x\x1estatus: adopted",
        "x‮retroactively",
        "x\ttabbed",
    )

    def test_a_note_with_a_line_break_or_control_character_is_refused(self):
        for attack in self.ATTACKS:
            self.refused("reject", "0006", "--reason", "agent-error", "--note", attack)

    def test_a_reviewer_with_a_line_break_or_control_character_is_refused(self):
        self.edit_scope()
        for attack in self.ATTACKS:
            self.refused("adopt", "0006", "--by", attack, "--date", "2026-10-09")

    def test_a_date_with_a_line_break_is_refused_by_both_commands(self):
        self.edit_scope()
        attack = "2026-10-09\nstatus: adopted\n---"
        self.refused("adopt", "0006", "--by", "Reviewer", "--date", attack, contains="YYYY-MM-DD")
        self.refused("reject", "0006", "--reason", "agent-error", "--date", attack, contains="YYYY-MM-DD")

    def test_only_calendar_dates_are_accepted(self):
        for bad in ("2026-02-30", "2026-9-23", "23.09.2026", "2026-09-23T10:00", "yesterday"):
            self.refused("reject", "0006", "--reason", "agent-error", "--date", bad, contains="YYYY-MM-DD")

    def test_a_heading_shaped_note_cannot_become_a_heading(self):
        code, _out, err = self.run_lesson("reject", "0006", "--reason", "agent-error", "--date", "2026-10-09",
                                          "--note", "## Rule")
        self.assertEqual(code, 0, err)
        text = self.lesson_path().read_text(encoding="utf-8")
        self.assertEqual([line for line in text.splitlines() if line.startswith("## ")],
                         ["## Trigger", "## Rule", "## Evidence", "## Proposed edit", "## Review notes"])
        self.assertTrue(text.endswith("rejected (agent-error): ## Rule\n"))

    def test_nothing_is_left_behind_by_a_refusal(self):
        self.refused("reject", "0006", "--reason", "agent-error", "--note", "x\nstatus: adopted")
        self.assertEqual([p.name for p in self.lessons.iterdir() if p.name.endswith(".tmp")], [])


# --- bytes ---------------------------------------------------------------------------------


class TestByteForByte(CloseCase):
    def crlf(self, text):
        return text.replace("\n", "\r\n").encode("utf-8")

    def test_a_crlf_lesson_stays_crlf_when_rejected(self):
        original = self.crlf(lesson_text())
        self.put(original)
        code, _out, err = self.run_lesson("reject", "0006", "--reason", "not-reproduced",
                                          "--note", "no second sighting", "--date", "2026-10-09")
        self.assertEqual(code, 0, err)
        written = self.lesson_path().read_bytes()
        self.assertEqual(written,
                         original.replace(b"status: proposed\r\n",
                                          b"status: rejected\r\nrejected-reason: not-reproduced\r\n")
                         + b"\r\n- 2026-10-09 rejected (not-reproduced): no second sighting\r\n")
        self.assertEqual(written.count(b"\n"), written.count(b"\r\n"), "no bare LF was introduced")

    def test_a_crlf_lesson_stays_crlf_when_adopted(self):
        original = self.crlf(lesson_text())
        self.put(original)
        self.edit_scope()
        code, _out, err = self.run_lesson("adopt", "0006", "--by", "Reviewer", "--date", "2026-10-09")
        self.assertEqual(code, 0, err)
        written = self.lesson_path().read_bytes()
        self.assertEqual(written, original.replace(b"status: proposed\r\n", b"status: adopted\r\n")
                         + b"\r\n- 2026-10-09 adopted by Reviewer; edit in working tree\r\n")
        self.assertEqual(written.count(b"\n"), written.count(b"\r\n"))

    def test_mixed_line_endings_are_each_kept(self):
        text = lesson_text(notes="\n- 2026-09-01 reviewer: note\n").encode("utf-8")
        mixed = text.replace(b"## Trigger\n", b"## Trigger\r\n").replace(b"status: proposed\n", b"status: proposed\r\n")
        self.put(mixed)
        code, _out, err = self.run_lesson("reject", "0006", "--reason", "agent-error", "--date", "2026-10-09")
        self.assertEqual(code, 0, err)
        self.assertEqual(
            self.lesson_path().read_bytes(),
            mixed.replace(b"status: proposed\r\n", b"status: rejected\r\nrejected-reason: agent-error\r\n")
            + b"- 2026-10-09 rejected (agent-error)\n")

    def test_a_byte_order_mark_is_kept(self):
        original = b"\xef\xbb\xbf" + lesson_text().encode("utf-8")
        self.put(original)
        code, _out, err = self.run_lesson("reject", "0006", "--reason", "agent-error", "--date", "2026-10-09")
        self.assertEqual(code, 0, err)
        self.assertEqual(
            self.lesson_path().read_bytes(),
            original.replace(b"status: proposed\n", b"status: rejected\nrejected-reason: agent-error\n")
            + b"\n- 2026-10-09 rejected (agent-error)\n")
        self.assertEqual(acm.read_lesson_frontmatter(self.lesson_path())["status"], "rejected")

    def test_a_file_without_a_final_newline_gets_only_the_newline_it_needs(self):
        original = lesson_text(notes="\n- 2026-09-01 reviewer: note").encode("utf-8")
        self.assertFalse(original.endswith(b"\n"))
        self.put(original)
        code, _out, err = self.run_lesson("reject", "0006", "--reason", "agent-error", "--date", "2026-10-09")
        self.assertEqual(code, 0, err)
        self.assertEqual(
            self.lesson_path().read_bytes(),
            original.replace(b"status: proposed\n", b"status: rejected\nrejected-reason: agent-error\n")
            + b"\n- 2026-10-09 rejected (agent-error)")

    def test_an_empty_section_with_no_final_newline_is_filled(self):
        original = lesson_text().encode("utf-8")[:-1]
        self.assertTrue(original.endswith(b"## Review notes"))
        self.put(original)
        code, _out, err = self.run_lesson("reject", "0006", "--reason", "agent-error", "--date", "2026-10-09")
        self.assertEqual(code, 0, err)
        self.assertTrue(self.lesson_path().read_bytes().endswith(
            b"## Review notes\n\n- 2026-10-09 rejected (agent-error)"))

    def test_text_in_the_body_that_looks_like_a_field_is_left_alone(self):
        proposed = "Target: `%s`.\n\nstatus: proposed\nrejected-reason: style-preference\n" % SCOPE
        original = lesson_text(proposed=proposed).encode("utf-8")
        self.put(original)
        code, _out, err = self.run_lesson("reject", "0006", "--reason", "agent-error", "--date", "2026-10-09")
        self.assertEqual(code, 0, err)
        written = self.lesson_path().read_bytes()
        self.assertEqual(written.count(b"status: proposed\n"), 1, "only the frontmatter field changed")
        self.assertEqual(written.count(b"rejected-reason: style-preference\n"), 1)

    def test_every_other_lesson_and_file_is_untouched(self):
        before = self.snapshot()
        index_before = before.pop("index.md")
        code, _out, err = self.run_lesson("reject", "0006", "--reason", "agent-error", "--date", "2026-10-09")
        self.assertEqual(code, 0, err)
        after = self.snapshot()
        after.pop("index.md")
        changed = sorted(name for name in after if after[name] != before.get(name))
        self.assertEqual(changed, ["0006-rotate-keys-before-export.md"])
        self.assertEqual(sorted(after), sorted(before), "no file appeared or vanished")
        self.assertNotEqual((self.lessons / "index.md").read_bytes(), index_before)
        for lesson in SKILL_ROOT.joinpath("lessons").glob("000[1-5]-*.md"):
            self.assertEqual((self.lessons / lesson.name).read_bytes(), lesson.read_bytes(),
                             "the scratch copy started from the real lesson")
        status = git(self.base, "status", "--porcelain", "--", ".", ":!lessons/.lock")
        self.assertEqual(sorted(line.split()[-1] for line in status.splitlines()),
                         ["lessons/0006-rotate-keys-before-export.md", "lessons/index.md"])


# --- dry run and json ----------------------------------------------------------------------


class TestDryRunAndJson(CloseCase):
    def test_a_dry_run_writes_nothing_for_either_command(self):
        self.edit_scope()
        before = {p.name: p.read_bytes() for p in sorted(self.lessons.iterdir()) if p.is_file()}
        scope_before = (self.base / SCOPE).read_bytes()
        for args in (("adopt", "0006", "--by", "Reviewer", "--date", "2026-10-09"),
                     ("reject", "0006", "--reason", "agent-error", "--date", "2026-10-09")):
            code, out, err = self.run_lesson(*args, "--dry-run")
            self.assertEqual(code, 0, err)
            self.assertIn("DRY RUN", out)
            self.assertIn("nothing was written", out)
        after = {p.name: p.read_bytes() for p in sorted(self.lessons.iterdir()) if p.is_file()}
        self.assertEqual(after, before, "no file changed, appeared or vanished (not even the lock)")
        self.assertEqual((self.base / SCOPE).read_bytes(), scope_before)
        self.assertEqual([p.name for p in self.lessons.iterdir() if p.name.endswith(".tmp")], [])

    def test_a_dry_run_shows_exactly_the_lines_that_would_change(self):
        code, out, err = self.run_lesson("reject", "0006", "--reason", "agent-error", "--note", "x",
                                         "--date", "2026-10-09", "--dry-run")
        self.assertEqual(code, 0, err)
        changes = [line[2:] for line in out.splitlines()
                   if line.startswith(("  -", "  +")) and not line.startswith(("  ---", "  +++"))]
        self.assertEqual(changes, ["-status: proposed", "+status: rejected", "+rejected-reason: agent-error",
                                   "+", "+- 2026-10-09 rejected (agent-error): x"])

    def test_a_dry_run_after_an_edit_names_what_adopt_would_record(self):
        sha = self.commit_scope_edit()
        code, out, err = self.run_lesson("adopt", "0006", "--by", "Reviewer", "--commit", sha,
                                         "--date", "2026-10-09", "--dry-run")
        self.assertEqual(code, 0, err)
        self.assertIn("+- 2026-10-09 adopted by Reviewer; edit in " + sha, out)

    def test_json_reports_the_change(self):
        self.edit_scope()
        code, out, err = self.run_lesson("adopt", "0006", "--by", "Reviewer", "--date", "2026-10-09", "--json")
        self.assertEqual(code, 0, err)
        result = json.loads(out)
        self.assertEqual(result["ok"], True)
        self.assertEqual((result["id"], result["from"], result["to"]), ("0006", "proposed", "adopted"))
        self.assertEqual(Path(result["file"]), self.lesson_path())
        self.assertEqual(Path(result["index"]), self.lessons / "index.md")
        self.assertFalse(result["dryRun"])

    def test_json_for_reject_and_for_a_dry_run(self):
        code, out, err = self.run_lesson("reject", "0006", "--reason", "agent-error", "--json", "--dry-run")
        self.assertEqual(code, 0, err)
        result = json.loads(out)
        self.assertEqual((result["ok"], result["from"], result["to"], result["dryRun"]),
                         (True, "proposed", "rejected", True))
        self.assertIsNone(result["index"], "a dry run rebuilds nothing")
        code, out, err = self.run_lesson("reject", "0006", "--reason", "agent-error", "--json",
                                         "--date", "2026-10-09")
        self.assertEqual(json.loads(out)["index"], str(self.lessons / "index.md"))

    def test_json_stdout_carries_nothing_but_the_object(self):
        self.edit_scope()
        _code, out, err = self.run_lesson("adopt", "0006", "--by", "Reviewer", "--json")
        json.loads(out)
        self.assertIn("WARN", err)
        self.assertIn("NOTE", err)

    def test_a_json_refusal_is_reported_on_stdout_and_stderr_with_exit_2(self):
        code, out, err = self.run_lesson("adopt", "0006", "--by", "Reviewer", "--json")
        self.assertEqual(code, 2)
        result = json.loads(out)
        self.assertEqual((result["ok"], result["id"]), (False, "0006"))
        self.assertIn("make the guidance edit first", result["error"])
        self.assertIn("ERROR: " + result["error"], err)


class TestHelp(unittest.TestCase):
    def test_lesson_help_lists_both_commands(self):
        code, out, _err = run_in(SKILL_ROOT, "lesson", "--help")
        self.assertEqual(code, 0)
        self.assertIn("adopt", out)
        self.assertIn("reject", out)

    def test_reject_help_names_the_eight_reasons(self):
        _code, out, _err = run_in(SKILL_ROOT, "lesson", "reject", "--help")
        flat = " ".join(out.split())
        for reason in REASONS:
            self.assertIn(reason, flat)

    def test_the_reasons_are_the_ones_the_format_documents(self):
        self.assertEqual(tuple(acm.LESSON_REJECT_REASONS), REASONS)
        readme = (SKILL_ROOT / "lessons" / "README.md").read_text(encoding="utf-8")
        section = readme.split("\n## Rejection\n", 1)[1].split("\n## Index maintenance", 1)[0]
        for reason in REASONS:
            self.assertIn("`%s`" % reason, section)


if __name__ == "__main__":
    unittest.main()
