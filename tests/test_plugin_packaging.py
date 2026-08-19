# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TarikAI
"""Guards on the Claude Code plugin packaging.

These exist because the failure they prevent is silent: users keep working on
a stale copy and nothing reports it. Nothing here touches the network.

Run:  python -m unittest discover -s tests -v
"""

import json
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
PLUGIN = SKILL_ROOT / ".claude-plugin" / "plugin.json"
MARKETPLACE = SKILL_ROOT / ".claude-plugin" / "marketplace.json"


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


class TestAutoUpdateContract(unittest.TestCase):
    """No `version` field, deliberately — it is what makes installs auto-update.

    Claude Code resolves a git-sourced plugin's version from the commit SHA
    when `version` is omitted, so every push to main reaches users. Setting the
    field pins the plugin instead: users then receive updates only when someone
    remembers to bump it, and a forgotten bump is invisible from this side.
    Adding `version` here is therefore a breaking change to distribution, not a
    tidy-up.
    """

    def test_plugin_json_declares_no_version(self):
        self.assertNotIn(
            "version", load(PLUGIN),
            "a version field pins the plugin and stops SHA-based auto-update",
        )

    def test_marketplace_entries_declare_no_version(self):
        for entry in load(MARKETPLACE).get("plugins", []):
            self.assertNotIn(
                "version", entry,
                "a version field on '" + str(entry.get("name")) + "' pins it and "
                "stops SHA-based auto-update",
            )


class TestPluginContents(unittest.TestCase):
    """Every path the plugin advertises must resolve inside the repository.

    A renamed or moved agent file still installs cleanly and simply goes
    missing for the user, so the check belongs here rather than in review.
    """

    def test_declared_agents_exist(self):
        agents = load(PLUGIN).get("agents", [])
        self.assertTrue(agents, "the plugin should ship its role agents")
        for relative in agents:
            path = (SKILL_ROOT / relative.lstrip("./")).resolve()
            self.assertTrue(path.is_file(), "declared agent is missing: " + relative)
            self.assertTrue(
                path.read_text(encoding="utf-8").startswith("---"),
                "agent needs YAML frontmatter to be discoverable: " + relative,
            )

    def test_marketplace_source_points_at_this_repository(self):
        entries = load(MARKETPLACE).get("plugins", [])
        names = {entry.get("name") for entry in entries}
        self.assertIn(load(PLUGIN)["name"], names,
                      "the marketplace must list the plugin it ships")

    def test_skill_entry_point_exists(self):
        self.assertTrue((SKILL_ROOT / "SKILL.md").is_file())


if __name__ == "__main__":
    unittest.main()
