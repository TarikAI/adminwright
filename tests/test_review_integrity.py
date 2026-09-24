# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TarikAI
"""The review state machine, and the git check of what a reviewer pass changed.

A review verdict must not stand beside an open blocking finding, a contest must not
be flipped straight back to reviewed, and a reviewer that edits what it reviews has
reviewed nothing. Tool restrictions are advisory in Claude Code and absent in other
harnesses, so `review-scope` checks the working tree itself.
"""

import json
import subprocess
import unittest

from cli_helpers import TempProject

CAPABILITY = "entities[user].capabilities[user.list]"


def gap(gid, severity="high", description="Search returns other tenants' users in user.list", **extra):
    entry = {"id": gid, "severity": severity, "description": description, "status": "open",
             "rationale": "", "evidence": []}
    entry.update(extra)
    return entry


class ReviewCase(TempProject):
    def setUp(self):
        super().setUp()
        self.data = self.build_honest_manifest()

    def add_gap(self, *entries):
        return self.cli("add", "--kind", "gap", "--json", json.dumps(list(entries)))

    def set_review(self, value):
        return self.cli("set", "--path", CAPABILITY + ".reviewStatus", "--value", value)


class TestReviewedWithBlockingGap(ReviewCase):
    def test_filing_a_blocking_gap_against_a_reviewed_capability_is_never_refused(self):
        # The OCR bridge files its findings as one all-or-nothing batch; refusing the
        # batch because a capability is already reviewed would lose every finding.
        code, _out, err = self.add_gap(gap("gap.leak"), gap("gap.other", severity="low", description="Copy"))
        self.assertEqual(code, 0, err)
        self.assertIn("reviewed-with-blocking-gap", err, "the filer is told to contest it")
        code, out, _err = self.validate(phase="plan")
        self.assertEqual(code, 1)
        self.assertIn("[reviewed-with-blocking-gap]", out)

    def test_marking_reviewed_while_a_blocking_gap_is_open_is_refused(self):
        self.capability_of(self.data)["reviewStatus"] = "unreviewed"
        self.data["gaps"] = [gap("gap.leak", capability="user.list", description="Leaks tenants")]
        self.write(self.data)
        code, _out, err = self.set_review("reviewed")
        self.assertEqual(code, 2)
        self.assertIn("[reviewed-with-blocking-gap]", err)

    def test_advisory_gaps_and_other_capabilities_do_not_block(self):
        self.data["gaps"] = [
            gap("gap.low", severity="medium"),
            gap("gap.bulk", description="user.list.bulk exports every tenant"),
            gap("gap.done", status="fixed", evidence=["evidence/tests.log"]),
        ]
        self.write(self.data)
        self.assertNotIn("reviewed-with-blocking-gap", self.rules())


class TestContestLoop(ReviewCase):
    def test_the_contest_loop_runs_in_order_and_cannot_be_short_cut(self):
        # 1. A reviewer files the gap, then contests.
        code, _o, err = self.add_gap(gap("gap.leak", capability="user.list"))
        self.assertEqual(code, 0, err)
        code, _o, err = self.set_review("contested")
        self.assertEqual(code, 0, err)
        # 2. Nobody flips it straight back.
        code, _o, err = self.set_review("reviewed")
        self.assertEqual(code, 2)
        self.assertIn("[review-transition]", err)
        # 3. The implementer marks the gap fixed while the capability is still
        #    contested: told, not refused, or the fix could never be recorded.
        code, _o, err = self.cli("set", "--path", "gaps[gap.leak].evidence", "--value", "evidence/tests.log")
        self.assertEqual(code, 0, err)
        code, _o, err = self.cli("set", "--path", "gaps[gap.leak].status", "--value", "fixed")
        self.assertEqual(code, 0, err)
        self.assertIn("contested-needs-gap", err)
        # 4. Even with the gap fixed, contested -> reviewed skips the implementer.
        code, _o, err = self.set_review("reviewed")
        self.assertEqual(code, 2)
        self.assertIn("[review-transition]", err)
        self.assertNotIn("reviewed-with-blocking-gap", err)
        # 5. The implementer returns it to unreviewed; a reviewer reviews it again.
        code, _o, err = self.set_review("unreviewed")
        self.assertEqual(code, 0, err)
        code, _o, err = self.set_review("reviewed")
        self.assertEqual(code, 0, err)

    def test_contesting_without_a_gap_is_refused_at_every_profile(self):
        code, _o, err = self.set_review("contested")
        self.assertEqual(code, 2)
        self.assertIn("[contested-needs-gap]", err)


class ReviewScopeCase(ReviewCase):
    def setUp(self):
        super().setUp()
        self.data["agents"] = [
            {"id": role + "-1", "role": role, "ownsCapabilities": [], "ownsScreens": [], "status": "active"}
            for role in ("ux-reviewer", "qa", "security", "implementer")
        ]
        self.write(self.data)
        (self.root / "src").mkdir()
        (self.root / "src" / "users.ts").write_text("export const list = () => repo.find();\n", encoding="utf-8")
        self.git("init", "-q")
        self.git("add", "-A")
        self.git("-c", "user.email=test@example.test", "-c", "user.name=test", "-c", "commit.gpgsign=false",
                 "commit", "-q", "--no-verify", "-m", "base")

    def git(self, *args):
        done = subprocess.run(["git", "-C", str(self.root)] + list(args), capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout.strip()

    def scope(self, agent, *extra):
        return self.cli("review-scope", "--agent", agent, *extra)

    def touch(self, rel, text="changed\n"):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


class TestReviewScope(ReviewScopeCase):
    def test_a_reviewer_editing_product_code_is_caught(self):
        self.assertEqual(self.scope("ux-reviewer-1", "--start")[0], 0)
        self.touch("src/users.ts", "export const list = () => repo.find({ limit: 50 });\n")
        code, _out, err = self.scope("ux-reviewer-1")
        self.assertEqual(code, 1)
        self.assertIn("src/users.ts", err)

    def test_work_uncommitted_before_dispatch_is_not_blamed_on_the_reviewer(self):
        self.touch("src/users.ts", "export const list = () => repo.find({ tenant });\n")  # the implementer's
        self.assertEqual(self.scope("ux-reviewer-1", "--start")[0], 0)
        self.touch("evidence/ux-review.md", "Reviewed /admin/users as ops: 4 states seen\n")
        self.data["crossCutting"]["experience"]["evidence"].append("evidence/ux-review.md")
        self.write(self.data)
        self.touch(".admin-console/worklog/ux-reviewer-1.md", "# worklog ux-reviewer-1\n")
        code, out, err = self.scope("ux-reviewer-1")
        self.assertEqual(code, 0, err)
        self.assertIn("within a reviewer's write surface", out)

    def test_changing_or_reverting_someone_elses_uncommitted_work_is_caught(self):
        self.touch("src/users.ts", "export const list = () => repo.find({ tenant });\n")
        self.assertEqual(self.scope("security-1", "--start")[0], 0)
        self.git("checkout", "--", "src/users.ts")
        code, _out, err = self.scope("security-1")
        self.assertEqual(code, 1)
        self.assertIn("src/users.ts", err)

    def test_evidence_counts_only_once_cited_and_never_as_source(self):
        self.assertEqual(self.scope("security-1", "--start")[0], 0)
        self.touch("evidence/npm-audit.log", "found 0 vulnerabilities\n")
        self.assertEqual(self.scope("security-1")[0], 1, "an uncited file is not evidence yet")
        self.data["qualityGates"][0]["evidence"].append("evidence/npm-audit.log")
        self.data["qualityGates"][0]["evidence"].append("src/users.ts")
        self.write(self.data)
        self.assertEqual(self.scope("security-1")[0], 0)
        self.touch("src/users.ts", "export const list = () => repo.find({ tenant });\n")
        code, _out, err = self.scope("security-1")
        self.assertEqual(code, 1, "citing source code does not make editing it a reviewer's job")
        self.assertIn("src/users.ts", err)

    def test_qa_adds_new_tests_but_does_not_edit_existing_ones(self):
        self.assertEqual(self.scope("qa-1", "--start")[0], 0)
        self.touch("tests/negative/authz.spec.ts",
                   "test('support cannot list', () => { expect(can('support', 'user.list')).toBe(false); });\n")
        self.touch("tests/fixtures/users.json", "[]\n")
        self.assertEqual(self.scope("qa-1")[0], 0)
        self.touch("tests/user.spec.ts", "test('weakened', () => { expect(true).toBe(true); });\n")
        code, _out, err = self.scope("qa-1")
        self.assertEqual(code, 1)
        self.assertIn("an existing test", err)

    def test_other_agents_worklogs_are_off_limits(self):
        self.assertEqual(self.scope("ux-reviewer-1", "--start")[0], 0)
        self.touch(".admin-console/worklog/implementer-1.md", "verified: nothing\n")
        code, _out, err = self.scope("ux-reviewer-1")
        self.assertEqual(code, 1)
        self.assertIn("another agent's worklog", err)

    def test_a_commit_can_be_the_base_and_paths_can_be_excused(self):
        self.touch("src/users.ts", "export const list = () => repo.find({ tenant });\n")
        self.assertEqual(self.scope("qa-1", "--base", "HEAD")[0], 1)
        self.assertEqual(self.scope("qa-1", "--base", "HEAD", "--ignore", "src/*")[0], 0)
        code, _out, err = self.scope("qa-1", "--base", "no-such-commit")
        self.assertEqual(code, 2)
        self.assertIn("is not a commit", err)

    def test_it_checks_reviewers_only_and_needs_a_starting_point(self):
        code, _out, err = self.scope("implementer-1", "--start")
        self.assertEqual(code, 2)
        self.assertIn("reviewer", err)
        code, _out, err = self.scope("qa-1")
        self.assertEqual(code, 2)
        self.assertIn("No starting point", err)
        code, _out, err = self.scope("solo", "--role", "qa", "--start")
        self.assertEqual(code, 0, err)


if __name__ == "__main__":
    unittest.main()
