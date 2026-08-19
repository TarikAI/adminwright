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
4. Records `graph.json`, `holes.json`, and `coverage.md` as `evidence[]` on the screens
   they cover.

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

### The three commands

All take `--manifest <project-root>/.admin-console/manifest.json`. `--project-root`
points at the git repository under review when it differs from the manifest's project.

**1. `diff` — Build/Repair mode.** Reviews the current changes.

```bash
python <skill-dir>/scripts/code_review.py diff --manifest <project-root>/.admin-console/manifest.json
```

Delegate engine (default, no OCR-side API key): writes the review bundle to
`.admin-console/ocr-bundle.json` — mode, refs, every reviewable file keyed by
`(path, status)`, rule groups, instructions — and prints the `record` command. **The
reviewing agent then reviews each file itself** (per the bundled rules and its own
reading of the diffs) and returns findings through `record`. Nothing was reviewed until
that happens; `diff` alone records nothing.

Endpoint engine (`--engine ocr`, needs provider config): runs
`ocr review --audience agent --format json` itself, keeps the raw JSON as evidence, and
persists findings directly. Range and commit modes: `--from main --to feature`, or
`--commit <hash>`.

**2. `record` — persist delegate findings as gaps.**

```bash
python <skill-dir>/scripts/code_review.py record --manifest <project-root>/.admin-console/manifest.json --findings '{
  "findings": [
    {"path": "src/orders.py", "content": "SQL built by string concatenation",
     "start_line": 10, "end_line": 10, "category": "security", "severity": "high"}
  ],
  "skipped": [
    {"path": "generated/client.ts", "reason": "generated code"}
  ]
}'
```

`--findings` accepts inline JSON, `@file`, or `-` (stdin). Every bundled file must end in
one list or the other, and only bundled paths may appear in either — silent omissions and
invented paths are both refused (exit 2, nothing persisted). A malformed payload (a
finding without a path, an unreadable `@file`, a corrupt bundle) also exits 2 with an
`ERROR:` message, never a traceback. Each finding becomes a `gaps[]` entry via
`add --kind gap` (id `ocr-<date>-<slug>-<n>`, severity mapped 1:1 — both sides use
critical/high/medium/low; an unknown severity is refused, an unknown category is recorded
as `other` with a notice) and appears in `emit --format gap-report` like any other
finding. Findings are written as one atomic batch, so a refused `record` leaves the
manifest untouched and re-running after a refusal cannot duplicate gaps. When the same
path appears twice in the bundle (workspace mode), one review or skip covers both entries
and `coverage_rate` counts it once.

**3. `scan` — Audit mode.** Full-file review of code no diff covers (endpoint engine
only; delegate mode has no full-file scan):

```bash
python <skill-dir>/scripts/code_review.py scan --manifest <project-root>/.admin-console/manifest.json --path src/admin
```

### Exit codes

| Code | Meaning |
|---|---|
| 0 | Clean, or nothing reviewable, or `ocr` absent (nothing was blocked) |
| 1 | Findings recorded as gaps — read them |
| 2 | Usage/IO/validation failure, including a refused `add` (gaps go in as one all-or-nothing batch — the manifest is left unmodified) |
| 3 | Intentionally unused — capability claim conflicts do not apply to this script |

`--strict` flips "ocr absent" from exit 0 to exit 2, for callers that want the pass
mandatory.

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
the spine (`scan` mode suits it), qa over each finished domain (`diff` mode on the
domain's changes). In delegate mode the dispatched agent performs the review itself and
returns findings through `record` — the independence rule applies: review what you did
not implement.

### Troubleshooting

- **Windows: "%1 is not a valid Win32 application"** — `ADMINWRIGHT_OCR_BIN` pointed at
  the extensionless npm shell shim. Point it at `ocr.cmd`/`ocr.exe`, or unset it and let
  the script resolve `ocr` from PATH.
- **"endpoint engine needs `ocr config provider`"** — you used `--engine ocr` or `scan`
  without configuring a provider; configure it, or use the delegate default.
- **"nothing to review"** — the workspace has no changes; pass `--from/--to` or
  `--commit` for a range, or use `scan`.
- **A refused `record`** — a bundled file was neither reviewed nor skipped, a finding used
  a path outside the bundle, or a finding failed schema validation (severity must be
  critical/high/medium/low). Nothing was persisted — the whole batch is all-or-nothing —
  so fix the payload and re-run; a retry can never duplicate gaps.

## Advisory OCR review of this repository

Pull requests to adminwright itself get a non-blocking OCR review via
[.github/workflows/ocr-advisory.yml](.github/workflows/ocr-advisory.yml) — findings as
PR comments, never a required check, skipped until the `OCR_LLM_URL`/`OCR_LLM_TOKEN`
secrets exist (they are mapped into job `env`, because a job-level `if` cannot see the
`secrets` context; the review step gates on `env`). The action is pinned to a release
SHA. See [CONTRIBUTING.md](CONTRIBUTING.md) "Advisory OCR review".
