# The manifest CLI

## Contents

1. Running it
2. Commands
3. Adding and changing entries
4. Shell-safe forms
5. The write guard
6. Claims and locks
7. Checking a reviewer pass
8. Files, commits and monorepos

## Running it

```text
python <skill-dir>/scripts/admin_console_manifest.py <command> ...
```

Use `py -3` or `python3` where `python` is not on PATH. Python 3.9 or later, standard library only. Run `<command> --help` for the arguments; do not read the script's source. Without Python, copy `assets/admin-console.manifest.template.json` and fill it against `assets/admin-console.manifest.schema.json`, and say in the completion report that no release gate ran: there is no `validate`, no `coverage` and no write guard.

Exit codes: 0 clean; 1 the manifest was read and has findings at error severity; 2 a usage or IO failure, or a write the guard refused (the manifest is unchanged); 3 a claim conflict.

## Commands

| Command | Does |
|---|---|
| `init` | creates the manifest, its schema copy, `.gitignore` and an assumed `dec.profile` decision |
| `migrate [--write]` | upgrades a 1.0 manifest; prints the result unless `--write` |
| `validate --phase plan\|release` | the gate; `--json` for machine output, `--profile` to preview another tier |
| `coverage` | structural gaps only (unused roles, unlinked screens and capabilities, unreachable lifecycle states); reads no files |
| `report` | a summary of counts and statuses |
| `emit --format <f> --out <path>` | `gap-report`, `authz-matrix`, `test-plan`, `seed-plan`, `nav-map`, `operator-handbook`, `rule-matrix` |
| `add`, `set`, `remove`, `rename` | the only way to change the manifest |
| `claim`, `release-claim` | take and give back capabilities |
| `review-scope` | checks from git what a reviewer pass changed |
| `harvest`, `promote`, `store`, `lesson` | the learning loop; see [skill-evolution.md](skill-evolution.md) |

## Adding and changing entries

`add --kind <kind> --json '<object or array>'` appends to a collection; an array is one all-or-nothing batch. A capability names its entity with `--entity`. Kinds: `agent`, `capability`, `decision`, `entity`, `feedback`, `gap`, `integration`, `queue`, `role`, `screen`, `static`.

`set --path <path>` changes one field. A path is written the way `validate` prints it, so a finding's path pastes in as it stands: `entities[user].capabilities[user.suspend].status`, `roles[0].name`, `qualityGates[build].evidence[0]`.

| Form | Effect |
|---|---|
| `--value <text>` | typed by the field: lists take comma-separated text or a JSON array, objects take JSON, strings are stored as written |
| `--value-file <path>` | the value read from a file; a UTF-8 or UTF-16 byte-order mark is accepted |
| `--append` | adds items to a list, skipping those already present; the union is taken under the manifest lock, so agents adding evidence at the same time keep each other's entries |
| an object value | merged into the existing object; `--replace` swaps it wholesale and lists the dropped keys |
| `--delete` | removes the field or list element, such as a stray key |

Keys the schema does not declare are reported as `unknown-field`; prefix a key with `x-` to keep project-specific data without the finding. Backslashes in evidence paths are stored as `/`.

`remove --kind <kind> --id <id>` is refused while anything still refers to the item, and the message lists every reference, including ones no rule checks, such as a research source's `appliedTo` or a gap's `capability`. `rename --kind <kind> --id <old> --to <new>` changes the id and every reference to it, lock files included. Never edit `manifest.json` by hand: a whole-file write discards every change another agent made since you read it.

## Shell-safe forms

- Write the JSON with your harness's file tool to `.admin-console/<agent-id>.payload.tmp`, then pass `--json-file` (or `set --value-file`). This works in every shell, and `init`'s `.gitignore` already ignores `*.tmp`.
- For a list, prefer comma-separated text with `--append`: `--value evidence/a.log,evidence/b.log`.
- PowerShell 5.1 strips the double quotes inside single-quoted JSON and splits values containing spaces, so inline `--json '{...}'` fails there. `--json '@file.json'` also works, but quote the `@` argument: unquoted, PowerShell reads `@file` as splatting. Piping JSON into `--json -` loses non-ASCII text unless `$OutputEncoding = [System.Text.UTF8Encoding]::new()` is set first; a file avoids the question.
- Capture a gate's log with the recipe in [verification.md](verification.md#capturing-gate-output) (`gate_run` in bash, `Invoke-Gate` in PowerShell). It records the command and its exit code, which is what a gate is judged on. In PowerShell 5.1, `>` writes UTF-16 and `Out-File -Encoding utf8` garbles non-ASCII output and adds error records, so use neither.

<!-- doc-test: skip -->
```text
Set-Content -Encoding utf8 .admin-console/impl-1.payload.tmp '{"id":"ops","name":"Ops team","responsibilities":["Admin"],"scopes":["all"]}'
python <skill-dir>/scripts/admin_console_manifest.py add --manifest <project-root>/.admin-console/manifest.json --kind role --json-file .admin-console/impl-1.payload.tmp
```

## The write guard

Every `add`, `set`, `remove`, `rename` and `claim` validates the manifest at the plan phase before and after the change, and refuses (exit 2, manifest unchanged) when the change introduces a new error. The following are refused at write time:

- bad ids and unknown references;
- placeholder values and unregistered static values;
- `contested` with no gap naming the capability;
- `reviewed` while an open critical or high gap targets the capability;
- `contested` moved straight to `reviewed`.

An unfinished model is not refused: completeness rules (a role with no capability yet, an entity with no query) warn until `--phase release`.

A rule that judges one field against other entries refuses only the write that changed that field. Filing a gap against a capability already marked `reviewed`, or marking a contest's gap `fixed`, prints a warning instead, so a finding can always be filed and a fix always recorded.

`--allow-invalid` writes anyway and prints every error it let through. The release gate still fails on each of them.

## Claims and locks

<!-- doc-test: skip -->
```text
python <skill-dir>/scripts/admin_console_manifest.py claim --manifest <project-root>/.admin-console/manifest.json --agent <agent-id> --role <role> --capability <capability-id> [--capability <capability-id> ...]
python <skill-dir>/scripts/admin_console_manifest.py release-claim --manifest <project-root>/.admin-console/manifest.json --agent <agent-id> [--capability <capability-id>]
```

- The first claim registers the agent.
- A builder's claim (`implementer`, `architect`) writes `locks/<capability>.lock` and sets `owner`. It also sets `implementedBy` when that is empty; an implementer's claim replaces an architect's.
- A reviewer's claim writes `locks/<capability>.<role>.lock` and changes neither field.
- A builder excludes everyone: nothing is reviewed while it is being built. Reviewers of different roles review one capability in parallel; two reviewers of the same role do not.
- An id that has built something cannot claim as a reviewer.
- Exit 3 names the holder, from the lock file or from `agents[]` (lock files are git-ignored, so the manifest is checked too).

`release-claim` without `--capability` releases everything the agent holds. It blanks `owner`, moves the capability to the agent's `previouslyOwned`, and sets the agent `done` when nothing is left. `implementedBy` is never cleared. `claim --force-steal --reason '<why>'` takes a capability from an agent that stalled. It is recorded on both agents; never steal from an agent that may still be writing.

| Lock | Where | Freed by |
|---|---|---|
| Write lock | `.admin-console/.lock`, held for milliseconds by each write | the operating system, when the holding process exits. A write that waits 30 seconds exits 2 naming the holder's pid. The file itself stays; deleting it frees nothing. On a filesystem without OS locks, `.lock.soft` is used instead. It is removed on release; delete one left by a crashed process by hand, once that process is gone |
| Builder claim | `locks/<capability>.lock` | `release-claim`, or `--force-steal` |
| Reviewer claim | `locks/<capability>.<role>.lock` | the same |
| Manifest claim | `agents[].ownsCapabilities` of an `active` agent, and `owner` | `release-claim`, the holder's status set to `done` or `handed-off`, or `--force-steal` |

A claim is refused on either of the last rows. Deleting a lock file does not free a capability the manifest still assigns to an active agent.

## Checking a reviewer pass

Tool restrictions on reviewers are advisory in Claude Code and absent in other harnesses. `review-scope` checks the working tree itself:

<!-- doc-test: skip -->
```text
python <skill-dir>/scripts/admin_console_manifest.py review-scope --manifest <project-root>/.admin-console/manifest.json --agent <reviewer-id> --start
python <skill-dir>/scripts/admin_console_manifest.py review-scope --manifest <project-root>/.admin-console/manifest.json --agent <reviewer-id>
```

- Run `--start` when the pass is dispatched. It records what was already uncommitted, so another agent's unfinished work is not blamed on the reviewer.
- Run the check when the pass returns. A reviewer may change:
  - the manifest and the tool's working files;
  - its own worklog;
  - files the manifest cites as evidence (logs, reports, screenshots; never source or project configuration);
  - `docs/admin-*.md`;
  - for `qa` only, test files that did not exist when the pass began.
- Exit 1 lists everything else. Revert it, or have an implementer adopt it under a claim, and file the gaps.
- `--base <commit>` compares with a commit instead of `--start`. `--ignore <glob>` excuses paths a concurrently running builder changed. `--role` names the pass when the agent is not in `agents[]`.

## Files, commits and monorepos

Commit the manifest, its schema copy, `worklog/`, the reports you `emit`, and the `ocr-record`, `ocr-diff` and `ocr-scan` files that gaps cite. Do not commit `locks/`, `.lock`, `.lock.soft`, `*.tmp` or `ocr-bundle*.json`. `init` writes `.admin-console/.gitignore` with those lines, adding them to an existing file if there is one.

With several consoles in one repository, keep one manifest per console: `init --project-root <repo> --manifest apps/<console>/console.json`. It records `projectRoot`, so every command resolves evidence against the repository, and evidence paths stay repository-relative. Keep the `platform.id` that `init` writes: `harvest` counts each console once by it. A manifest without one gets a warning; add it with `set --path platform.id --value <console-slug>`.
