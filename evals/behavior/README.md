# Behavioural evals

`evals/fixtures/` pins what the validator decides. This folder tests what an agent *does*
with the skill and its agents, using [`claude plugin eval`](https://code.claude.com/docs/en/plugin-evals):
- whether the skill loads for a back-office request and stays out of a near miss;
- whether an audit finds a decorative console;
- whether the role agents keep their boundaries.

`.claude-plugin/plugin.json` points the runner here (`"experimental": {"evals": "evals/behavior"}`),
because `evals/` already holds the validator fixtures.

Each case runs three times with the plugin and three times without it. The report shows both
scores and their difference, `Δ`: what the plugin contributed.

## The cases

**`smoke/`** needs no tool grant, so it runs anywhere, native Windows included.

| Case | Passes when |
|---|---|
| `triggers-on-back-office-plan` | A back-office request loads the skill. The plan treats suspend and refund as server-side, audited commands and builds the permission and audit spine first |
| `triggers-on-gateway-superadmin` | A superadmin request for an LLM gateway loads the skill and gets real operator controls, not invented numbers |
| `ignores-marketing-chart` | A chart on a public landing page does not load the skill |
| `ignores-customer-password-reset` | A customer-facing feature does not load the skill |
| `audit-finds-a-decorative-console` | Auditing the Support Desk admin page names the hard-coded tenants, the browser-only permission check, and the Suspend button that calls nothing |
| `no-shell-no-verified-claim` | With no shell, the reply says the manifest gates could not run, and calls nothing verified |

**`shell/`** dispatches the role agents, which run the manifest CLI. These cases need
`--allow-tools Bash Write Edit`, and Bash runs inside Claude Code's OS sandbox: Linux (with
`bubblewrap` and `socat`), macOS, or WSL2. Native Windows has no sandbox backend and refuses
the runs.

| Case | Passes when |
|---|---|
| `architect-plans-before-building` | The architect writes a manifest with roles and a high-risk command, and records a build order as a decision. It creates nothing under `src/` and leaves the scaffolded source files byte for byte |
| `implementer-builds-on-real-data` | The implementer claims `tenant.list`. It replaces the hard-coded tenants with the real query, checks the staff session in the API route, and leaves the capability unreviewed |
| `security-files-gaps-fixes-nothing` | The orchestrator records the reviewer's starting point with `review-scope --start`. The security reviewer files a critical or high gap against `tenant.suspend`, whose code has none of the checks the manifest claims. It leaves that code untouched and does not sign off |
| `qa-contests-instead-of-signing` | The qa agent is dispatched, registers through the CLI, and sets `contested` with a high gap open. It never marks the capability `reviewed`, and the main session never writes the manifest with its file tools (a hand edit by the dispatched agent itself is not visible to these graders) |
| `harvester-asks-for-the-conversation` | Dispatched without a conversation digest, the harvester still banks the manifest's feedback, then returns `NEEDS_CONTEXT` |

The shell cases judge an agent by what it leaves in the workspace (the manifest and the
product files) and by the `STATUS:` line it returns, which reaches the trace as the Agent
tool's result. They do not count the tool calls a dispatched agent makes inside its own
context, which the main session's transcript may not include.

A `trace` grader sees every message, tool results included, so a Read of `multi-agent.md`
puts the words `STATUS: DONE` and `NEEDS_CONTEXT` in the trace. The status graders therefore
require the line to start a message or a line; `tests/test_behavior_evals.py` runs each one
against the skill's documents and the case prompts and fails if any of them matches.

`_fixtures/` holds the Support Desk project (a Next.js and Prisma help desk whose admin page is
decorative), an overlay for a handed-off `tenant.suspend`, and `scaffold-lib.sh`. Each case's
`fixture.sh` sources that file to seed its workspace. Seeded manifests are built with the
plugin's own CLI, so they always match the current rules.

## Running

You need:
- Claude Code 2.1.269 or later, logged in: run `claude`, then `/login`, or set `ANTHROPIC_API_KEY`;
- Python 3.9+ and git on PATH, for the scaffolds;
- on Windows, Git for Windows (Claude Code needs it anyway).

Run from the repository root. `--scaffold` is required because the cases seed the Support
Desk project. `--trust-plugin` asserts that you trust this checkout's code; pass it only for
your own copy.

```bash
claude plugin eval . --tag smoke --scaffold --trust-plugin --no-publish --max-cost-usd 10
```

```bash
claude plugin eval . --tag shell --scaffold --allow-tools Bash Write Edit --trust-plugin --no-publish --max-cost-usd 60
```

**Before the first shell run, check the sandbox once.** Shell grants run Bash in a sandbox
whose docs say the home directory is unreadable, and the plugin under test, with the
`scripts/admin_console_manifest.py` every agent calls, lives under a home directory both
locally and on a CI runner. The docs do not say whether the plugin's own directory is
exempt. If it is not, every shell case fails at its first CLI call and the whole set's
budget buys scores that measure nothing. So run one case, one arm, once, keep the
workspace, and read the dispatched agent's first `admin_console_manifest.py` call in the
transcript:

```bash
claude plugin eval . --case architect-plans-before-building --runs 1 --ablation none --scaffold --allow-tools Bash Write Edit --trust-plugin --no-publish --keep-temp
```

If the call was denied, the fix is in the scaffold, not the agents: have `scaffold-lib.sh`
copy `scripts/` and the references into the workspace and write that copy's path to
`.adminwright/skill-dir`, which every agent's "Locate the skill" reads first.

While changing one case, run one arm once, then confirm at the default three runs:

```bash
claude plugin eval . --case implementer-builds-on-real-data --runs 1 --ablation none --scaffold --allow-tools Bash Write Edit --trust-plugin --no-publish
```

Every run and every `llm` grader is a real model call on your account:
- the smoke set is 36 short runs;
- the shell set is 30 agent runs that each build, review or plan something.

Keep `--max-cost-usd` on. Reports go to `evals/behavior/results/<timestamp>/`, which git
ignores. The manual workflow `.github/workflows/behavior-evals.yml` runs the suite on Linux
with the repository's `ANTHROPIC_API_KEY` secret, which only the step that checks it and the
step that runs the suite can read. Left empty, its cost ceiling follows the tag: 10 for
smoke, 60 for shell, 70 for all. A ceiling below that stops the run partway (exit 2,
`partial: true`).

## Adding a case

- Tag it `smoke` (read-only tools, no grant) or `shell` (lists `Bash`).
- Give it one grader on the result (a file, or the final reply) and one on how it got there.
- Grade a long file with `regex` over its contents rather than an `llm` judge.
- A grader that checks the run left something unchanged passes before the run by design.
  Add it to `PRESERVATION` in `tests/test_behavior_evals.py`, which fails on any other grader
  that would pass against the untouched workspace.
- `python -m pytest tests/test_behavior_evals.py` checks every case file against the
  runner's schema and runs every scaffold. Keep it green; it costs nothing.
