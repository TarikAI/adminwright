# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TarikAI
"""No command may traceback on a manifest that is the wrong shape.

An agent that hand-writes JSON produces exactly these: an object where a string
belongs, a string where a list belongs, `null` for a collection. The contract is that
`validate` reports it and every other command either works or refuses with exit 2 --
never a Python traceback with exit 1 and an empty stdout.
"""

import json
import copy
import unittest

from cli_helpers import TempProject, run

EMITTERS = ("authz-matrix", "test-plan", "nav-map", "seed-plan", "operator-handbook", "gap-report")


class TestMalformedManifests(TempProject):
    def setUp(self):
        super().setUp()
        self.good = self.build_honest_manifest()

    def broken(self, mutate):
        data = copy.deepcopy(self.good)
        mutate(data)
        self.write(data)

    def assert_no_traceback(self, code, out, err, label):
        self.assertNotIn("Traceback", err, label + "\n" + err)
        self.assertIn(code, (0, 1, 2), label)

    def exercise_everything(self, label):
        for fmt in EMITTERS:
            code, out, err = self.cli("emit", "--format", fmt)
            self.assert_no_traceback(code, out, err, label + " emit " + fmt)
        for args in (("report",), ("report", "--json"), ("coverage",),
                     ("validate", "--phase", "plan"), ("validate", "--phase", "release"),
                     ("validate", "--json")):
            code, out, err = self.cli(*args)
            self.assert_no_traceback(code, out, err, label + " " + " ".join(args))

    def test_wrong_shapes_at_every_level(self):
        mutations = {
            "roles as strings": lambda d: d.update(roles=["ops", 5, None, ["x"]]),
            "role id is an object": lambda d: d["roles"][0].update(id={"a": 1}),
            "entities null": lambda d: d.update(entities=None),
            "entity capabilities a string": lambda d: d["entities"][0].update(capabilities="none"),
            "capability kind a list": lambda d: d["entities"][0]["capabilities"][0].update(kind=["query"]),
            "capability risk a dict": lambda d: d["entities"][0]["capabilities"][0].update(risk={"a": 1}),
            "capability status a list": lambda d: d["entities"][0]["capabilities"][0].update(status=[]),
            "capability roles a string": lambda d: d["entities"][0]["capabilities"][0].update(roles="ops"),
            "entityStates a string": lambda d: d["entities"][0]["capabilities"][0].update(entityStates="x"),
            "screens with objects in roles": lambda d: d["screens"][0].update(roles=[{"id": "x"}]),
            "screen states a number": lambda d: d["screens"][0].update(states=5),
            "gates a dict": lambda d: d.update(qualityGates={"build": {}}),
            "gate status a list": lambda d: d["qualityGates"][0].update(status=["passed"]),
            "crossCutting a list": lambda d: d.update(crossCutting=[]),
            "crossCutting section a string": lambda d: d["crossCutting"].update(audit="yes"),
            "platform a string": lambda d: d.update(platform="Acme"),
            "platform archetypes a string": lambda d: d["platform"].update(archetypes="saas"),
            "decisions with a string appliesTo": lambda d: d.update(
                decisions=[{"id": "d", "decision": "x", "reason": "y", "evidence": [], "status": "confirmed",
                            "appliesTo": "everything"}]),
            "gaps with a null": lambda d: d.update(gaps=[None, {"id": "g"}]),
            "agents with strings": lambda d: d.update(agents=["a1", {"id": "a2", "role": ["qa"]}]),
            "feedback a dict": lambda d: d.update(feedback={"a": 1}),
            "declaredStatic path a list": lambda d: d.update(declaredStatic=[{"id": "s", "path": ["x"]}]),
        }
        for label, mutate in mutations.items():
            with self.subTest(label):
                self.broken(mutate)
                self.exercise_everything(label)

    def test_a_manifest_that_is_not_an_object_or_is_empty(self):
        for text in ("[]", "null", "42", '"x"', "{}", ""):
            self.manifest.write_text(text, encoding="utf-8")
            for args in (("validate",), ("report",), ("emit", "--format", "gap-report"),
                         ("coverage",)):
                code, out, err = self.cli(*args)
                self.assertNotIn("Traceback", err, text + " " + " ".join(args))
                self.assertIn(code, (0, 1, 2), text + " " + " ".join(args))


class TestStoreCommands(TempProject):
    """`store status` and `store init` had no CLI test at all."""

    def test_status_and_init_without_a_remote(self):
        store = self.tmp / "store"
        code, out, err = run("store", "status", "--store", store)
        self.assertEqual(code, 0, err)
        self.assertIn("Exists: no", out)
        code, out, err = run("store", "init", "--store", store)
        self.assertEqual(code, 0, err)
        code, out, err = run("store", "status", "--store", store)
        self.assertEqual(code, 0, err)
        self.assertIn("Exists: yes", out)
        self.assertIn("Observations: 0", out)

    def test_sync_without_init_is_a_usage_error(self):
        code, _out, err = run("store", "sync", "--store", self.tmp / "nowhere")
        self.assertEqual(code, 2)
        self.assertNotIn("Traceback", err)


if __name__ == "__main__":
    unittest.main()
