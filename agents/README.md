# Entry points by harness

This skill is a plain Agent Skill: a `SKILL.md` with frontmatter, plus `references/`,
`assets/`, `scripts/`, and `lessons/`. Nothing in it depends on a particular vendor. The only
runtime requirement is Python 3 for `scripts/admin_console_manifest.py`, which uses the
standard library only.

| Harness | Install as |
|---|---|
| Claude Code, claude.ai | Copy the skill directory into `~/.claude/skills/` or the project's `.claude/skills/`, or ship it inside a plugin |
| OpenAI Codex | Copy into `.agents/skills/adminwright/` and reference it from the repository's `AGENTS.md` |
| Cursor | Copy into the project and point a rule at `SKILL.md` |
| Gemini CLI | Copy into the project and reference `SKILL.md` from `GEMINI.md` |
| Any other harness | Point the agent at `SKILL.md` directly; it routes to everything else |

`openai.yaml` is an optional interface descriptor for harnesses that read one. Its absence
changes nothing — no file in this skill requires it, and no guidance depends on it.

## With no skill support at all

The skill still works. Give the agent two things:

1. The path to `SKILL.md`, with an instruction to follow it and load references by phase.
2. The project's agent contract file, written from
   [../assets/agent-contract.template.md](../assets/agent-contract.template.md).

The second matters more over time. A skill governs one session; the contract file lives in the
repository and governs every session after it.

## Adapting invocations

Documented commands use this form:

```text
python <skill-dir>/scripts/admin_console_manifest.py <command> ...
```

Substitute `py -3` or `python3` where `python` is not on PATH. Nothing else in the invocation
changes across platforms; paths are handled inside the script.

## Multi-agent use

Coordination is through the manifest and lock files on disk, never through a vendor's
inter-agent messaging. Any set of agents that can read and write the same working tree can
collaborate, whether they run in one harness or several. See
[../references/multi-agent.md](../references/multi-agent.md).
