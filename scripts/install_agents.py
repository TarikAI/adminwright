# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TarikAI
"""Install the adminwright agents into any harness.

The agent files in <skill-dir>/agents/ are written once, harness-neutral, with the
token ${CLAUDE_PLUGIN_ROOT} standing for the skill directory. Claude Code expands
that token in plugin installs; every other harness receives a copy with the token
already replaced by the resolved absolute path, written to wherever that harness
discovers agents. Standard library only.

Usage:

    python <skill-dir>/scripts/install_agents.py --harness <name> --project-root <dir>
        [--skill-dir <dir>] [--append-pointer]

Harnesses:

    claude-code   <project>/.claude/agents/            (project-level subagents)
    opencode      <project>/.opencode/agent/           (mode: subagent added)
    codex         <project>/.adminwright/agents/  + pointer block for AGENTS.md
    antigravity   <project>/.adminwright/agents/  + pointer block for AGENTS.md
    gemini        <project>/.adminwright/agents/  + pointer block for GEMINI.md
    cursor        <project>/.adminwright/agents/  + pointer block for .cursorrules
    pi            <project>/.adminwright/agents/  + pointer block for AGENTS.md
    generic       <project>/.adminwright/agents/  + pointer block printed only

For harnesses without native subagent support, the pointer block instructs the
main agent to run the passes sequentially with distinct agent ids, per the
single-agent degradation rules in references/multi-agent.md. `--append-pointer`
appends the block to the harness's instruction file idempotently (a marker
comment prevents duplicates); without it the block is printed for manual paste.

Exit codes: 0 success, 2 usage or IO failure.
"""

import argparse
import sys
from pathlib import Path

TOKEN = "${CLAUDE_PLUGIN_ROOT}"
MARKER_START = "<!-- adminwright-agents:start -->"
MARKER_END = "<!-- adminwright-agents:end -->"

# harness -> (relative target directory, instruction file for the pointer block)
HARNESSES = {
    "claude-code": (".claude/agents", None),
    "opencode": (".opencode/agent", None),
    "codex": (".adminwright/agents", "AGENTS.md"),
    "antigravity": (".adminwright/agents", "AGENTS.md"),
    "gemini": (".adminwright/agents", "GEMINI.md"),
    "cursor": (".adminwright/agents", ".cursorrules"),
    "pi": (".adminwright/agents", "AGENTS.md"),
    "generic": (".adminwright/agents", None),
}

# Dispatch order matters and the pointer block states it.
AGENT_ORDER = (
    "adminwright-architect",
    "adminwright-implementer",
    "adminwright-ux-reviewer",
    "adminwright-security",
    "adminwright-qa",
    "adminwright-harvester",
)


def fail(message):
    print("ERROR: " + message, file=sys.stderr)
    return 2


def portable(path):
    """Absolute path with forward slashes, valid on every platform Python runs on."""
    return Path(path).resolve().as_posix()


def add_opencode_mode(text):
    """opencode discovers agents by file, with `mode: subagent` in frontmatter."""
    if not text.startswith("---\n") or "\nmode:" in text.split("\n---", 1)[0]:
        return text
    return text.replace("---\n", "---\nmode: subagent\n", 1)


# Extra dispatch guidance per harness, appended inside the pointer block. Each
# entry exists because that harness's default behavior fought the skill in the
# field; keep entries short and behavioral.
HARNESS_NOTES = {
    "antigravity": [
        "Antigravity note — respect existing plans: this harness drafts its own",
        "implementation plan before acting. When a plan already exists — the user supplied",
        "one, or the manifest's decisions[] records the build order — that IS the plan.",
        "Do not author a competing plan. If the harness requires a plan artifact, mirror",
        "the existing plan into it verbatim, then start executing at the first unfinished",
        "step. Re-planning burns budget and drifts from the manifest, which is the plan of",
        "record.",
    ],
    "codex": [
        "Codex note: run the passes sequentially in one session, adopting one role file at",
        "a time. Announce each pass switch in one line (role, agent id, exit condition)",
        "before continuing, so the transcript shows where one role's authority ended.",
    ],
    "gemini": [
        "Gemini note: run the passes sequentially, one role file at a time, and re-read the",
        "manifest from disk at each pass switch — do not carry a cached copy across passes.",
    ],
    "cursor": [
        "Cursor note: keep this file loaded as a rule; adopt one role file at a time and",
        "make the current role explicit in each response so review independence is visible.",
    ],
    "pi": [
        "Pi note: run the passes sequentially, one role file at a time, with a distinct",
        "agent id per pass; coordination state lives in the manifest, not the session.",
    ],
}


def pointer_block(agent_dir, skill_dir, harness=None):
    lines = [
        MARKER_START,
        "## Adminwright agent passes",
        "",
        "This project builds or audits its admin console under the adminwright skill at",
        "`" + skill_dir + "` (start from its `SKILL.md`). The role prompts in",
        "`" + agent_dir + "/` define six passes. If this harness can spawn subagents, dispatch",
        "each file as one. If not, run them yourself sequentially, adopting one file at a",
        "time as your instructions, with a distinct agent id per pass — the single-agent",
        "degradation rules in the skill's `references/multi-agent.md` apply.",
        "",
        "Order: architect (always first; plan-validate must exit 0), implementer for the",
        "auth/authz/tenancy/audit spine (serialized), implementers per entity slice,",
        "ux-reviewer and security over each finished domain, qa for the adversarial pass",
        "and release gate, harvester last to bank what the run taught.",
        "",
        "If the user supplies a plan at any point, it is the plan of record: record it in",
        "the manifest's decisions[], build from it, and raise conflicts as gaps — never",
        "regenerate it as a fresh plan of your own.",
        "",
        "The implementer of a capability never reviews it. Coordination goes through",
        "`.admin-console/manifest.json` via the skill's script, never through chat memory.",
        "",
        "When the final pass completes, close with the skill's completion report: one",
        "plain-language paragraph on what operators can now do, then profile, domains and",
        "workflows controlled, roles, integrations, safeguards and audit coverage,",
        "verification performed, validate/coverage exit codes, lessons recorded, and what",
        "remains deferred or blocked with the reason. Never claim 'production-ready'",
        "without those exit codes at 0.",
    ]
    notes = HARNESS_NOTES.get(harness or "")
    if notes:
        lines += [""] + notes
    lines.append(MARKER_END)
    return "\n".join(lines) + "\n"


def append_pointer(instruction_path, block):
    existing = ""
    if instruction_path.exists():
        existing = instruction_path.read_text(encoding="utf-8")
        if MARKER_START in existing:
            return False
    joiner = "" if not existing or existing.endswith("\n\n") else ("\n" if existing.endswith("\n") else "\n\n")
    instruction_path.write_text(existing + joiner + block, encoding="utf-8")
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--harness", required=True, choices=sorted(HARNESSES))
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--skill-dir", default=None,
                        help="defaults to this script's parent skill directory")
    parser.add_argument("--append-pointer", action="store_true",
                        help="append the pointer block to the harness's instruction file")
    args = parser.parse_args(argv)

    skill_dir = Path(args.skill_dir).resolve() if args.skill_dir else Path(__file__).resolve().parent.parent
    project_root = Path(args.project_root).resolve()
    source_dir = skill_dir / "agents"

    if not (skill_dir / "SKILL.md").exists():
        return fail("no SKILL.md in " + str(skill_dir) + "; pass --skill-dir explicitly")
    if not project_root.is_dir():
        return fail("project root does not exist: " + str(project_root))

    sources = [source_dir / (name + ".md") for name in AGENT_ORDER]
    missing = [str(p) for p in sources if not p.exists()]
    if missing:
        return fail("agent files missing: " + ", ".join(missing))

    target_rel, instruction_name = HARNESSES[args.harness]
    target_dir = project_root / target_rel
    target_dir.mkdir(parents=True, exist_ok=True)

    skill_path = portable(skill_dir)
    written = []
    for source in sources:
        text = source.read_text(encoding="utf-8").replace(TOKEN, skill_path)
        if args.harness == "opencode":
            text = add_opencode_mode(text)
        target = target_dir / source.name
        target.write_text(text, encoding="utf-8")
        written.append(target)

    print("Installed " + str(len(written)) + " agents to " + str(target_dir))
    for path in written:
        print("  " + path.name)

    if instruction_name:
        block = pointer_block(portable(target_dir), skill_path, args.harness)
        instruction_path = project_root / instruction_name
        if args.append_pointer:
            if append_pointer(instruction_path, block):
                print("Appended pointer block to " + str(instruction_path))
            else:
                print("Pointer block already present in " + str(instruction_path) + "; unchanged")
        else:
            print("\nAdd this to " + instruction_name + " (or rerun with --append-pointer):\n")
            print(block)
    elif args.harness == "generic":
        print("\nPoint your harness at the installed files; dispatch order is in each name:")
        print(pointer_block(portable(target_dir), skill_path, args.harness))
    return 0


if __name__ == "__main__":
    sys.exit(main())
