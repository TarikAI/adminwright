# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TarikAI
"""`remove` and `rename`: the CLI path that lets a wrong entry be undone without hand-editing.

SKILL.md forbids editing manifest.json by hand and, until these commands, offered no way to
delete an item or to change an id. Both run under the write guard, so a removal that would
leave a dangling reference, or a rename that would break one, is refused with the reason.
"""

import json
import unittest

from cli_helpers import TempProject, acm


class RemoveRenameCase(TempProject):
    def setUp(self):
        super().setUp()
        self.data = self.build_honest_manifest()
        self.data["decisions"].append({
            "id": "dec.scope", "decision": "Scope", "reason": "Confirmed", "evidence": [],
            "status": "confirmed", "appliesTo": ["user.list", "users", "ops"]})
        self.data["agents"] = [{
            "id": "impl", "role": "implementer", "ownsCapabilities": [], "ownsScreens": ["users"],
            "previouslyOwned": ["user.list"], "status": "done"}]
        self.write(self.data)


class TestRemove(RemoveRenameCase):
    def test_an_unreferenced_item_can_be_removed(self):
        entry = {"id": "gap.temp", "severity": "low", "description": "d", "status": "open",
                 "rationale": "", "evidence": []}
        code, _o, err = self.cli("add", "--kind", "gap", "--json", json.dumps(entry))
        self.assertEqual(code, 0, err)
        code, out, err = self.cli("remove", "--kind", "gap", "--id", "gap.temp")
        self.assertEqual(code, 0, err)
        self.assertIn("Removed gap 'gap.temp'", out)
        self.assertEqual([g["id"] for g in self.read()["gaps"]], [])

    def test_removing_something_still_referenced_is_refused(self):
        code, _o, err = self.cli("remove", "--kind", "role", "--id", "ops")
        self.assertEqual(code, 2)
        self.assertIn("unknown-role-reference", err)
        self.assertEqual(len(self.read()["roles"]), 1)

    def test_allow_invalid_removes_and_lists_what_dangles(self):
        code, _o, err = self.cli("remove", "--kind", "role", "--id", "ops", "--allow-invalid")
        self.assertEqual(code, 0, err)
        self.assertIn("unresolved error", err)
        self.assertEqual(self.read()["roles"], [])

    def test_a_capability_is_found_across_entities(self):
        code, _o, err = self.cli("remove", "--kind", "capability", "--id", "user.list")
        self.assertEqual(code, 2, "the screen still lists it")
        self.assertIn("unknown-capability-reference", err)

    def test_an_unknown_id_is_a_usage_error(self):
        code, _o, err = self.cli("remove", "--kind", "screen", "--id", "nope")
        self.assertEqual(code, 2)
        self.assertNotIn("Traceback", err)


class TestRename(RemoveRenameCase):
    def test_renaming_a_capability_updates_every_reference(self):
        data = self.read()
        data["entities"][0]["lifecycleTransitions"] = [
            {"from": "active", "to": "active", "command": "user.list", "actorRoles": ["ops"]}]
        data["declaredStatic"] = [{
            "id": "static.one", "path": "entities[user].capabilities[user.list].safeguards",
            "value": "v", "reason": "r", "approvedBy": "cto", "evidence": []}]
        self.write(data)
        code, out, err = self.cli("rename", "--kind", "capability", "--id", "user.list",
                                  "--to", "user.search")
        self.assertEqual(code, 0, err)
        self.assertIn("updated", out)
        data = self.read()
        self.assertEqual(self.capability_of(data, "user.search")["id"], "user.search")
        self.assertEqual(data["screens"][0]["capabilities"], ["user.search"])
        self.assertEqual(data["entities"][0]["lifecycleTransitions"][0]["command"], "user.search")
        self.assertIn("user.search", data["decisions"][-1]["appliesTo"])
        self.assertEqual(data["agents"][0]["previouslyOwned"], ["user.search"])
        self.assertIn("[user.search]", data["declaredStatic"][0]["path"])
        code, out, err = self.validate()
        self.assertEqual(code, 0, out + err)

    def test_renaming_a_role_updates_capabilities_screens_and_decisions(self):
        code, _o, err = self.cli("rename", "--kind", "role", "--id", "ops", "--to", "operators")
        self.assertEqual(code, 0, err)
        data = self.read()
        self.assertEqual(data["roles"][0]["id"], "operators")
        self.assertEqual(self.capability_of(data)["roles"], ["operators"])
        self.assertEqual(data["screens"][0]["roles"], ["operators"])
        self.assertIn("operators", data["decisions"][-1]["appliesTo"])
        code, out, err = self.validate()
        self.assertEqual(code, 0, out + err)

    def test_renaming_an_agent_updates_ownership_records(self):
        data = self.read()
        self.capability_of(data)["owner"] = "impl"
        self.write(data)
        code, _o, err = self.cli("rename", "--kind", "agent", "--id", "impl", "--to", "builder")
        self.assertEqual(code, 0, err)
        data = self.read()
        self.assertEqual(data["agents"][0]["id"], "builder")
        self.assertEqual(self.capability_of(data)["owner"], "builder")
        self.assertEqual(self.capability_of(data)["implementedBy"], "builder")

    def test_a_clash_or_a_bad_id_is_refused(self):
        entry = {"id": "support", "name": "Support", "responsibilities": ["x"], "scopes": ["y"]}
        self.cli("add", "--kind", "role", "--json", json.dumps(entry))
        for target in ("support", "Bad Id"):
            code, _o, err = self.cli("rename", "--kind", "role", "--id", "ops", "--to", target)
            self.assertEqual(code, 2, target)
            self.assertNotIn("Traceback", err)
        self.assertEqual({r["id"] for r in self.read()["roles"]}, {"ops", "support"})

    def test_renaming_an_item_that_already_carries_errors_is_not_blamed_for_them(self):
        data = self.read()
        data["roles"].append({"id": "broken", "name": "", "responsibilities": ["x"],
                              "scopes": ["all"], "mfaRequired": True})
        self.write(data)
        code, _o, err = self.cli("rename", "--kind", "role", "--id", "broken", "--to", "repaired")
        self.assertEqual(code, 0, err)


class TestSetWholeObjects(RemoveRenameCase):
    """Part 2: one `set` for a whole crossCutting section instead of one call per flag."""

    def test_a_section_can_be_set_in_one_call(self):
        section = dict(self.read()["crossCutting"]["audit"])
        section["tamperProtected"] = False
        code, _o, err = self.cli("set", "--path", "crossCutting.audit", "--value", json.dumps(section))
        self.assertEqual(code, 0, err)
        self.assertIs(self.read()["crossCutting"]["audit"]["tamperProtected"], False)


if __name__ == "__main__":
    unittest.main()
