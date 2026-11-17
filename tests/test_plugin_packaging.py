# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TarikAI
"""Guards on the Claude Code plugin packaging.

These exist because the failure they prevent is silent: users keep working on
a stale copy and nothing reports it. Nothing here touches the network.

Run:  python -m unittest discover -s tests -v
"""

import json
import re
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
PLUGIN = SKILL_ROOT / ".claude-plugin" / "plugin.json"
MARKETPLACE = SKILL_ROOT / ".claude-plugin" / "marketplace.json"
SUPPORTED_AGENT_KEYS = {
    "name", "description", "model", "effort", "maxTurns", "autoCompactWindow", "tools",
    "disallowedTools", "skills", "memory", "background", "omitClaudeMd", "isolation", "color",
    "experimental",
}
IGNORED_AGENT_KEYS = {"permissionMode", "hooks", "mcpServers", "initialPrompt"}
AGENT_COLORS = {"red", "blue", "green", "yellow", "purple", "orange", "pink", "cyan"}


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def parse_frontmatter_text(text, label):
    """Parse YAML frontmatter the way a strict loader would.

    Uses PyYAML when it is importable (CI installs it). Otherwise a strict fallback:
    a plain (unquoted, unbracketed) scalar may not contain `: ` or ` #`, which every
    real YAML parser rejects or truncates.
    """
    match = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n", text, re.S)
    assert match, label + ": no YAML frontmatter block"
    block = match.group(1)
    try:
        import yaml
    except ImportError:
        yaml = None
    if yaml is not None:
        try:
            data = yaml.safe_load(block)
        except yaml.YAMLError as exc:
            raise AssertionError(label + ": frontmatter is not valid YAML: " + str(exc).splitlines()[0])
        assert isinstance(data, dict), label + ": frontmatter must be a mapping"
        return data
    fields = {}
    for line in block.splitlines():
        if not line.strip() or line.startswith((" ", "\t", "-")):
            continue
        key, sep, value = line.partition(":")
        assert sep, label + ": line without a key: " + line
        value = value.strip()
        if value and value[0] not in "\"'[{|>":
            assert ": " not in value and " #" not in value, (
                label + ": unquoted value for '" + key + "' contains ': ' or ' #' and is not valid YAML"
            )
        fields[key.strip()] = value.strip("\"'")
    return fields


def strict_frontmatter(path):
    return parse_frontmatter_text(path.read_text(encoding="utf-8"), path.name)


class TestAutoUpdateContract(unittest.TestCase):
    """No `version` field, deliberately — it is what lets every commit be an update.

    Claude Code resolves a git-sourced plugin's version from the commit SHA
    when `version` is omitted, so every commit merged to main is a new version a
    user can install. (It is not pushed to them: third-party marketplaces have
    auto-update off by default, so users update with `claude plugin update` or
    enable auto-update for the marketplace.) Setting the field pins the plugin
    instead: users then receive updates only when someone remembers to bump it,
    and a forgotten bump is invisible from this side. Adding `version` here is
    therefore a breaking change to distribution, not a tidy-up.
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

    def test_every_declared_agent_has_strictly_valid_frontmatter(self):
        """Claude Code drops an agent whose frontmatter does not parse, silently.

        The QA agent's description held an unquoted `: `, which PyYAML, js-yaml,
        `yaml` and gray-matter all reject, so it was absent from the recommended
        install with no error anywhere. A `startswith('---')` check saw nothing.
        """
        for relative in load(PLUGIN).get("agents", []):
            path = (SKILL_ROOT / relative.lstrip("./")).resolve()
            fields = strict_frontmatter(path)
            self.assertEqual(fields.get("name"), path.stem,
                             path.name + ": name must equal the file stem")
            self.assertTrue(str(fields.get("description", "")).strip(),
                            path.name + ": description is required")

    def test_agent_frontmatter_uses_only_keys_claude_code_reads(self):
        """Claude Code ignores an unknown or misspelled key silently: `disallowedtools`
        would drop a reviewer's tool restriction with no signal anywhere (A-15).

        The lists are the plugin-agent fields in
        https://code.claude.com/docs/en/plugins/components ("Frontmatter fields in plugin
        agents"), checked 2026-11-17.
        """
        for relative in load(PLUGIN).get("agents", []):
            path = (SKILL_ROOT / relative.lstrip("./")).resolve()
            fields = strict_frontmatter(path)
            ignored = sorted(set(fields) & IGNORED_AGENT_KEYS)
            self.assertEqual(ignored, [], path.name + ": " + ", ".join(ignored)
                             + " is ignored for plugin agents; ship it as plugin hooks or MCP servers")
            unknown = sorted(set(fields) - SUPPORTED_AGENT_KEYS)
            self.assertEqual(unknown, [], path.name + ": not a plugin-agent frontmatter key")
            if "color" in fields:
                self.assertIn(fields["color"], AGENT_COLORS, path.name + ": color")

    def test_the_reviewers_cannot_edit_or_dispatch(self):
        for name in ("adminwright-qa", "adminwright-security", "adminwright-ux-reviewer"):
            fields = strict_frontmatter(SKILL_ROOT / "agents" / (name + ".md"))
            denied = {t.strip() for t in str(fields.get("disallowedTools", "")).split(",")}
            self.assertTrue({"Edit", "NotebookEdit", "Agent"} <= denied, name + ": " + str(sorted(denied)))

    def test_the_key_check_catches_a_misspelling(self):
        broken = ("---" + chr(10) + "name: x" + chr(10) + "description: d" + chr(10)
                  + "disallowedtools: Edit" + chr(10) + "---" + chr(10))
        fields = parse_frontmatter_text(broken, "broken.md")
        self.assertEqual(set(fields) - SUPPORTED_AGENT_KEYS, {"disallowedtools"})

    def test_skill_frontmatter_is_strictly_valid(self):
        fields = strict_frontmatter(SKILL_ROOT / "SKILL.md")
        self.assertEqual(fields.get("name"), "adminwright")
        self.assertTrue(str(fields.get("description", "")).strip())

    def test_the_strict_check_catches_an_unquoted_colon(self):
        broken = ("---" + chr(10) + "name: x" + chr(10)
                  + "description: Owns the release gate: validate must pass" + chr(10)
                  + "---" + chr(10) + "body" + chr(10))
        with self.assertRaises(AssertionError):
            parse_frontmatter_text(broken, "broken.md")

    def test_marketplace_source_points_at_this_repository(self):
        entries = load(MARKETPLACE).get("plugins", [])
        names = {entry.get("name") for entry in entries}
        self.assertIn(load(PLUGIN)["name"], names,
                      "the marketplace must list the plugin it ships")

    def test_skill_entry_point_exists(self):
        self.assertTrue((SKILL_ROOT / "SKILL.md").is_file())


if __name__ == "__main__":
    unittest.main()
