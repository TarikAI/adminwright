# Entry points by harness

## Shipped subagents

The plugin registers six subagents: one per coordination role in
[../references/multi-agent.md](../references/multi-agent.md), plus a learning pass. Each
carries its role contract — entry and exit conditions, what it writes, what it must not
touch, and the manifest protocol — so a dispatched agent needs no conversation history to do
its pass correctly. The files are harness-neutral: Claude Code loads them natively from the
plugin; every other harness gets them via the installer below.

| Agent | Role | Dispatch when |
|---|---|---|
| `adminwright-architect` | `architect` | First, always. Discovery, manifest modeling, capability derivation, IA, build order. Never writes feature code. |
| `adminwright-implementer` | `implementer` | Per claimed slice: the spine (one instance, serialized), then one instance per entity/domain in parallel. Never reviews its own work. |
| `adminwright-ux-reviewer` | `ux-reviewer` | A screen is `implemented` and the app runs. States, IA, accessibility, responsiveness, copy. Never changes server logic. |
| `adminwright-qa` | `qa` | A domain is `implemented` and the build passes; also owns the release gate. Adversarial audit, permission matrix, coverage, browser evidence. Never fixes its own findings. |
| `adminwright-security` | `security` | The auth/authz/tenancy/audit spine is `implemented` and the authz matrix emits. Threat model, matrix, audit completeness, data exposure. Never approves its own exceptions. |
| `adminwright-harvester` | learning pass | Last, always — after build, extend, audit, or repair. Collects what the run taught from `feedback[]`, worklogs, and a conversation digest; runs `harvest` and `promote`; records lessons; edits references when the promotion bar clears. |

Shape of a healthy run, orchestrated from the main session:

1. Dispatch `adminwright-architect`; wait for plan validation to exit 0.
2. Dispatch one `adminwright-implementer` for the walking skeleton — serialized, nothing in
   parallel with the spine.
3. Fan out `adminwright-implementer` instances per entity or domain slice, each under its
   own claim, no shared migrations in flight.
4. Converge: `adminwright-ux-reviewer` and `adminwright-security` in parallel over each
   finished domain; `adminwright-qa` rolling behind them.
5. Route filed gaps back to implementers under new claims; repeat until qa's release gate
   (`validate` and `coverage` both exit 0) passes.
6. Dispatch `adminwright-harvester` last, passing it a short digest of this session's
   conversation — corrections the user made, guidance that proved wrong, phases that were
   skipped — so what the run taught outlives the chat.

The agents coordinate through the manifest and lock files only. In a harness without
subagent support, run the same passes in sequence with distinct agent ids — the
single-agent degradation rules in
[../references/multi-agent.md](../references/multi-agent.md) apply.

## Installing the agents into any harness

The agent files use the token `${CLAUDE_PLUGIN_ROOT}` for the skill directory. Claude Code
expands it in plugin installs. For every other harness, run the installer — it resolves the
skill path, bakes it into the prompts, and writes them where that harness discovers agents:

```text
python <skill-dir>/scripts/install_agents.py --harness <name> --project-root <project-root>
```

| `--harness` | Writes to | Notes |
|---|---|---|
| `claude-code` | `.claude/agents/` | For non-plugin installs; the plugin needs no installer |
| `opencode` | `.opencode/agent/` | Adds `mode: subagent` to each file's frontmatter |
| `codex` | `.adminwright/agents/` | Prints a pointer block for `AGENTS.md`; add `--append-pointer` to write it |
| `antigravity` | `.adminwright/agents/` | Pointer block for `AGENTS.md`, same flag |
| `gemini` | `.adminwright/agents/` | Pointer block for `GEMINI.md`, same flag |
| `cursor` | `.adminwright/agents/` | Pointer block for `.cursorrules`, same flag |
| `pi` | `.adminwright/agents/` | Pointer block for `AGENTS.md`, same flag |
| `generic` | `.adminwright/agents/` | Any other harness: point it at the files, in the printed order |

In harnesses that spawn subagents, dispatch each file as one. In harnesses that cannot, the
pointer block instructs the main agent to adopt the files one at a time, sequentially, with
a distinct agent id per pass — the independence rule stays machine-checkable either way,
because coordination state lives in the manifest, not the harness.

The pointer block also carries three rules every harness needs stated explicitly, plus
harness-specific notes where a harness's default behavior fights the skill:

- **A supplied plan is the plan of record.** Field-tested on Antigravity, which drafts its
  own implementation plan even when the user provides one: the block (and the architect and
  implementer prompts themselves) forbid authoring a competing plan — mirror the given plan
  into the harness's plan artifact verbatim if one is required, and start executing.
- **The completion report is for humans.** Every agent leads its final message with a
  plain-language paragraph (what operators can now do, what was found, whether it is safe),
  and the run closes with the skill's completion-report format with validate/coverage exit
  codes as evidence — never "production-ready" without them.
- **Sequential harnesses announce pass switches** (Codex/Gemini/Cursor/Pi notes), so the
  transcript shows where one role's authority ended and the manifest is re-read from disk
  rather than carried across passes.

Learning transfers across harnesses the same way: conversation history stays in one tool,
so each agent banks session learnings into the manifest's `feedback[]`, and the harvester
moves them into the cross-project store (`$ADMINWRIGHT_HOME`, default `~/.adminwright`)
that every harness shares.

## Installing the skill elsewhere

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
