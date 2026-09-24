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
   (`validate` and `coverage` both exit 0) passes. The loop has a stop rule: a reviewer that
   sets `reviewStatus: contested` must file a `gaps[]` entry naming the capability (the
   validator refuses `contested` without one), the implementer fixes it and returns it to
   `unreviewed`, and a different reviewer than the contester reviews next. A capability
   contested twice goes to the user, not to a third round: file a `blocked` gap stating both
   positions. A gap becomes `accepted` only by the user, or by a named approver recorded as a
   confirmed `decisions[]` entry; agents propose, they never accept. The two-round rule and the
   approver rule are stated once, in [../references/multi-agent.md](../references/multi-agent.md).
6. Dispatch `adminwright-harvester` last, passing it a short digest of this session's
   conversation — corrections the user made, guidance that proved wrong, phases that were
   skipped — so what the run taught outlives the chat.

The agents coordinate through the manifest and lock files only. In a harness without
subagent support, run the same passes in sequence with distinct agent ids — the
single-agent degradation rules in
[../references/multi-agent.md](../references/multi-agent.md) apply.

## Installing the agents into any harness

The agent files name the skill directory `${CLAUDE_PLUGIN_ROOT}`, which Claude Code fills in
for plugin installs. For every other harness, run the installer — it writes the prompts where
that harness discovers agents, naming the skill in a way each machine can resolve:

```text
python <skill-dir>/scripts/install_agents.py --harness <name> --project-root <project-root>
```

| `--harness` | Native agent files | Also writes | Notes |
|---|---|---|---|
| `claude-code` | `.claude/agents/<name>.md` | — | The source files, frontmatter included. For non-plugin installs; the plugin needs no installer |
| `opencode` | `.opencode/agents/<name>.md` | — | Frontmatter rebuilt as `description` + `mode: subagent` |
| `codex` | `.codex/agents/<name>.toml` | `.adminwright/agents/`, `AGENTS.md` block | TOML with `name`, `description`, `developer_instructions`. The block is printed; add `--append-pointer` to write it |
| `antigravity` | `.agents/agents/<name>/agent.md` | `.adminwright/agents/`, `AGENTS.md` block | Frontmatter `name` + `description`. Same flag |
| `gemini` | `.gemini/agents/<name>.md` | `.adminwright/agents/`, `GEMINI.md` block | Frontmatter `name` + `description`, plus `max_turns: 200` and `timeout_mins: 120`: Gemini CLI's defaults (30 turns, 10 minutes) stop a pass partway. Same flag |
| `cursor` | `.cursor/agents/<name>.md` | `.adminwright/agents/`, `.cursor/rules/adminwright.mdc` | The `.mdc` is a project rule, always applied, rewritten on every install |
| `pi` | none — Pi has no built-in subagents | `.adminwright/agents/`, `AGENTS.md` block | Same flag |
| `generic` | none | `.adminwright/agents/` | Any other harness: point it at the files, in the printed order |

With the skill outside the project, every harness also gets `.adminwright/skill-dir` (this
machine's skill path) and a `/.adminwright/skill-dir` line in `.gitignore`; see below.

Native files carry only the fields their harness documents; `.adminwright/agents/` is the
harness-neutral copy of the source files. Everything else in the source frontmatter is
Claude Code-only — `model` (the harvester's `opus`), `disallowedTools` (the reviewers' edit
guard), and any Claude Code field added later, such as `color` — so only the `claude-code`
copies act on it; the neutral copy carries it verbatim for reference. Every other harness
loses the reviewers' edit restriction: see
[the reviewer edit guard](#the-reviewer-edit-guard-per-harness) below. The pointer block is
delimited by marker comments, so rerunning with
`--append-pointer` after the skill moves replaces it in place. Codex, Antigravity and Pi all
read `AGENTS.md`, so the block there carries one marked section per harness installed into
it: installing them one after another, in any order, keeps every harness's notes, and a
rerun changes nothing.

No file the installer writes for the project to commit holds a machine's absolute path.
When the skill lives inside the project, every copy and the pointer block name it relative
to the project root. When it lives outside (a plugin cache, `~/.claude/skills/`, a clone
elsewhere), the copies say `<skill>` where the source says `${CLAUDE_PLUGIN_ROOT}`, and
the installer writes this machine's path to `.adminwright/skill-dir` and adds
`/.adminwright/skill-dir` to the project's `.gitignore`. A skill found in Claude Code's
versioned plugin cache (`~/.claude/plugins/cache/<marketplace>/<plugin>/<version>/`, deleted
about 14 days after the plugin updates) is recorded as the marketplace clone
(`~/.claude/plugins/marketplaces/<marketplace>/`) when that holds the skill too, and the
installer warns when it does not. Each agent's "Locate the skill"
section reads that file first, and falls back to its search list whenever the path it names
does not exist. A teammate who pulls the committed agents needs the skill in one of those
standard places, or one run of the installer, which writes their own
`.adminwright/skill-dir`. The placeholder is deliberately not a `${...}` variable: outside
a Claude Code plugin no harness expands `${CLAUDE_PLUGIN_ROOT}`, and Gemini CLI reads
`${name}` in a subagent body as a template input and refuses to start the subagent.

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
- **Native subagents first, sequential passes as the fallback.** Codex, Gemini CLI, Cursor
  and Antigravity discover the installed agents, and the block names each one's directory;
  the Codex, Gemini and Cursor notes say to dispatch each pass to the agent of the same name
  (Codex spawns subagents when `AGENTS.md` asks it to). A pass run in the main session
  instead, and every pass in Pi, announces the switch and re-reads the manifest from disk,
  so the transcript shows where one role's authority ended.

Learning transfers across harnesses the same way: conversation history stays in one tool,
so each agent banks session learnings into the manifest's `feedback[]`, and the harvester
moves them into the cross-project store (`$ADMINWRIGHT_HOME`, default `~/.adminwright`)
that every harness shares.

### The reviewer edit guard, per harness

The qa, security and ux-reviewer sources carry `disallowedTools: Edit, NotebookEdit`: a
reviewer must not edit the code it reviews, yet it still writes its worklog and the
manifest, and qa writes tests, so file writes and the shell stay available. Only Claude Code
enforces this; in every other harness the restriction is lost. None of them can say "no
edit tool, writes allowed" in a project file, and their coarser switches — opencode's
`permission.edit`, Codex's `sandbox_mode`, Cursor's `readonly` — would block the worklog
and manifest writes too, so the installer maps the guard to none of them:

| Harness | Guard | What the harness offers |
|---|---|---|
| Claude Code | Enforced | `disallowedTools` removes Edit and NotebookEdit; Write and Bash remain, so it stops in-place edits, not every write |
| opencode | Not enforced | `permission.edit` gates `edit`, `write` and `apply_patch` together; path patterns under it can scope a reviewer's writes by hand |
| Codex | Not enforced | No per-tool switch for edits; `sandbox_mode = "read-only"` blocks every write, and a parent session's live sandbox settings override the agent file |
| Gemini CLI | Not enforced by the installer | `tools` is an allowlist of validated names (omitted, it inherits every tool), so dropping `replace` means listing all the others; a user-level policy rule does it cleanly (below) — project-level policies are currently disabled |
| Cursor | Not enforced | `readonly: true` also blocks file writes and state-changing shell commands |
| Antigravity | Not enforced | `tools` is an allowlist in which an unknown name can hang the subagent; its permissions gate `write_file` for edits and new files alike |
| Pi | Not applicable | No subagents |

Review independence does not rest on this guard: the manifest records who implemented and
who reviewed each capability, and `validate` reports a review recorded by the capability's
implementer (`reviewer-identity`).

To add the guard to Gemini CLI for your own user, save this as
`~/.gemini/policies/adminwright.toml`:

```toml
[[rule]]
toolName = "replace"
subagent = "adminwright-qa"
decision = "deny"
priority = 500
denyMessage = "adminwright reviewers file gaps; they do not edit the code they review"

[[rule]]
toolName = "replace"
subagent = "adminwright-security"
decision = "deny"
priority = 500
denyMessage = "adminwright reviewers file gaps; they do not edit the code they review"

[[rule]]
toolName = "replace"
subagent = "adminwright-ux-reviewer"
decision = "deny"
priority = 500
denyMessage = "adminwright reviewers file gaps; they do not edit the code they review"
```

### Dispatch names and working trees

In Claude Code the plugin registers the agents in its namespace: dispatch
`adminwright:adminwright-architect`, `adminwright:adminwright-qa`, and so on. Copies
installed with `--harness claude-code`, and the agents in every other harness, use the bare
`adminwright-<role>` names.

Run every pass in the project's own working tree. Do not give adminwright agents a worktree
of their own — Claude Code's `isolation: worktree`, or Antigravity's `branch` workspace
option: a worktree has its own copy of the manifest (Claude Code branches it from the default
branch, not the current HEAD), and the claim locks under `.admin-console/locks/` are
git-ignored, so passes in different worktrees see neither each other's manifest writes nor
their claims.

## Installing the skill elsewhere

This skill is a plain Agent Skill: a `SKILL.md` with frontmatter, plus `references/`,
`assets/`, `scripts/`, and `lessons/`. Nothing in it depends on a particular vendor. The only
runtime requirement is Python 3 for `scripts/admin_console_manifest.py`, which uses the
standard library only.

| Harness | Install as |
|---|---|
| Claude Code, claude.ai | Copy the skill directory into `~/.claude/skills/` or the project's `.claude/skills/`, or ship it inside a plugin |
| OpenAI Codex, Gemini CLI, Cursor, opencode, Antigravity, Pi | Copy into the project's `.agents/skills/adminwright/`: every one of them discovers project skills there. Their own directories work too (`.gemini/skills/`, `.cursor/skills/`, `.opencode/skills/`) |
| Any other harness | Point the agent at `SKILL.md` directly; it routes to everything else |

A skill inside the project also keeps the installed agents portable: they name it by its
project-relative path, which is the same on every machine that checks the project out.

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
