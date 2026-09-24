# Optional integrations

Adminwright runs standalone — offline, dependency-free, any harness. Two external tools
sharpen it when present, and change nothing when absent. Neither is a prerequisite;
neither blocks any mode, including Audit. Record their use or absence in `decisions[]`
like any other choice.

| Tool | What it adds | Where it plugs in | Cost of absence |
|---|---|---|---|
| [DesignArchitect](https://github.com/TarikAI/DesignArchitect) | Machine-verified UI closure — every control resolves, every state exists | Phase 4 (information architecture) | The architect designs per `references/experience-design.md` as before |
| [Open Code Review](https://github.com/alibaba/open-code-review) (OCR) | Diff-scoped, line-anchored code review with guaranteed file coverage | Phase 7 verification, and Audit mode | The gates run exactly as before |

Together they form a pipeline: the manifest models the domain → DesignArchitect proves
the UI complete against it → implementers build → OCR adds a second-opinion review whose
findings persist as `gaps[]` and surface in `emit --format gap-report`.

## DesignArchitect — verified UI closure

### What it solves

Design work done by prompt alone suffers dangling affordances (a "Settings" button that
leads nowhere), sibling blindness (the list screen exists, the detail screen doesn't),
and depth blindness (no empty state, no permission-denied state). DesignArchitect runs a
closure fixpoint: every destination a control asserts gets minted into a real screen,
iterating until nothing dangles — and then re-extracts the links from the *rendered*
output to prove closure held in the artifact, not just the plan.

### Setup

```bash
git clone https://github.com/TarikAI/DesignArchitect   # anywhere; a sibling checkout works
export DESIGN_ARCHITECT_HOME=/path/to/DesignArchitect  # or rely on sibling detection
```

Detection: the `DESIGN_ARCHITECT_HOME` environment variable, or a sibling checkout
containing `core/scripts/run_pipeline.py`. No variable and no checkout → the architect
proceeds without it and records the decision as not-available.

### How the run uses it

At Phase 4, after discovery and modeling, `adminwright-architect`:

1. Writes the manifest's screens, capabilities, actions, and required states to a spec
   doc DesignArchitect's Phase-1 miner reads (it mines BMAD/PRD/OpenAPI/schema/README
   docs from the project — confirm the read locations in your checkout's
   `ARCHITECTURE.md`; default `docs/design/admin-capabilities.md`).
2. Runs its pipeline for the admin area (mockup renderer unless the project says
   otherwise).
3. Accepts closure **only** when `.design-architect/holes.json` reports nothing
   unresolved and `handoff/coverage.md` reports `holes_remaining: 0`.
4. Records `graph.json`, `holes.json`, and `coverage.md` as `evidence[]` on the
   `decisions[]` entry that records the closure run (or under
   `crossCutting.experience.evidence`). Screens have no evidence field; their
   `accessibilityStatus` is a status enum, not a place for a log path.

### The contract handed to implementers

Affordance coverage, nothing more: every affordance in `.design-architect/graph.json`
resolves to a real, authorized destination, and every state the graph enumerates exists
in the app. The prototype's visual design never binds — the project's design system
always governs. `adminwright-implementer` carries this in its build order and
non-negotiables.

## Open Code Review — line-anchored review

### What it solves

An agent reviewing its own diff from memory skips files and drifts on line numbers. OCR's
deterministic layer computes the exact reviewable file set (staged, unstaged, untracked,
range, or commit), bundles related files, and anchors every comment to exact diff lines.
What the findings themselves are remains a judgment — the reviewing agent's (delegate
engine) or its configured LLM's (endpoint engine). Guaranteed: file coverage. Not
guaranteed: correctness. Treat output as evidence, never as a gate replacement; a clean
run is a floor, not a proof.

### Setup

```bash
npm install -g @alibaba-group/open-code-review   # needs Git >= 2.41
ocr --version                                    # verify
```

Delegate mode — the default — needs nothing else. Endpoint mode additionally needs a
provider. Interactive: `ocr config provider`, then `ocr config model`. Non-interactive
(any OpenAI-compatible gateway):

```bash
ocr config set provider my-gateway
ocr config set custom_providers.my-gateway.url https://gateway.example.com/v1
ocr config set custom_providers.my-gateway.protocol openai
ocr config set providers.my-gateway.api_key <key>
ocr config set model <model-id-from-the-gateway>
```

### Environment

| Variable | Effect |
|---|---|
| `ADMINWRIGHT_OCR_BIN` | The `ocr` executable to run instead of `ocr` on PATH. A value that names no file is an error (exit 2 naming the variable), never "ocr is not on PATH". |
| `ADMINWRIGHT_NODE_BIN` | Windows npm installs only: the `node` that runs the resolved `ocr.js` (see below). Default: the `node.exe` beside the shim, then `node` on PATH. |
| `ADMINWRIGHT_OCR_TIMEOUT` | Seconds one `ocr` call may run before it and its child processes are stopped (default 900); `--timeout` on `diff`/`scan` overrides it. Either must be a finite positive number (`inf` is refused, exit 2); a value above 86400 (a day) is lowered to 86400 with a note. |
| `OCR_NO_UPDATE` | Set to `1` by the bridge on every `ocr` call. The npm launcher otherwise checks npm for a newer version and can run `npm i -g` in the background each time it starts. |

### The three commands

All take `--manifest <project-root>/.admin-console/manifest.json`. `--project-root` is the
git repository under review and defaults to the manifest's project root: the optional
top-level `projectRoot` in the manifest (a path relative to the directory containing the
manifest file), otherwise the parent of that directory. Pass it only when the repository
to review is somewhere else. It is never taken from the working directory.

**1. `diff` — Build/Repair mode.** Reviews the current changes.

```bash
python <skill-dir>/scripts/code_review.py diff --manifest <project-root>/.admin-console/manifest.json --agent <your-agent-id>
```

Delegate engine (default, no OCR-side API key): writes one review bundle per run,
`.admin-console/ocr-bundle-<agent>-<timestamp>-<pid>.json` (no `<agent>` part without
`--agent`), so concurrent runs never overwrite each other. The bundle holds the mode, the
refs (`--from`/`--to`/`--commit`), every reviewable file keyed by `(path, status)`, the rule
groups and the instructions. `diff` prints the exact `record ... --bundle <that file>`
command. **The reviewing agent then reviews each file itself** (per the bundled rules and
its own reading of the diffs) and returns findings through `record`. Nothing was reviewed
until that happens; `diff` alone records nothing. A new `diff` by the same `--agent`
closes that agent's own earlier unrecorded bundle, and so does a `diff` that finds nothing
to review; bundles of other agents, or of runs without `--agent`, are never touched.

Endpoint engine (`--engine ocr`, needs provider config): runs
`ocr review --audience agent --format json` itself, keeps the raw JSON as evidence
(`.admin-console/ocr-diff-<timestamp>-<pid>.json`; `scan` writes `ocr-scan-...`), and
persists findings directly. What happens next depends on how `ocr` says the run ended: the
reply's `status`, which repeats the run manifest's `terminal_state` (`ocr` 1.8.5 and later).
Replies recorded from `ocr` 1.12.9 are in `tests/fixtures/ocr/`, and CI runs the bridge
against the real CLI at 1.9.6 and at npm's latest.

| `ocr` reports | Meaning | The bridge |
|---|---|---|
| `complete` | every selected file was reviewed | records the findings; exit 1, or 0 when there are none |
| `partial` (`ocr` itself exits 0) | some files failed, usually at the provider | records the findings that exist and prints every file in `coverage.failed` with its classification and reason; exit 2 |
| `failed`, or `ocr` exits non-zero | the run failed, or every selected file did | records nothing and prints `ocr`'s own message and the failed files; exit 2. The provider set-up hint appears only when the failure is a provider or configuration one |
| `skipped` | nothing was selected: no changes, or only excluded files changed | prints "nothing to review"; exit 0 |

`scan` has no run manifest: its `status` is `success`, `completed_with_warnings` or
`completed_with_errors`, a file it could not scan is named in `warnings[]`, and
`completed_with_errors` is handled like `partial`. A state the bridge does not know (a newer
`ocr`) keeps the findings but exits 2, because nothing confirms the coverage. A failed run is
never reported as "0 finding(s) recorded". Range and commit modes: `--from main --to feature`,
or `--commit <hash>`.

**2. `record` — persist delegate findings as gaps.**

```bash
python <skill-dir>/scripts/code_review.py record --manifest <project-root>/.admin-console/manifest.json --bundle <the bundle diff printed> --findings-file findings.json
```

where `findings.json` is:

```json
{
  "findings": [
    {"path": "src/orders.py", "content": "SQL built by string concatenation",
     "start_line": 10, "end_line": 10, "category": "security", "severity": "high"}
  ],
  "skipped": [
    {"path": "generated/client.ts", "reason": "generated code"}
  ]
}
```

Pass the payload as a file with `--findings-file <path>` (what `diff` prints), or with
`--findings`, which takes inline JSON, `'@<path>'` or `-` for stdin. Prefer the file: a long
inline payload can exceed the operating system's command-line limit (about 32k characters on
Windows), and PowerShell strips the double quotes out of inline JSON. A file or stdin may be
UTF-8, with or without a byte-order mark, or UTF-16 with one; anything else is refused with
exit 2 and a note to re-save the file as UTF-8, never guessed at.

Windows PowerShell 5.1 specifics (PowerShell 7 writes and pipes UTF-8 and needs none of this):

- `ConvertTo-Json ... > findings.json` and `Out-File` write UTF-16LE with a byte-order mark:
  accepted. Plain `Set-Content` writes the ANSI code page: refused; add `-Encoding utf8`.
- Quote the `@` form, `--findings '@findings.json'`. A bare leading `@` is PowerShell's
  splatting operator and the line does not parse. `--findings-file` needs no quoting.
- A pipe into `--findings -` is encoded with `$OutputEncoding`, which is ASCII by default:
  every non-ASCII character arrives as `?`, the record succeeds, and the gap text is wrong,
  with nothing the script can detect. Use `--findings-file`, or set
  `$OutputEncoding = [System.Text.UTF8Encoding]::new()` before piping.

*Which bundle.* `--bundle` names it. Without the flag, `record` uses the one live bundle
beside the manifest: not yet recorded and less than 24 hours old. Several live bundles are
refused (exit 2, listing them), because guessing would check your findings against another
agent's review. A bundle older than 24 hours is ignored with a notice. A successful `record`
closes its bundle, so it neither gates nor is checked against the next `record` (the file
stays until you delete it, with the skip reasons under `closed.skipped` and the name of the
record it produced under `closed.record`). A refused `record` leaves the bundle open and
writes nothing.

*The record.* Every successful `record` writes
`.admin-console/ocr-record-<timestamp>-<pid>.json`: the findings and skips as submitted, and,
when a bundle was used, the bundle's mode, refs, agent and reviewable files. That file, not
the bundle, is what the gaps cite, and the summary line names it (`record=<path>`).

*Coverage.* With a bundle, every bundled file must end in one list or the other (a skip
entry needs a `path` and a `reason`), and only bundled paths may appear in either — silent
omissions and invented paths are both refused (exit 2, nothing persisted), and the summary
reports `total_files`, `reviewed_files`, `skipped_files` and `coverage_rate`. When the same
path appears twice in the bundle (workspace mode), one review or skip covers both entries
and `coverage_rate` counts it once. Without a bundle there is nothing to check paths
against: the payload is taken as given and the summary says `coverage: unknown`, never a
percentage.

*Gaps.* Each finding becomes a `gaps[]` entry via `add --kind gap` (id
`ocr-<date>-<slug>-<n>`, with `-r2`, `-r3`... when that id is taken; severity mapped 1:1 —
both sides use critical/high/medium/low; an unknown severity is refused, an unknown
category is recorded as `other` with a notice) and appears in `emit --format gap-report`
like any other finding. Every such gap carries `source: "ocr"` and never an `entity` or
`capability` field: the validator ignores OCR-sourced gaps when it decides whether a
lifecycle state has an accepted escape, so review text that happens to mention a state
name cannot silence a lifecycle check. Each gap also carries `fingerprint`, 12 hex
characters identifying `(path, start_line, content)`: a finding that is already an open,
accepted or blocked gap is not written again (the summary says `N already recorded`, and
the exit stays 1 because findings still exist), while a gap marked `fixed` that comes back
is recorded anew. To accept a finding, `set` its `status` to `accepted` and write your own
accepted-risk `rationale`; the fingerprint lives in its own field, so the next run still
recognises it (a whole-object `set` that drops the field makes it new again). Gaps recorded
before the field existed carry the key at the end of their rationale as `fp:<12 hex>`,
which is honoured for as long as that text stays. Findings are written as one atomic batch,
so a refused `record` leaves the manifest untouched and re-running after a refusal cannot
duplicate gaps.

*What to commit.* Everything the bridge writes sits beside the manifest in
`.admin-console/`:

| File | What it is | Commit it? |
|---|---|---|
| `ocr-bundle*.json` | one delegate review's work order, written by `diff` | No. Transient and git-ignored; delete it once recorded |
| `ocr-record-*.json` | what a `record` submitted, with the bundle's file list | Yes: the gaps cite it |
| `ocr-diff-*.json`, `ocr-scan-*.json` | the raw `ocr` reply of an endpoint `diff` or `scan` | Yes: the gaps cite it |

Commit the evidence with the manifest, so a clone or a CI checkout can follow every OCR gap
to the review that produced it.

**3. `scan` — Audit mode.** Full-file review of code no diff covers (endpoint engine
only; delegate mode has no full-file scan):

```bash
python <skill-dir>/scripts/code_review.py scan --manifest <project-root>/.admin-console/manifest.json --path src/admin
```

### Exit codes

| Code | Meaning |
|---|---|
| 0 | Clean, or nothing reviewable (an endpoint review `ocr` reports as `skipped` included), or `ocr` absent (nothing was blocked) |
| 1 | Findings recorded as gaps (or already recorded from an earlier run) — read them |
| 2 | Usage/IO/validation failure: a refused `add` (gaps go in as one all-or-nothing batch — the manifest is left unmodified); a wrong `ADMINWRIGHT_OCR_BIN`, missing rule file or unresolvable Windows shim; a timeout; an endpoint review or scan that failed (nothing recorded). Also one that ended `partial` (scan: `completed_with_errors`) or in a state the bridge does not know: its findings *were* recorded, and the files `ocr` could not review are printed with their reasons |
| 3 | Intentionally unused — capability claim conflicts do not apply to this script |

`--strict` flips "ocr absent" from exit 0 to exit 2, for callers that want the pass
mandatory.

### Windows: `ocr.cmd` and cmd.exe

The npm install puts an `ocr.cmd` shim on PATH. A `.cmd` file runs under `cmd.exe`, which
executes `&`, `%`, `^` and `!` in a repository file name as commands — a tracked file named
`src/x&pwned&y.py` would have run `pwned` during a review. The bridge therefore never hands
the shim any argument. It reads the shim, finds the `node_modules/@alibaba-group/open-code-review/.../ocr.js`
entry point it launches, and runs `node ocr.js <args>` directly, so no shell parses the
file names. If the command cannot be resolved that way (a hand-written `.cmd`/`.bat`, a
missing entry point, no `node`), it is refused with exit 2 and nothing is executed; set
`ADMINWRIGHT_OCR_BIN` to a native executable to bypass the launcher. The npm install does
not create an `ocr.exe`; its native binary is `opencodereview.exe` inside the package's
per-platform dependency (for example
`<npm prefix>/node_modules/@alibaba-group/open-code-review/node_modules/@alibaba-group/ocr-win32-x64/bin/opencodereview.exe`),
which the launcher starts with the same arguments.

### Review rules

`assets/adminwright-ocr-rules.json` is applied by default (via OCR's `--rule`). Schema,
verified against the `ocr` CLI: `rules[]` entries with a `path` glob and a `rule` text;
**the first matching path wins, and a custom rule replaces the system language rules for
that file** — so every shipped rule starts by asking for the normal language review,
then adds the admin-console contract (server-side authorization with default deny, audit
events with actor/target/reason, per-row tenant policy on exports and bulk reads,
idempotency on financial/messaging/provisioning commands, impersonation limits, and so
on). `exclude[]` skips files entirely (DesignArchitect's state dir, the manifest dir).

Adjust the globs to your project's layout — if routes live in `src/app/api/**` rather
than `**/routes/**`, edit the paths. Check what applies to a file with:

```bash
ocr rules check --rule assets/adminwright-ocr-rules.json src/your/file.py
```

### Which agent runs it

`adminwright-security` and `adminwright-qa` carry the pass in "The pass": security over
the spine (`scan` needs an endpoint configured; in delegate mode use `diff`), qa over each
finished domain (`diff` mode on the domain's changes). In delegate mode the dispatched
agent performs the review itself and returns findings through `record` — the independence
rule applies: review what you did not implement. Pass `--agent <your id>` to `diff` so your
bundle is yours alone.

### Troubleshooting

- **"ADMINWRIGHT_OCR_BIN is set to ... but no such executable exists"** — the variable
  names no file. Fix the path, or unset it to use `ocr` from PATH.
- **"refusing to run ... .cmd/.bat"** (Windows) — the command is a `.cmd`/`.bat` that is not
  the npm shim for `@alibaba-group/open-code-review`, or its `ocr.js` entry point or `node`
  cannot be found. Reinstall with `npm install -g @alibaba-group/open-code-review`, point
  `ADMINWRIGHT_NODE_BIN` at your `node`, or point `ADMINWRIGHT_OCR_BIN` at a native
  executable. See "Windows: `ocr.cmd` and cmd.exe" above.
- **Windows: "%1 is not a valid Win32 application"** — `ADMINWRIGHT_OCR_BIN` pointed at the
  extensionless npm shell shim (`ocr` beside `ocr.cmd`), and no `ocr.cmd`/`ocr.exe`
  sibling was found. Point it at `ocr.cmd`, or unset it and let the script resolve `ocr`
  from PATH. The npm install does not create an `ocr.exe`.
- **"ocr diff did not complete (...)" / "ocr scan did not complete (...)"** — the run
  failed and nothing was recorded; the rest of the line and any `ocr:` lines are `ocr`'s own
  words. With **"hint: the LLM provider is missing, unreachable or refusing requests"** it
  failed at the provider: `--engine ocr` or `scan` without a configured provider (configure
  it, or use the delegate default), a wrong URL or key, or a gateway that is down. Without
  the hint it is something else, such as an unknown `--from`/`--to`/`--commit` ref.
- **"ocr could not review N file(s) of M selected; coverage is incomplete"** — the run
  ended `partial`. Findings for the other files were recorded; each failed file is listed
  with `ocr`'s classification (`provider`, `timeout`, `budget`, ...). A provider failure
  while other files succeeded is usually a rate limit or an upstream error: re-run later —
  findings already recorded are not duplicated.
- **"ocr reported ..., a state this bridge does not know"** — a newer `ocr` uses a word the
  bridge has not seen. Its findings were recorded; exit 2 because nothing confirms the
  coverage. Compare `ocr --version` with the versions named under "The three commands".
- **"ocr did not finish within N seconds"** — `ocr` and its children were stopped. Narrow the
  review, or raise `--timeout` / `ADMINWRIGHT_OCR_TIMEOUT`.
- **"the --rule file ... does not exist" / "the shipped rules ... are missing"** — the review
  is refused rather than run without the admin-console rules. Fix the path, or restore
  `assets/adminwright-ocr-rules.json`.
- **"nothing to review"** — the workspace has no changes, or only files the rules exclude
  (`ocr` reports that as `skipped`); pass `--from/--to` or `--commit` for a range, or use
  `scan`.
- **"the findings file ... is not valid UTF-8" / "standard input is not valid UTF-8"** — the
  payload is in a legacy code page, typically from Windows PowerShell 5.1's plain
  `Set-Content`. Write it with `Set-Content -Encoding utf8`, `Out-File` or `>` and pass
  `--findings-file`; nothing was recorded.
- **"several review bundles are waiting to be recorded"** — more than one live bundle sits
  beside the manifest (concurrent agents, or an abandoned run). Pass `--bundle <path>` with
  the path your `diff` printed.
- **"the review bundle ... is already closed"** — that bundle was recorded (or superseded)
  already. Re-run `diff` for a fresh one.
- **A refused `record`** — a bundled file was neither reviewed nor skipped, a finding used
  a path outside the bundle, or a finding failed schema validation (severity must be
  critical/high/medium/low). Nothing was persisted — the whole batch is all-or-nothing —
  so fix the payload and re-run; a retry can never duplicate gaps.

## Advisory OCR review of this repository

Pull requests to adminwright itself get a non-blocking OCR review via
[.github/workflows/ocr-advisory.yml](.github/workflows/ocr-advisory.yml) — findings as
PR comments, never a required check. The job always runs; its review step is skipped
until the `OCR_LLM_URL`/`OCR_LLM_TOKEN` secrets exist. An `if` cannot read the `secrets`
context, so the job `env` holds only `HAS_OCR`, a true/false derived from them, and the
secret values are passed to the review step alone, in its `with:` (the composite action
maps them into the environment of its own `ocr review` step only). The action is pinned to
a release SHA, and its `ocr_version` input pins the npm package it installs to the same release
(`1.9.6`), so the workflow no longer installs `latest` at run time; `OCR_NO_UPDATE=1` in
the job environment stops the launcher's own background update, which would otherwise run
`npm i -g` for the newest version even after this pin. Residual exposure, stated plainly:
the package is identified by version, not by a pinned integrity hash (npm versions are
immutable, but a compromised registry account or mirror is not ruled out); its native
binary comes from a per-platform optional package at the same version or, when that is
unavailable, from the release's GitHub assets checked against a checksum file published
beside them (same origin: it detects corruption, not a compromised release); and the
reviewed PR contents plus `OCR_LLM_TOKEN` are visible to that code and to the configured
LLM endpoint. Bump the action SHA and `ocr_version` together, by hand, and read the release
notes when you do: Dependabot ignores this action because it would move the SHA without the
input, and `tests/test_code_review.py` (`AdvisoryPins`) fails when the SHA's version comment,
`ocr_version` and the `ocr-smoke` CI pin disagree. See [CONTRIBUTING.md](CONTRIBUTING.md)
"Advisory OCR review".
