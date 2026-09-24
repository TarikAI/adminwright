# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TarikAI
"""The learning loop must survive its own install (audit batch 4).

harvest -> promote -> lesson add -> edit a reference is how the skill improves. Each
test here is a way that loop silently lost work: lessons written into a plugin cache
that the next update discards, feedback stamped `promoted` before the store write
that then crashed, observations deleted by a sync, findings merged that had nothing
in common, and a lesson file that contradicted its own format documentation.
"""

import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from cli_helpers import SCRIPT, SKILL_ROOT, TempProject, acm, run

HAVE_GIT = shutil.which("git") is not None

# Repository-locating variables a test run may inherit (a git hook, a worktree
# wrapper); the scratch repositories below must be found from their own paths.
GIT_ENV = {k: v for k, v in os.environ.items()
           if k not in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR")}


def git_repository(path, commit=True):
    """`git init` a directory, optionally with one commit; return its short HEAD."""
    def git(*args):
        done = subprocess.run(["git", "-C", str(path)] + list(args), capture_output=True,
                              text=True, encoding="utf-8", errors="replace", env=GIT_ENV)
        if done.returncode != 0:
            raise AssertionError("git " + " ".join(args) + ": " + done.stderr)
        return done.stdout.strip()
    Path(path).mkdir(parents=True, exist_ok=True)
    git("init", "-q")
    if not commit:
        return None
    git("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q",
        "--allow-empty", "-m", "project commit")
    return git("rev-parse", "--short", "HEAD")


def make_skill_copy(parent, with_git=False, cache_path=False, vendored=False):
    """A copy of the skill; a checkout of its own repository only with with_git."""
    base = Path(parent)
    if cache_path:
        base = base / "plugins" / "cache" / "tarikai" / "adminwright" / "0123abcdef01"
    if vendored:
        base = base / ".claude" / "skills" / "adminwright"
    base.mkdir(parents=True)
    for name in ("scripts", "assets", "lessons", "references"):
        shutil.copytree(str(SKILL_ROOT / name), str(base / name),
                        ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copyfile(str(SKILL_ROOT / "SKILL.md"), str(base / "SKILL.md"))
    if with_git:
        git_repository(base, commit=False)
    return base


def run_copy(base, *args, env=None):
    environment = dict(os.environ)
    environment.update(env or {})
    environment["PYTHONIOENCODING"] = "cp1252"
    proc = subprocess.run(
        [sys.executable, str(base / "scripts" / "admin_console_manifest.py")] + [str(a) for a in args],
        capture_output=True, text=True, encoding="utf-8", errors="replace", env=environment,
    )
    return proc.returncode, proc.stdout, proc.stderr


LESSON = ["lesson", "add", "--title", "Rotate keys before export", "--category", "gap",
          "--scope", "references/security-governance.md", "--trigger", "an export runs",
          "--rule", "Rotate first.", "--date", "2026-09-23"]


class TestLessonsSurviveTheInstall(unittest.TestCase):
    """4.1 -- a plugin cache is replaced on update; lessons written there vanish."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="adminwright-learn-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.home = self.tmp / "home"
        self.env = {"ADMINWRIGHT_HOME": str(self.home)}

    def test_a_non_checkout_writes_to_the_outbox(self):
        base = make_skill_copy(self.tmp / "skill")
        before = sorted(p.name for p in (base / "lessons").iterdir())
        code, out, err = run_copy(base, *LESSON, env=self.env)
        self.assertEqual(code, 0, err)
        self.assertEqual(sorted(p.name for p in (base / "lessons").iterdir()), before,
                         "nothing may be written into the discarded copy")
        written = list((self.home / "outbox" / "lessons").glob("*.md"))
        self.assertEqual(len(written), 1)
        self.assertIn("not a git checkout", out)
        self.assertIn("github.com/TarikAI/adminwright", out)

    @unittest.skipUnless(HAVE_GIT, "git is required to make a checkout")
    def test_a_plugin_cache_path_is_refused_even_with_a_git_dir(self):
        base = make_skill_copy(self.tmp / "skill", with_git=True, cache_path=True)
        code, out, err = run_copy(base, *LESSON, env=self.env)
        self.assertEqual(code, 0, err)
        self.assertTrue(list((self.home / "outbox" / "lessons").glob("*.md")))
        self.assertIn("not a git checkout", out)

    @unittest.skipUnless(HAVE_GIT, "git is required to make a project repository")
    def test_a_copy_vendored_inside_a_project_repository_is_not_a_checkout(self):
        # README's manual install: the skill copied into <project>/.claude/skills/.
        # The PROJECT's repository encloses it; the next copy overwrites it.
        project = self.tmp / "project"
        git_repository(project)
        base = make_skill_copy(project, vendored=True)
        before = sorted(p.name for p in (base / "lessons").iterdir())
        code, out, err = run_copy(base, *LESSON, env=self.env)
        self.assertEqual(code, 0, err)
        self.assertEqual(sorted(p.name for p in (base / "lessons").iterdir()), before,
                         "a vendored copy is overwritten by the next copy; never write into it")
        self.assertEqual(len(list((self.home / "outbox" / "lessons").glob("*.md"))), 1)
        self.assertIn("not a git checkout", out)
        self.assertIn("github.com/TarikAI/adminwright", out)

    def test_an_explicit_lessons_dir_is_honoured(self):
        base = make_skill_copy(self.tmp / "skill")
        target = self.tmp / "clone" / "lessons"
        code, _out, err = run_copy(base, *LESSON, "--lessons-dir", target, env=self.env)
        self.assertEqual(code, 0, err)
        self.assertTrue(list(target.glob("0001-*.md")))
        self.assertFalse((self.home / "outbox").exists())

    @unittest.skipUnless(HAVE_GIT, "git is required to make a checkout")
    def test_a_checkout_writes_into_lessons(self):
        base = make_skill_copy(self.tmp / "skill", with_git=True)
        code, _out, err = run_copy(base, *LESSON, env=self.env)
        self.assertEqual(code, 0, err)
        self.assertTrue(list((base / "lessons").glob("0006-*.md")))

    def test_an_empty_git_directory_does_not_make_a_checkout(self):
        base = make_skill_copy(self.tmp / "skill")
        (base / ".git").mkdir()
        code, out, err = run_copy(base, *LESSON, env=self.env)
        self.assertEqual(code, 0, err)
        self.assertIn("not a git checkout", out)
        self.assertFalse(list((base / "lessons").glob("0006-*.md")))


class TestVersion(unittest.TestCase):
    """4.2 -- staleness must be visible in every transcript."""

    def test_version_flag_prints_the_commit_and_ruleset(self):
        code, out, _err = run("--version")
        self.assertEqual(code, 0)
        self.assertRegex(out.strip(), r"^adminwright \S+ \(ruleset [0-9a-f]{8}\)$")

    def test_a_plugin_cache_reports_its_sha_directory(self):
        tmp = Path(tempfile.mkdtemp(prefix="adminwright-ver-"))
        self.addCleanup(shutil.rmtree, str(tmp), ignore_errors=True)
        base = make_skill_copy(tmp, cache_path=True)
        code, out, _err = run_copy(base, "--version")
        self.assertEqual(code, 0)
        self.assertIn("adminwright 0123abcdef01", out)

    def test_a_bare_copy_reports_unknown(self):
        tmp = Path(tempfile.mkdtemp(prefix="adminwright-ver-"))
        self.addCleanup(shutil.rmtree, str(tmp), ignore_errors=True)
        base = make_skill_copy(tmp / "skill")
        _code, out, _err = run_copy(base, "--version")
        self.assertIn("adminwright unknown", out)

    @unittest.skipUnless(HAVE_GIT, "git is required to make a project repository")
    def test_a_vendored_copy_never_reports_the_projects_commit(self):
        tmp = Path(tempfile.mkdtemp(prefix="adminwright-ver-"))
        self.addCleanup(shutil.rmtree, str(tmp), ignore_errors=True)
        project_head = git_repository(tmp / "project")
        base = make_skill_copy(tmp / "project", vendored=True)
        _code, out, _err = run_copy(base, "--version")
        self.assertIn("adminwright unknown", out)
        self.assertNotIn(project_head, out, "the project's HEAD is not the skill's version")
        # Every write stamps validatedWith.skill; it must not churn with project commits.
        root = tmp / "console"
        code, _out, err = run_copy(base, "init", "--project-root", root, "--name", "Vendored")
        self.assertEqual(code, 0, err)
        manifest = json.loads((root / ".admin-console" / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["validatedWith"]["skill"], "unknown")

    @unittest.skipUnless(HAVE_GIT, "git is required to make a home repository")
    def test_a_plugin_cache_under_a_git_tracked_home_reports_its_sha_directory(self):
        tmp = Path(tempfile.mkdtemp(prefix="adminwright-ver-"))
        self.addCleanup(shutil.rmtree, str(tmp), ignore_errors=True)
        home_head = git_repository(tmp / "home")
        base = make_skill_copy(tmp / "home" / ".claude", cache_path=True)
        _code, out, _err = run_copy(base, "--version")
        self.assertIn("adminwright 0123abcdef01", out)
        self.assertNotIn(home_head, out)

    @unittest.skipUnless(HAVE_GIT, "git is required to make a checkout")
    def test_a_checkout_reports_its_own_head(self):
        tmp = Path(tempfile.mkdtemp(prefix="adminwright-ver-"))
        self.addCleanup(shutil.rmtree, str(tmp), ignore_errors=True)
        base = make_skill_copy(tmp / "skill")
        head = git_repository(base)
        _code, out, _err = run_copy(base, "--version")
        self.assertIn("adminwright " + head, out)

    def test_ruleset_changes_when_a_severity_changes(self):
        before = acm.ruleset_version()
        saved = acm.RULES["plan-note"]
        try:
            acm.RULES["plan-note"] = acm.E3
            self.assertNotEqual(acm.ruleset_version(), before)
        finally:
            acm.RULES["plan-note"] = saved
        self.assertEqual(acm.ruleset_version(), before)


class LearningCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="adminwright-store-"))
        self.addCleanup(shutil.rmtree, str(self.tmp), ignore_errors=True)
        self.store = self.tmp / "store"
        self.env = {"ADMINWRIGHT_HOME": str(self.store)}

    def project(self, name, feedback, platform_id=None):
        root = self.tmp / (name.replace(" ", "_") + "-" + str(len(list(self.tmp.iterdir()))))
        root.mkdir()
        run("init", "--project-root", root, "--name", name, "--profile", "standard")
        manifest = root / ".admin-console" / "manifest.json"
        data = json.loads(manifest.read_text(encoding="utf-8"))
        if platform_id:
            data["platform"]["id"] = platform_id
        data["feedback"] = [
            {"id": "fb." + str(i), "observation": obs, "category": category,
             "proposedChange": change, "evidence": [], "status": "open",
             "scope": "references/security-governance.md"}
            for i, (category, obs, change) in enumerate(feedback, 1)
        ]
        manifest.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return manifest

    def harvest(self, manifest, expect=0):
        code, out, err = run("harvest", "--manifest", manifest, "--date", "2026-09-23",
                             env=self.env)
        self.assertEqual(code, expect, out + err)
        self.assertNotIn("Traceback", err)
        return out, err


class TestHarvestOrder(LearningCase):
    """4.3 -- feedback must not be marked promoted before the store write."""

    def assert_refused_and_still_open(self, manifest, home):
        code, out, err = run("harvest", "--manifest", manifest, "--date", "2026-09-23",
                             env={"ADMINWRIGHT_HOME": str(home)})
        self.assertEqual(code, 2, out + err)
        self.assertNotIn("Traceback", err)
        data = json.loads(manifest.read_text(encoding="utf-8"))
        self.assertEqual([f["status"] for f in data["feedback"]], ["open"],
                         "a failed store write must leave the feedback open")
        self.assertEqual([f.get("promotedHash") for f in data["feedback"]], [None])

    def test_an_unwritable_store_leaves_the_feedback_open(self):
        manifest = self.project("Alpha", [("gap", "obs one", "change one")])
        blocker = self.tmp / "not-a-directory"
        blocker.write_text("a regular file", encoding="utf-8")
        self.assert_refused_and_still_open(manifest, blocker)
        self.harvest(manifest)
        data = json.loads(manifest.read_text(encoding="utf-8"))
        self.assertEqual([f["status"] for f in data["feedback"]], ["promoted"])

    def test_a_store_under_a_regular_file_fails_the_write_not_the_precheck(self):
        # ADMINWRIGHT_HOME=<file> is refused before anything runs, so it cannot
        # tell store-first from manifest-first. <file>/sub passes that check and
        # fails at the store write itself -- which is the ordering under test.
        manifest = self.project("Alpha", [("gap", "obs one", "change one")])
        blocker = self.tmp / "not-a-directory"
        blocker.write_text("a regular file", encoding="utf-8")
        self.assert_refused_and_still_open(manifest, blocker / "sub")
        self.harvest(manifest)
        data = json.loads(manifest.read_text(encoding="utf-8"))
        self.assertEqual([f["status"] for f in data["feedback"]], ["promoted"])

    def test_a_read_only_observation_file_leaves_the_feedback_open(self):
        manifest = self.project("Alpha", [("gap", "obs one", "change one")])
        store = self.tmp / "read-only-store"
        store.mkdir()
        observations = store / "observations.jsonl"
        observations.write_text("", encoding="utf-8")
        os.chmod(str(observations), stat.S_IREAD)
        self.addCleanup(os.chmod, str(observations), stat.S_IREAD | stat.S_IWRITE)
        if os.access(str(observations), os.W_OK):
            self.skipTest("this account can write a read-only file (root?)")
        self.assert_refused_and_still_open(manifest, store)


class TestPromotionBar(LearningCase):
    """4.5 -- count distinct projects, key harvest safely, notice edits."""

    SHARED = ("gap", "Bulk action authorized once for the whole batch",
              "Require per-target authorization in bulk commands")

    def test_two_projects_with_one_name_are_still_two_projects(self):
        a = self.project("admin", [self.SHARED], platform_id="admin-aaaaaa")
        b = self.project("admin", [self.SHARED], platform_id="admin-bbbbbb")
        self.harvest(a)
        self.harvest(b)
        payload = json.loads(run("promote", "--json", env=self.env)[1])
        self.assertEqual(len(payload["candidates"]), 1)
        self.assertEqual(payload["candidates"][0]["projectCount"], 2)

    def test_one_project_renamed_is_still_one_project(self):
        manifest = self.project("Old Name", [self.SHARED], platform_id="stable-abcdef")
        self.harvest(manifest)
        data = json.loads(manifest.read_text(encoding="utf-8"))
        data["platform"]["name"] = "New Name"
        data["feedback"][0].update({"status": "open", "observation": self.SHARED[1] + " again"})
        data["feedback"][0].pop("promotedHash", None)
        manifest.write_text(json.dumps(data, indent=2), encoding="utf-8")
        self.harvest(manifest)
        payload = json.loads(run("promote", "--json", env=self.env)[1])
        self.assertEqual(payload["candidates"], [], "one project, however often it is named")

    def test_the_project_flag_renames_but_never_re_keys(self):
        # The reviewer's probe: harvest, re-open the entry, harvest again with
        # --project. One codebase must stay one project.
        manifest = self.project("AcmePortal", [self.SHARED], platform_id="acmeportal-f95c53")
        self.harvest(manifest)
        code, _out, err = run("set", "--manifest", manifest, "--path", "feedback[fb.1].status",
                              "--value", "open")
        self.assertEqual(code, 0, err)
        code, out, err = run("harvest", "--manifest", manifest, "--date", "2026-09-23",
                             "--project", "acme-portal", env=self.env)
        self.assertEqual(code, 0, out + err)
        payload = json.loads(run("promote", "--json", env=self.env)[1])
        self.assertEqual(payload["candidates"], [], "one codebase is one project")
        store_keys = {json.loads(line)["projectKey"] for line in
                      (self.store / "observations.jsonl").read_text(encoding="utf-8").splitlines()
                      if line.strip()}
        self.assertEqual(store_keys, {"acmeportal-f95c53"})

    def test_a_manifest_without_a_platform_id_is_counted_by_name_and_warns(self):
        manifest = self.project("Legacy", [self.SHARED])
        data = json.loads(manifest.read_text(encoding="utf-8"))
        del data["platform"]["id"]
        manifest.write_text(json.dumps(data, indent=2), encoding="utf-8")
        code, out, err = run("harvest", "--manifest", manifest, "--date", "2026-09-23",
                             "--project", "legacy-display", env=self.env)
        self.assertEqual(code, 0, out + err)
        self.assertIn("platform.id is missing", err)
        self.assertIn("--path platform.id", err)
        record = json.loads((self.store / "observations.jsonl").read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(record["projectKey"], "Legacy", "--project must not re-key a legacy project")
        self.assertEqual(record["project"], "legacy-display")
        code, out, _err = run("validate", "--manifest", manifest, "--phase", "plan")
        self.assertEqual(code, 0, out)
        self.assertIn("[platform-id-missing]", out)
        self.assertIn("--path platform.id", out)

    def test_a_manifest_with_a_platform_id_does_not_warn(self):
        manifest = self.project("Current", [self.SHARED], platform_id="current-abcdef")
        _code, out, _err = run("validate", "--manifest", manifest, "--phase", "plan")
        self.assertNotIn("platform-id-missing", out)
        _out, err = self.harvest(manifest)
        self.assertNotIn("platform.id is missing", err)

    def test_an_edited_entry_is_harvested_again(self):
        manifest = self.project("Alpha", [self.SHARED])
        out, _err = self.harvest(manifest)
        self.assertIn("Harvested 1", out)
        out, _err = self.harvest(manifest)
        self.assertIn("Harvested 0", out)
        data = json.loads(manifest.read_text(encoding="utf-8"))
        data["feedback"][0]["proposedChange"] = "Require per-target authorization AND a preview"
        manifest.write_text(json.dumps(data, indent=2), encoding="utf-8")
        out, _err = self.harvest(manifest)
        self.assertIn("Harvested 1", out)

    def test_promote_lists_each_clusters_members(self):
        a = self.project("Alpha", [self.SHARED])
        b = self.project("Beta", [self.SHARED])
        self.harvest(a)
        self.harvest(b)
        out = run("promote", env=self.env)[1]
        self.assertIn("Merged from 2 observation(s)", out)
        self.assertEqual(out.count("    - "), 2)


class TestClustering(unittest.TestCase):
    """4.7 -- unrelated findings must not be chained into one idea."""

    def obs(self, project, text, change=""):
        return {"project": project, "observation": text, "proposedChange": change,
                "category": "friction", "scope": "SKILL.md", "date": "2026-09-23",
                "fingerprint": acm.observation_fingerprint(
                    {"scope": "SKILL.md", "category": "friction", "proposedChange": text})}

    def test_a_shared_word_does_not_chain_unrelated_findings(self):
        # A~B and B~C under single-link although A and C share nothing at all.
        records = [
            self.obs("A", "Fix validation"),
            self.obs("B", "validation slow output"),
            self.obs("C", "slow output"),
        ]
        clusters = acm.cluster_observations(records)
        by_project = [sorted(r["project"] for r in c) for c in clusters]
        self.assertIn(["A"], by_project, "A shares only one word and must stay apart")
        self.assertLess(acm.observation_similarity(records[0], records[1]), acm.SIMILARITY_THRESHOLD)

    def test_short_notes_need_more_than_one_shared_word(self):
        a = self.obs("A", "audit logs missing")
        b = self.obs("B", "audit report layout")
        self.assertLess(acm.observation_similarity(a, b), acm.SIMILARITY_THRESHOLD)

    def test_the_same_finding_in_different_words_still_groups(self):
        records = [
            self.obs("A", "Audit modeled entities and screens but zero roles or capabilities; harvest never ran",
                     "roles and capabilities must exist before screens"),
            self.obs("B", "Build session modeled entities and screens but zero roles and capabilities; harvest never ran",
                     "phase ordering: roles and capabilities before screens"),
        ]
        self.assertEqual(len(acm.cluster_observations(records)), 1)

    def test_average_link_needs_the_cluster_not_one_member(self):
        # C resembles A (half its words) but shares nothing with B. Single-link
        # would follow A into the cluster; average-link asks about the cluster.
        records = [self.obs("A", "alpha bravo charlie delta echo foxtrot"),
                   self.obs("B", "alpha bravo charlie golf hotel india"),
                   self.obs("C", "delta echo foxtrot juliet kilo lima")]
        self.assertGreaterEqual(acm.observation_similarity(records[0], records[2]),
                                acm.SIMILARITY_THRESHOLD)
        self.assertEqual(acm.observation_similarity(records[1], records[2]), 0.0)
        sizes = sorted(len(c) for c in acm.cluster_observations(records))
        self.assertEqual(sizes, [1, 2])

    def groups(self, records):
        return sorted(sorted(r["project"] for r in c) for c in acm.cluster_observations(records))

    def assert_apart(self, records, first, second):
        for members in self.groups(records):
            self.assertFalse(first in members and second in members,
                             first + " and " + second + " were merged: " + str(self.groups(records)))

    # The reviewer's probes (audit 2026-09-23, re-checked by the round-1 verifier).

    def test_reviewer_probe_a_a_shared_complaint_is_not_a_shared_finding(self):
        records = [self.obs("P1", "Manifest validation failed on the audit event"),
                   self.obs("P2", "Manifest validation failed on screens")]
        self.assertEqual(self.groups(records), [["P1"], ["P2"]])

    def test_reviewer_probe_c_a_short_note_is_not_absorbed_by_a_paragraph(self):
        records = [
            self.obs("P1", "validator rejected screens"),
            self.obs("P2", "The implementer agent modelled the tenants entity and its screens before "
                           "the roles, and the validator rejected the manifest because the screens "
                           "referenced capabilities that did not exist yet; ordering guidance in "
                           "build-order is unclear"),
        ]
        self.assertEqual(self.groups(records), [["P1"], ["P2"]])

    def test_reviewer_probe_d_a_bridging_note_does_not_chain_two_findings(self):
        records = [
            self.obs("P1", "Bulk suspend authorized once per batch", "authorize each target in bulk commands"),
            self.obs("P2", "Bulk export authorized once per batch and audit event missing",
                     "authorize each target and emit audit events"),
            self.obs("P3", "Audit event missing on export screen", "emit audit events for exports"),
        ]
        self.assert_apart(records, "P1", "P3")

    def test_a_strong_bridge_does_not_chain_in_any_order(self):
        # P2 repeats both findings word for word: it matches P1 and P3 perfectly
        # while they share nothing. Average-link admitted P3 on mean(0, 1) = 0.5.
        texts = {"P1": "bulk suspend authorized once per batch",
                 "P2": "bulk suspend authorized once per batch; audit event missing on export screen",
                 "P3": "audit event missing on export screen"}
        for order in (("P1", "P2", "P3"), ("P2", "P1", "P3"), ("P3", "P2", "P1"), ("P2", "P3", "P1")):
            records = []
            for day, project in enumerate(order, 1):
                record = self.obs(project, texts[project])
                record["date"] = "2026-09-%02d" % day
                records.append(record)
            self.assert_apart(records, "P1", "P3")

    def test_two_short_notes_sharing_a_bigram_stay_apart(self):
        records = [self.obs("P1", "audit log export missing filters"),
                   self.obs("P2", "audit log retention too short")]
        self.assertEqual(self.groups(records), [["P1"], ["P2"]])

    def test_identical_wording_always_groups_however_short(self):
        records = [self.obs("A", "Fix validation"), self.obs("B", "Fix validation")]
        self.assertEqual(self.groups(records), [["A", "B"]])

    def test_a_larger_cluster_admits_a_record_that_matches_a_majority(self):
        members = [self.obs("A1", "alpha bravo charlie delta echo foxtrot"),
                   self.obs("A2", "alpha bravo charlie delta echo golf"),
                   self.obs("A3", "alpha bravo charlie delta echo hotel"),
                   self.obs("A4", "alpha bravo charlie india juliet kilo")]
        joins = self.obs("E", "delta echo foxtrot golf hotel lima")          # A1-A3, not A4
        stays = self.obs("F", "delta echo foxtrot mike november oscar")      # A1 only
        self.assertEqual(self.groups(members + [joins]), [["A1", "A2", "A3", "A4", "E"]])
        self.assertEqual(self.groups(members + [stays]), [["A1", "A2", "A3", "A4"], ["F"]])


@unittest.skipUnless(HAVE_GIT, "git is required for store sync")
class TestStoreSync(unittest.TestCase):
    """4.4 -- a sync must never lose an observation on any device or on the remote."""

    def git(self, *args, cwd=None):
        done = subprocess.run(["git"] + list(args), cwd=str(cwd) if cwd else None,
                              capture_output=True, text=True, encoding="utf-8", errors="replace")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        return done.stdout

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="adminwright-sync-"))
        self.addCleanup(shutil.rmtree, str(self.tmp), ignore_errors=True)
        self.remote = self.tmp / "remote.git"
        self.git("init", "--bare", "-q", str(self.remote))
        self.a = self.tmp / "a"
        self.b = self.tmp / "b"
        for store in (self.a, self.b):
            code, out, err = run("store", "init", "--store", store, "--remote", self.remote)
            self.assertEqual(code, 0, out + err)

    def record(self, ident):
        # The shape of a harvested record, as the verifier's sync harness wrote it.
        return {"project": ident.split(":")[0], "category": "gap", "observation": "obs " + ident,
                "proposedChange": "change " + ident, "scope": "SKILL.md", "evidence": [],
                "date": "2026-09-23", "id": ident, "fingerprint": ident.replace(":", "")}

    def append(self, store, ids, compact=False):
        """Append the way a harvest does; `compact` is the separators older tools wrote."""
        with open(str(store / "observations.jsonl"), "a", encoding="utf-8", newline="\n") as handle:
            for ident in ids:
                if compact:
                    handle.write(json.dumps(self.record(ident), separators=(",", ":")) + "\n")
                else:
                    handle.write(json.dumps(self.record(ident)) + "\n")

    def ids(self, text):
        return {json.loads(line)["id"] for line in text.splitlines() if line.strip()}

    def store_ids(self, store):
        return self.ids((store / "observations.jsonl").read_bytes().decode("utf-8"))

    def sync(self, store):
        code, out, err = run("store", "sync", "--store", store, "--date", "2026-09-23")
        self.assertEqual(code, 0, out + err)
        self.assertNotIn("Traceback", err)

    def remote_ids(self):
        return self.ids(self.git("show", "main:observations.jsonl", cwd=self.remote))

    def test_the_same_records_in_a_different_order_are_never_deleted(self):
        """Seed 126 of the verifier's 3-device harness, shrunk to the steps that lose data.

        Device C harvests two records (compact lines, as older tools wrote them)
        and syncs. Then A and C both harvest S2:fb0 -- one project's feedback seen
        from two devices -- each at a different position among its own records.
        At d4e6f27 the following rounds of syncs deleted D0:fb2, S2:fb0 and S2:fb2
        from every store and from the remote: the line merge re-applied one
        device's ordering as a deletion of the other's records.
        """
        c = self.tmp / "c"
        code, out, err = run("store", "init", "--store", c, "--remote", self.remote)
        self.assertEqual(code, 0, out + err)
        self.append(c, ["S0:fb2", "S2:fb1"], compact=True)
        self.sync(c)
        self.append(self.a, ["S2:fb2", "D0:fb2", "S2:fb0"])
        self.append(c, ["S2:fb0", "D2:fb7"])
        everything = {"S0:fb2", "S2:fb1", "S2:fb2", "D0:fb2", "S2:fb0", "D2:fb7"}
        for _round in range(2):
            for store in (self.a, self.b, c):
                self.sync(store)
        # Ids first: a byte mismatch (line endings, key order) must never mask
        # which records went missing, or where.
        for name, store in (("a", self.a), ("b", self.b), ("c", c)):
            self.assertEqual(self.store_ids(store), everything, "store " + name + " lost records")
        self.assertEqual(self.remote_ids(), everything, "the remote lost records")
        self.assertEqual(len({(s / "observations.jsonl").read_bytes() for s in (self.a, self.b, c)}), 1,
                         "every device must hold identical bytes")

    def test_interleaved_appends_converge_to_identical_bytes(self):
        seen = set()
        for round_number in range(3):
            self.append(self.a, ["p1:a" + str(round_number), "shared:s" + str(round_number)])
            self.append(self.b, ["p2:b" + str(round_number), "shared:s" + str(round_number)])
            seen |= {"p1:a" + str(round_number), "p2:b" + str(round_number),
                     "shared:s" + str(round_number)}
            for store in (self.a, self.b, self.a):
                self.sync(store)
        self.sync(self.a)
        self.sync(self.b)
        self.sync(self.a)
        self.assertEqual(self.store_ids(self.a), seen)
        self.assertEqual(self.store_ids(self.b), seen)
        self.assertEqual(self.remote_ids(), seen)
        text_a = (self.a / "observations.jsonl").read_bytes()
        text_b = (self.b / "observations.jsonl").read_bytes()
        self.assertEqual(text_a, text_b, "both devices must hold identical bytes")

    def test_only_the_store_files_are_committed(self):
        self.append(self.a, ["p1:x1"])
        (self.a / "unrelated-secret.txt").write_text("do not push me", encoding="utf-8")
        self.sync(self.a)
        tracked = self.git("ls-tree", "-r", "--name-only", "main", cwd=self.remote)
        self.assertNotIn("unrelated-secret.txt", tracked)
        self.assertNotIn("salt", tracked.split())

    def test_sync_takes_the_store_lock(self):
        self.append(self.a, ["p1:x1"])
        lock = self.a / ".lock"
        lock.write_text("pid=1\n", encoding="utf-8")
        past = 1
        os.utime(str(lock), (past, past))  # stale: sync may take it over
        self.sync(self.a)
        self.assertFalse(lock.exists(), "the lock must be released when the sync ends")


class TestFeedbackScope(TempProject):
    """4.6 -- scope drives clustering; an unscoped entry can never group."""

    FEEDBACK = {"id": "fb-001", "observation": "o", "category": "gap",
                "proposedChange": "c", "evidence": []}

    def test_add_requires_a_scope(self):
        code, _o, err = self.cli("add", "--kind", "feedback", "--json", json.dumps(self.FEEDBACK))
        self.assertEqual(code, 2)
        self.assertIn("needs a scope", err)

    def test_valid_scopes_are_accepted(self):
        for index, scope in enumerate(("SKILL.md", "references/verification.md", "scripts",
                                       "agents", "scripts/code_review.py")):
            entry = dict(self.FEEDBACK, id="fb-00" + str(index), scope=scope)
            code, _o, err = self.cli("add", "--kind", "feedback", "--json", json.dumps(entry))
            self.assertEqual(code, 0, scope + err)

    def test_an_invalid_scope_is_refused(self):
        for scope in ("somewhere", "docs/readme.md", "../SKILL.md"):
            entry = dict(self.FEEDBACK, scope=scope)
            code, _o, _err = self.cli("add", "--kind", "feedback", "--json", json.dumps(entry))
            self.assertEqual(code, 2, scope)

    def test_an_existing_unscoped_entry_warns(self):
        data = self.read()
        data["feedback"].append(dict(self.FEEDBACK, status="open"))
        self.write(data)
        code, out, _err = self.validate(phase="plan")
        self.assertEqual(code, 0)
        self.assertIn("[feedback-scope]", out)

    def test_init_writes_a_stable_platform_id(self):
        first = self.read()["platform"]["id"]
        self.assertRegex(first, r"^testplatform-[0-9a-f]{6}$")


class TestLessonFormat(unittest.TestCase):
    """4.8 -- `lesson add` must emit exactly the format lessons/README.md documents."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="adminwright-lessons-"))
        self.addCleanup(shutil.rmtree, str(self.tmp), ignore_errors=True)
        self.lessons = self.tmp / "lessons"
        shutil.copytree(str(SKILL_ROOT / "lessons"), str(self.lessons))

    def add(self, *extra, **kwargs):
        args = list(LESSON) + list(extra)
        return run(*args, "--lessons-dir", self.lessons, **kwargs)

    def newest(self):
        return sorted(self.lessons.glob("[0-9][0-9][0-9][0-9]-*.md"))[-1]

    def test_output_has_the_documented_frontmatter_and_five_headings(self):
        code, _o, err = self.add("--platform", "b2b-saas", "--evidence", "Project A: gaps[] id gap.x")
        self.assertEqual(code, 0, err)
        text = self.newest().read_text(encoding="utf-8")
        front = text.split("---")[1]
        fields = dict(line.split(": ", 1) for line in front.strip().splitlines())
        self.assertEqual(list(fields), ["id", "title", "date", "category", "scope", "status",
                                        "confidence", "platforms"])
        self.assertEqual(fields["id"], '"0006"')
        self.assertEqual(fields["confidence"], "observed-once")
        self.assertEqual(fields["status"], "proposed")
        self.assertEqual(json.loads(fields["platforms"]), ["b2b-saas"])
        headings = re.findall(r"^#{1,6} .*$", text.split("---", 2)[2], re.M)
        self.assertEqual(headings, ["## Trigger", "## Rule", "## Evidence", "## Proposed edit",
                                    "## Review notes"])

    def test_vocabularies_are_enforced(self):
        code, _o, err = run(*[a if a != "gap" else "banana" for a in LESSON],
                            "--lessons-dir", self.lessons)
        self.assertEqual(code, 2)
        self.assertIn("banana", err)
        code, _o, _err = self.add("--confidence", "high")
        self.assertEqual(code, 2)
        code, _o, _err = run(*[a if a != "references/security-governance.md" else "references/nope.md"
                               for a in LESSON], "--lessons-dir", self.lessons)
        self.assertEqual(code, 2)
        code, _o, err = self.add("--status", "adopted")
        self.assertEqual(code, 2)
        self.assertIn("separate reviewed edit", err)

    def test_a_multiline_rule_cannot_corrupt_the_frontmatter_or_add_headings(self):
        args = [a if a != "Rotate first." else "First line.\n## Injected heading\nkey: value" for a in LESSON]
        code, _o, err = run(*args, "--lessons-dir", self.lessons)
        self.assertEqual(code, 0, err)
        text = self.newest().read_text(encoding="utf-8")
        self.assertEqual(len(re.findall(r"^## ", text, re.M)), 5)
        self.assertNotIn("Injected", text.split("---")[1])

    def test_the_index_is_rebuilt_from_the_files_with_links_and_guidance_level(self):
        self.add()
        index = (self.lessons / "index.md").read_text(encoding="utf-8")
        self.assertIn("Guidance level: 0005", index)
        self.assertIn("[0006](0006-rotate-keys-before-export.md)", index)
        self.assertIn("[0001](0001-example-lesson.md)", index)
        code, out, _err = run("lesson", "index", "--lessons-dir", self.lessons)
        self.assertEqual(code, 0)
        self.assertIn("guidance level 0005", out)

    def test_two_parallel_adds_get_distinct_ids_and_two_index_rows(self):
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        procs = []
        for title in ("First parallel lesson", "Second parallel lesson"):
            args = [a if a != "Rotate keys before export" else title for a in LESSON]
            procs.append(subprocess.Popen(
                [sys.executable, str(SCRIPT)] + [str(a) for a in args] + ["--lessons-dir", str(self.lessons)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env))
        for proc in procs:
            _out, err = proc.communicate()
            self.assertEqual(proc.returncode, 0, err.decode("utf-8", "replace"))
        files = sorted(p.name[:4] for p in self.lessons.glob("[0-9][0-9][0-9][0-9]-*.md"))
        self.assertEqual(len(files), len(set(files)))
        self.assertEqual(files[-2:], ["0006", "0007"])
        index = (self.lessons / "index.md").read_text(encoding="utf-8")
        self.assertIn("parallel", index.lower())
        self.assertEqual(index.count("| [0006]") + index.count("| [0007]"), 2)

    def test_every_shipped_lesson_conforms_to_the_format(self):
        for path in sorted((SKILL_ROOT / "lessons").glob("[0-9][0-9][0-9][0-9]-*.md")):
            text = path.read_text(encoding="utf-8")
            fields = acm.read_lesson_frontmatter(path)
            self.assertEqual(fields.get("id"), path.name[:4], path.name)
            self.assertIn(fields.get("confidence"), acm.LESSON_CONFIDENCES, path.name)
            self.assertIn(fields.get("category"), acm.FEEDBACK_CATEGORIES, path.name)
            self.assertTrue((SKILL_ROOT / fields["scope"]).is_file(), path.name)
            body = text.split("---", 2)[2]
            headings = re.findall(r"^## (.*)$", body, re.M)
            self.assertEqual(headings, ["Trigger", "Rule", "Evidence", "Proposed edit", "Review notes"],
                             path.name)
            self.assertNotIn("\ntrigger:", text.split("---")[1], path.name)
            if fields.get("status") == "adopted":
                proposed = body.split("## Proposed edit", 1)[1].split("## Review notes", 1)[0]
                self.assertTrue(proposed.strip(), path.name + ": adopted needs a Proposed edit")
                self.assertTrue(fields.get("platforms"), path.name + ": adopted needs platforms")
                evidence = body.split("## Evidence", 1)[1].split("## Proposed edit", 1)[0]
                self.assertNotIn("none recorded", evidence, path.name + ": adopted needs evidence")

    def test_the_shipped_index_matches_a_rebuild(self):
        rebuilt = self.tmp / "rebuilt"
        shutil.copytree(str(SKILL_ROOT / "lessons"), str(rebuilt))
        run("lesson", "index", "--lessons-dir", rebuilt)
        self.assertEqual((rebuilt / "index.md").read_text(encoding="utf-8"),
                         (SKILL_ROOT / "lessons" / "index.md").read_text(encoding="utf-8"),
                         "run `lesson index` and commit the result")


class TestPrivacy(unittest.TestCase):
    """4.9 -- what the docs promise about export must be what the code does."""

    def test_scrub_covers_relative_paths_new_tlds_and_common_token_shapes(self):
        cases = {
            "see src/acme/billing/refund.ts": "src/acme",
            "host billing.acme.dev is down": "acme.dev",
            "see api.acme-corp.xyz": "acme-corp",
            "key AKIAIOSFODNN7EXAMPLE leaked": "AKIA",
            "token ghp_" + "a" * 36: "ghp_",
            "jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijk": "eyJ",
        }
        for text, forbidden in cases.items():
            self.assertNotIn(forbidden, acm.scrub_text(text), text)

    def test_fingerprints_are_salted_per_store(self):
        self.assertNotEqual(acm.project_fingerprint("Acme", "salt-one"),
                            acm.project_fingerprint("Acme", "salt-two"))
        self.assertNotEqual(acm.project_fingerprint("Acme", "salt-one"),
                            acm.project_fingerprint("Acme"))
        self.assertEqual(acm.project_fingerprint("Acme", "s"), acm.project_fingerprint("Acme", "s"))

    def test_the_store_init_readme_does_not_overpromise(self):
        tmp = Path(tempfile.mkdtemp(prefix="adminwright-priv-"))
        self.addCleanup(shutil.rmtree, str(tmp), ignore_errors=True)
        if not HAVE_GIT:
            self.skipTest("git needed for store init")
        store = tmp / "store"
        code, _o, err = run("store", "init", "--store", store)
        self.assertEqual(code, 0, err)
        readme = (store / "README.md").read_text(encoding="utf-8")
        self.assertIn("store sync", readme)
        self.assertTrue((store / "salt").exists())
        self.assertIn("salt", (store / ".gitignore").read_text(encoding="utf-8"))

    def test_shipped_files_name_no_real_projects(self):
        for path in list((SKILL_ROOT / "lessons").glob("*.md")) + [SKILL_ROOT / "tests" / "test_manifest.py"]:
            text = path.read_text(encoding="utf-8")
            # built from pieces so this file does not itself name the projects
            for name in ("Ro" + "ya", "AIV" + "ORA", "Zenith" + "APP"):
                self.assertNotRegex(text, r"\b" + name + r"\b", path.name)


if __name__ == "__main__":
    unittest.main()
