# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TarikAI
"""A ceiling on what each entry point costs an agent to load.

An agent that follows SKILL.md loads references by phase, and every token of them competes
with the code it is working on. A reference that only ever grows stops being read (see
references/skill-evolution.md), and the per-file 450-line CI ceiling does not stop the
SUM from growing: at the audited commit a full build already cost about 68k tokens of
references, and the audit's own fixes added roughly a tenth of that again.

Tokens are estimated as characters / 4, which is coarse and stable. The ceilings sit a
few percent above today's numbers. Raising one is allowed; it should be a decision made
in the pull request that raises it, not something that happens by accretion.
"""

import glob
import unittest
from pathlib import Path

from cli_helpers import SKILL_ROOT

CEILINGS = {
    # entry point (SKILL.md's "Load references by phase" table) -> ceiling in estimated tokens
    "full build": 90000,
    "narrow extension": 18000,
    "audit only": 38000,
    "repair": 16000,
}
AGENT_CEILING = 3500


def estimate(paths):
    return sum(len((SKILL_ROOT / p).read_text(encoding="utf-8")) for p in paths) // 4


def references(*names):
    return ["references/" + name + ".md" for name in names]


ENTRY_POINTS = {
    "full build": ["SKILL.md"] + sorted(
        "references/" + Path(p).name for p in glob.glob(str(SKILL_ROOT / "references" / "*.md"))
    ),
    "narrow extension": ["SKILL.md"] + references("discovery", "stack-adapters"),
    "audit only": ["SKILL.md"] + references("discovery", "capability-catalog", "verification",
                                            "security-governance"),
    "repair": ["SKILL.md"] + references("verification"),
}


class TestTokenBudget(unittest.TestCase):
    def test_every_entry_point_stays_under_its_ceiling(self):
        for name, ceiling in CEILINGS.items():
            used = estimate(ENTRY_POINTS[name])
            self.assertLessEqual(
                used, ceiling,
                "%s now costs about %d tokens (ceiling %d). Trim, move a fact to the one file "
                "that owns it, or raise the ceiling in this test deliberately." % (name, used, ceiling),
            )

    def test_each_agent_prompt_stays_small(self):
        for path in sorted(glob.glob(str(SKILL_ROOT / "agents" / "adminwright-*.md"))):
            used = len(open(path, encoding="utf-8").read()) // 4
            self.assertLessEqual(used, AGENT_CEILING, path + " costs about %d tokens" % used)

    def test_the_entry_points_name_files_that_exist(self):
        for name, paths in ENTRY_POINTS.items():
            for path in paths:
                self.assertTrue((SKILL_ROOT / path).is_file(), name + ": " + path)


if __name__ == "__main__":
    unittest.main()
