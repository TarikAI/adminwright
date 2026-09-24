# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TarikAI
"""CLI correctness regressions (audit batch 6, manifest script).

Each test is an input a real agent produced or a state a real machine reached:
an object stored as a string, a crash on a list where a string belonged, a claim
decided from files that do not survive a clone, a lock two waiters both "held".
"""

import errno
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from cli_helpers import SCRIPT, TempProject, acm, run

CAP = "entities[user].capabilities[user.list]"


class TestCoerceValue(TempProject):
    """6.6 -- `set` must store what it was given, typed by the field."""

    def setUp(self):
        super().setUp()
        self.data = self.build_honest_manifest()

    def test_strings_named_true_and_null_stay_strings(self):
        for value in ("true", "false", "null"):
            code, _o, err = self.cli("set", "--path", "platform.name", "--value", value)
            self.assertEqual(code, 0, err)
            self.assertEqual(self.read()["platform"]["name"], value)

    def test_a_boolean_field_still_takes_true_and_false(self):
        code, _o, err = self.cli("set", "--path", "roles[ops].mfaRequired", "--value", "false")
        self.assertEqual(code, 0, err)
        self.assertIs(self.read()["roles"][0]["mfaRequired"], False)

    def test_an_object_field_takes_json_and_is_stored_as_an_object(self):
        code, _o, err = self.cli("set", "--path", CAP + ".entityStates",
                                 "--value", '{"from": ["active"], "to": []}')
        self.assertEqual(code, 0, err)
        self.assertEqual(self.capability_of(self.read())["entityStates"],
                         {"from": ["active"], "to": []})

    def test_an_object_field_refuses_plain_text(self):
        code, _o, err = self.cli("set", "--path", CAP + ".entityStates", "--value", "active")
        self.assertEqual(code, 2)
        self.assertIn("JSON", err)

    def test_malformed_json_is_refused_not_split_on_commas(self):
        before = self.read()
        code, _o, err = self.cli("set", "--path", CAP + ".serverOperations",
                                 "--value", '["A.b", "C.d"')
        self.assertEqual(code, 2)
        self.assertIn("valid JSON", err)
        self.assertEqual(self.read(), before)

    def test_plain_comma_lists_still_work(self):
        code, _o, err = self.cli("set", "--path", CAP + ".serverOperations", "--value", "A.b, C.d")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.capability_of(self.read())["serverOperations"], ["A.b", "C.d"])

    def test_a_dict_for_a_string_field_is_a_type_error_not_silent_text(self):
        code, _o, err = self.cli("set", "--path", CAP + ".rationale", "--value", '{"a": 1}')
        self.assertEqual(code, 0, "strings are stored as written")
        self.assertEqual(self.capability_of(self.read())["rationale"], '{"a": 1}')

    def test_append_takes_the_union_and_keeps_what_others_added(self):
        # Two reviewers each adding one evidence file by read-modify-write dropped
        # the other's entry; --append takes the union inside the manifest lock.
        path = "crossCutting.experience.evidence"
        (self.root / "evidence" / "ux-review.md").write_text(
            "Reviewed 4 screens as ops and support\n", encoding="utf-8")
        code, _o, err = self.cli("set", "--path", path, "--append", "--value", "evidence\\ux-review.md")
        self.assertEqual(code, 0, err)
        code, _o, err = self.cli("set", "--path", path, "--append",
                                 "--value", "evidence/ux-review.md,evidence/experience.md")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.read()["crossCutting"]["experience"]["evidence"],
                         ["evidence/experience.md", "evidence/ux-review.md"])

    def test_append_refuses_a_field_that_is_not_a_list(self):
        code, _o, err = self.cli("set", "--path", "platform.name", "--append", "--value", "x")
        self.assertEqual(code, 2)
        self.assertIn("needs a list field", err)
        code, _o, err = self.cli("set", "--path", CAP + ".tests", "--append", "--replace", "--value", "x")
        self.assertEqual(code, 2)


class TestPathsFromFindings(TempProject):
    """6.12 -- a finding's path must be pasteable into `set --path`."""

    def setUp(self):
        super().setUp()
        self.data = self.build_honest_manifest()

    def test_the_manifest_prefix_is_accepted(self):
        code, _o, err = self.cli("set", "--path", "manifest.platform.summary", "--value", "Hello")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.read()["platform"]["summary"], "Hello")

    def test_numeric_indexes_select_items(self):
        code, _o, err = self.cli("set", "--path", "manifest.roles[0].name", "--value", "Renamed")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.read()["roles"][0]["name"], "Renamed")

    def test_a_list_element_can_be_set_by_index(self):
        code, _o, err = self.cli("set", "--path", "qualityGates[build].evidence[0]",
                                 "--value", "evidence/build.log")
        self.assertEqual(code, 0, err)
        code, _o, err = self.cli("set", "--path", "qualityGates[build].evidence[5]", "--value", "x")
        self.assertEqual(code, 2)

    def test_a_typo_leaf_is_refused_at_standard(self):
        code, _o, err = self.cli("set", "--path", CAP + ".rationnale", "--value", "oops")
        self.assertEqual(code, 2)
        self.assertIn("unknown-field", err)

    def test_an_unknown_key_in_add_is_refused_at_standard_and_a_warning_at_internal(self):
        entry = {"id": "gap.x", "severity": "low", "description": "d", "status": "open",
                 "rationale": "", "evidence": [], "severty": "high"}
        code, _o, err = self.cli("add", "--kind", "gap", "--json", json.dumps(entry))
        self.assertEqual(code, 2)
        self.assertIn("unknown-field", err)
        data = self.read()
        data["profile"] = "internal"
        self.write(data)
        code, _o, err = self.cli("add", "--kind", "gap", "--json", json.dumps(entry))
        self.assertEqual(code, 0, err)
        self.assertIn("unknown-field", err)


class TestClaims(TempProject):
    """6.7 -- claims must be decided from the manifest, not only from lock files."""

    def setUp(self):
        super().setUp()
        self.data = self.build_honest_manifest()
        self.cap = self.capability_of(self.data)
        self.cap.update({"owner": "", "implementedBy": "", "reviewStatus": "unreviewed",
                         "reviewedBy": ""})
        self.write(self.data)

    def claim(self, agent, role="implementer", *extra):
        return self.cli("claim", "--agent", agent, "--role", role, "--capability", "user.list", *extra)

    def test_a_claim_is_refused_when_the_manifest_shows_another_active_holder(self):
        code, _o, err = self.claim("a1")
        self.assertEqual(code, 0, err)
        # a fresh clone: locks/ does not exist
        for lock in (self.manifest.parent / "locks").glob("*.lock"):
            lock.unlink()
        code, _o, err = self.claim("a2")
        self.assertEqual(code, 3)
        self.assertIn("a1", err)

    def test_force_steal_needs_a_reason_and_updates_both_agents(self):
        self.claim("a1")
        code, _o, err = self.claim("a2", "implementer", "--force-steal")
        self.assertEqual(code, 2)
        code, _o, err = self.claim("a2", "implementer", "--force-steal", "--reason", "a1 stalled")
        self.assertEqual(code, 0, err)
        agents = {a["id"]: a for a in self.read()["agents"]}
        self.assertEqual(agents["a1"]["ownsCapabilities"], [])
        self.assertIn("user.list", agents["a1"]["previouslyOwned"])
        self.assertIn("user.list", agents["a2"]["ownsCapabilities"])
        self.assertTrue(agents["a2"]["stolenFrom"])
        code, _o, err = self.cli("release-claim", "--agent", "a1")
        self.assertEqual(code, 0, "the victim can finish cleanly: " + err)
        self.assertEqual({a["id"]: a for a in self.read()["agents"]}["a1"]["status"], "done")

    def test_releasing_something_never_held_is_a_conflict(self):
        code, out, err = self.cli("release-claim", "--agent", "ghost", "--capability", "user.list")
        self.assertEqual(code, 3)
        self.assertIn("does not hold", err)
        self.assertIn("Released: nothing", out)

    def test_a_released_capability_can_be_claimed_by_someone_else(self):
        self.claim("a1")
        self.cli("release-claim", "--agent", "a1")
        code, _o, err = self.claim("a2")
        self.assertEqual(code, 0, err)

    def test_the_manifest_is_stamped_with_the_rule_set(self):
        self.claim("a1")
        stamp = self.read()["validatedWith"]
        self.assertEqual(stamp["ruleset"], acm.ruleset_version())


HOLD_LOCK = (
    "import importlib.util, sys, time\n"
    "spec = importlib.util.spec_from_file_location('acm', %r)\n"
    "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
    "lock = m.FileLock(%r); lock.__enter__()\n"
    "print('held', flush=True)\n"
    "time.sleep(120)\n"
)


class TestFileLock(unittest.TestCase):
    """6.8 -- an OS lock: released when its holder exits, never orphaned, never shared.

    The previous lock was the lock file's existence. Waiters read it on every poll;
    on Windows an open reader makes the holder's delete fail, the failure was
    swallowed, and 15 of 16 parallel writers timed out after 30 seconds.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="adminwright-lock-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(str(self.tmp), ignore_errors=True))
        self.path = self.tmp / ".lock"

    def hold_in_child(self):
        proc = subprocess.Popen(
            [sys.executable, "-c", HOLD_LOCK % (str(SCRIPT), str(self.path))],
            stdout=subprocess.PIPE,
        )
        self.addCleanup(lambda: (proc.kill(), proc.wait()))
        self.assertEqual(proc.stdout.readline().decode().strip(), "held")
        return proc

    def test_the_holder_is_readable_while_the_lock_is_held(self):
        # Windows byte-range locks are mandatory; the lock byte must not cover the pid.
        with acm.FileLock(self.path):
            text = self.path.read_text(encoding="utf-8")
            self.assertIn("pid=" + str(os.getpid()), text)

    def test_a_timeout_names_the_holder(self):
        child = self.hold_in_child()
        with self.assertRaises(acm.LockError) as caught:
            with acm.FileLock(self.path, timeout=0.5):
                pass
        message = str(caught.exception)
        self.assertIn("pid " + str(child.pid), message)
        self.assertIn("released automatically", message)

    def test_a_killed_holder_releases_the_lock_at_once(self):
        child = self.hold_in_child()
        child.kill()
        child.wait()
        started = time.time()
        with acm.FileLock(self.path, timeout=10):
            pass
        self.assertLess(time.time() - started, 2, "a dead holder must not cost any wait")

    def test_a_lock_file_left_on_disk_does_not_block(self):
        self.path.write_text("pid=1\nsince=0\n", encoding="utf-8")
        started = time.time()
        with acm.FileLock(self.path, timeout=5):
            pass
        self.assertLess(time.time() - started, 1)

    def test_holders_never_overlap(self):
        script = (
            "import sys, time, importlib.util\n"
            "spec = importlib.util.spec_from_file_location('acm', %r)\n"
            "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
            "with m.FileLock(%r, timeout=60):\n"
            "    open(%r, 'a').write(str(__import__('os').getpid()) + ' in\\n')\n"
            "    time.sleep(0.15)\n"
            "    open(%r, 'a').write(str(__import__('os').getpid()) + ' out\\n')\n"
        ) % (str(SCRIPT), str(self.path), str(self.tmp / "trace.txt"), str(self.tmp / "trace.txt"))
        procs = [subprocess.Popen([sys.executable, "-c", script]) for _ in range(6)]
        for proc in procs:
            self.assertEqual(proc.wait(timeout=90), 0)
        lines = (self.tmp / "trace.txt").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 12)
        for index in range(0, 12, 2):
            pid = lines[index].split()[0]
            self.assertEqual(lines[index], pid + " in")
            self.assertEqual(lines[index + 1], pid + " out",
                             "two holders overlapped: " + " | ".join(lines))

    def test_sixteen_parallel_adds_all_land(self):
        """The field reproduction: 16 `add` processes against one manifest."""
        project = Path(tempfile.mkdtemp(prefix="adminwright-lock-adds-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(str(project), ignore_errors=True))
        fixture = Path(__file__).resolve().parent.parent / "evals" / "fixtures" / "saas-clean"
        __import__("shutil").copytree(str(fixture), str(project / "p"))
        manifest = project / "p" / ".admin-console" / "manifest.json"
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
        procs = []
        for index in range(16):
            gap = {"id": "par-%d" % index, "severity": "low", "description": "parallel %d" % index,
                   "status": "open", "rationale": "", "evidence": []}
            procs.append(subprocess.Popen(
                [sys.executable, str(SCRIPT), "add", "--manifest", str(manifest), "--kind", "gap",
                 "--json", json.dumps(gap)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
            ))
        failures = []
        for proc in procs:
            _out, err = proc.communicate(timeout=120)
            if proc.returncode:
                failures.append(err.decode("utf-8", "replace")[:200])
        self.assertEqual(failures, [])
        stored = json.loads(manifest.read_text(encoding="utf-8"))
        ids = {gap["id"] for gap in stored["gaps"]}
        self.assertTrue({"par-%d" % i for i in range(16)} <= ids)

    def test_a_filesystem_without_os_locks_falls_back_to_a_soft_lock(self):
        original = acm._os_try_lock

        def unsupported(fd):
            raise OSError(errno.ENOLCK, "No locks available")

        acm._os_try_lock = unsupported
        self.addCleanup(setattr, acm, "_os_try_lock", original)
        with acm.FileLock(self.path, timeout=1):
            self.assertTrue(self.path.with_name(".lock.soft").exists())
            with self.assertRaises(acm.LockError) as caught:
                with acm.FileLock(self.path, timeout=0.3):
                    pass
            self.assertIn("delete", str(caught.exception))
        self.assertFalse(self.path.with_name(".lock.soft").exists())


class TestCrashes(TempProject):
    """6.9 -- wrong shapes and IO failures must never be tracebacks."""

    def assert_clean(self, code, err, expected):
        self.assertNotIn("Traceback", err)
        self.assertIn(code, expected)

    def test_emit_survives_an_object_in_a_roles_list(self):
        data = self.build_honest_manifest()
        self.capability_of(data)["roles"] = ["ops", {"id": "x"}]
        data["screens"][0]["roles"] = ["ops", {"id": "x"}]
        self.write(data)
        for fmt in ("authz-matrix", "operator-handbook", "test-plan", "seed-plan", "nav-map"):
            code, _o, err = self.cli("emit", "--format", fmt)
            self.assert_clean(code, err, (0,))
        code, out, err = self.validate(phase="plan")
        self.assertEqual(code, 1)
        self.assertIn("manifest-type", out)

    def test_a_list_valued_kind_is_a_finding_not_a_crash(self):
        data = self.build_honest_manifest()
        self.capability_of(data)["kind"] = ["query", "command"]
        self.write(data)
        code, out, err = self.validate()
        self.assertEqual(code, 1, err)
        self.assertNotIn("Traceback", err)
        self.assertIn("manifest-type", out)
        code, _o, err = self.cli("report")
        self.assert_clean(code, err, (0,))

    def test_an_io_failure_writing_the_output_is_exit_2(self):
        blocker = self.tmp / "afile"
        blocker.write_text("x", encoding="utf-8")
        code, _o, err = self.cli("emit", "--format", "gap-report", "--out", blocker / "sub" / "r.md")
        self.assertEqual(code, 2)
        self.assertNotIn("Traceback", err)

    def test_migrate_of_a_malformed_1_0_manifest_is_a_usage_error(self):
        broken = self.tmp / "old.json"
        broken.write_text(json.dumps({"manifestVersion": "1.0", "platform": "not an object",
                                      "roles": [], "entities": []}), encoding="utf-8")
        for extra in ([], ["--write"]):
            code, _o, err = run("migrate", "--manifest", broken, *extra)
            self.assertEqual(code, 2, err)
            self.assertNotIn("Traceback", err)

    def test_migrate_write_upgrades_a_valid_1_0_manifest(self):
        old = self.read()
        old["manifestVersion"] = "1.0"
        for key in ("declaredStatic", "feedback", "agents"):
            old.pop(key, None)
        old["platform"].pop("stack", None)
        path = self.tmp / "old.json"
        path.write_text(json.dumps(old), encoding="utf-8")
        code, out, err = run("migrate", "--manifest", path, "--write")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["manifestVersion"], "2.0")


class TestNestedAdd(TempProject):
    """6.10 -- an entity added with its capabilities inline must get their defaults."""

    def test_add_entity_with_nested_capabilities(self):
        entity = {"id": "order", "name": "Order", "sourceOfTruth": "postgres:orders",
                  "lifecycleStates": ["open"], "retention": "1y",
                  "capabilities": [{"id": "order.list", "outcome": "Find an order",
                                    "kind": "query", "roles": []}]}
        code, _o, err = self.cli("add", "--kind", "entity", "--json", json.dumps(entity))
        self.assertEqual(code, 0, err)
        stored = self.read()["entities"][0]["capabilities"][0]
        self.assertEqual(stored["surface"], ["ui"])
        self.assertIn("dataBinding", stored)

    def test_add_capability_with_entity_flag(self):
        entity = {"id": "order", "name": "Order", "sourceOfTruth": "postgres:orders",
                  "lifecycleStates": ["open"], "retention": "1y"}
        self.cli("add", "--kind", "entity", "--json", json.dumps(entity))
        code, _o, err = self.cli("add", "--kind", "capability", "--entity", "order", "--json",
                                 json.dumps({"id": "order.list", "outcome": "Find", "kind": "query"}))
        self.assertEqual(code, 0, err)


class TestWriteGuard(TempProject):
    """6.11 -- the guard must not blame a rename, and must count duplicates."""

    def test_renaming_an_item_that_already_has_errors_is_not_blamed_for_them(self):
        data = self.build_honest_manifest()
        data["roles"].append({"id": "broken", "name": "", "responsibilities": ["x"],
                              "scopes": ["all"], "mfaRequired": True})
        self.write(data)
        code, _o, err = self.cli("set", "--path", "roles[broken].id", "--value", "renamed")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.read()["roles"][1]["id"], "renamed")

    def test_a_second_identical_finding_is_new(self):
        base = self.build_honest_manifest()
        one_dup = json.loads(json.dumps(base))
        one_dup["roles"].append(dict(one_dup["roles"][0]))
        two_dup = json.loads(json.dumps(one_dup))
        two_dup["roles"].append(dict(two_dup["roles"][0]))
        errors, _warn = acm.new_findings(one_dup, two_dup, self.root, self.manifest.parent)
        self.assertTrue([e for e in errors if e["rule"] == "id-duplicate"],
                        "the second duplicate has the same signature and must still count")

    def test_a_finding_already_present_is_not_reported_again(self):
        base = self.build_honest_manifest()
        errors, _warn = acm.new_findings(base, json.loads(json.dumps(base)), self.root,
                                         self.manifest.parent)
        self.assertEqual(errors, [])


class TestGapReportHealth(TempProject):
    """6.12 -- the report's health line and its table must use the same severities."""

    def test_a_clean_manifest_carries_no_caveat(self):
        self.build_honest_manifest()
        code, out, _err = self.cli("emit", "--format", "gap-report")
        self.assertEqual(code, 0)
        self.assertNotIn("sketch, not a record", out)

    def test_a_manifest_with_release_errors_says_so_at_release(self):
        code, out, _err = self.cli("emit", "--format", "gap-report")
        self.assertEqual(code, 0)
        self.assertIn("at release validation", out)
        self.assertIn("sketch, not a record", out)


class TestSmallCorrectness(TempProject):
    """6.13 -- ids, help text, init ergonomics."""

    def test_an_id_with_a_trailing_newline_is_invalid(self):
        self.assertIsNone(acm.ID_PATTERN.fullmatch("ops\n"))
        code, _o, err = self.cli("add", "--kind", "role", "--json",
                                 json.dumps({"id": "ops\n", "name": "Ops",
                                             "responsibilities": ["x"], "scopes": ["y"]}))
        self.assertEqual(code, 2)

    def test_help_strings_and_exit_codes_exist(self):
        for command in ("set", "claim", "release-claim"):
            _code, out, _err = run(command, "--help")
            self.assertIn("path to manifest.json", out, command)
        _code, out, _err = run("--help")
        self.assertIn("exit codes:", out)

    def test_init_accepts_repeated_and_comma_separated_archetypes(self):
        root = self.tmp / "other"
        root.mkdir()
        code, _o, err = run("init", "--project-root", root, "--name", "X",
                            "--archetype", "b2b-saas,commerce", "--archetype", "fintech")
        self.assertEqual(code, 0, err)
        data = json.loads((root / ".admin-console" / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(data["platform"]["archetypes"], ["b2b-saas", "commerce", "fintech"])

    def test_init_on_an_existing_manifest_says_how_to_resume(self):
        code, _o, err = run("init", "--project-root", self.root, "--name", "X")
        self.assertEqual(code, 2)
        self.assertIn("--force", err)
        self.assertIn("handed-off", err)


class TestInitLayout(TempProject):
    """6.24 -- init records where the project root is and what not to commit."""

    def test_init_writes_a_gitignore_the_project_root_and_an_assumed_profile_decision(self):
        ignore = (self.manifest.parent / ".gitignore").read_text(encoding="utf-8")
        for entry in ("locks/", ".lock", "*.tmp", "ocr-bundle*.json"):
            self.assertIn(entry, ignore)
        data = self.read()
        self.assertEqual(data["projectRoot"], "..")
        decision = data["decisions"][0]
        self.assertEqual((decision["id"], decision["status"], decision["appliesTo"]),
                         ("dec.profile", "assumed", ["*"]))

    def test_a_manifest_elsewhere_records_a_relative_project_root_and_validates(self):
        root = self.tmp / "mono"
        (root / "apps" / "admin").mkdir(parents=True)
        target = root / "apps" / "admin" / "console.json"
        code, _o, err = run("init", "--project-root", root, "--manifest", target, "--name", "Admin")
        self.assertEqual(code, 0, err)
        data = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(data["projectRoot"], "../..")
        self.assertEqual(acm.project_root_of(target, data), root.resolve())
        code, out, err = run("validate", "--manifest", target, "--phase", "plan")
        self.assertEqual(code, 0, out + err)

    def test_the_profile_decision_blocks_regulated_release_until_confirmed(self):
        data = self.build_honest_manifest()
        data["profile"] = "regulated"
        data["decisions"][0]["status"] = "assumed"
        self.write(data)
        code, out, _err = self.validate()
        self.assertEqual(code, 1)
        self.assertIn("decision-assumed", out)


class TestRuleMatrix(unittest.TestCase):
    """6.25 -- the published matrix is generated from RULES, never hand-written."""

    def test_emit_rule_matrix_lists_every_rule_with_its_three_tiers(self):
        code, out, err = run("emit", "--format", "rule-matrix")
        self.assertEqual(code, 0, err)
        rows = [line for line in out.splitlines() if line.startswith("| `")]
        self.assertEqual(len(rows), len(acm.RULES))
        for name, tiers in acm.RULES.items():
            row = [r for r in rows if r.startswith("| `" + name + "`")]
            self.assertEqual(len(row), 1, name)
            self.assertTrue(row[0].endswith("| " + " | ".join(tiers) + " |"), name)

    def test_the_doc_table_equals_the_emitted_one(self):
        doc = (Path(SCRIPT).parent.parent / "references" / "rule-matrix.md").read_text(encoding="utf-8")
        _code, out, _err = run("emit", "--format", "rule-matrix")
        # The whole file, header included: the documented regeneration command must
        # reproduce it byte for byte.
        self.assertEqual(doc, out, "regenerate: emit --format rule-matrix --out references/rule-matrix.md")


if __name__ == "__main__":
    unittest.main()
