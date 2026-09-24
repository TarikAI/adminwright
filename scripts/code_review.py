#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TarikAI
"""Optional Open Code Review (OCR) bridge for adminwright.

Standard library only. Runs on Windows and POSIX. Never required: when the
`ocr` CLI is absent this script exits 0 with a notice (exit 2 under --strict),
so the skill keeps its offline, dependency-free contract.

Commands: diff, scan, record.

diff    Build/Repair mode. --engine delegate (default, no OCR-side LLM): runs
        `ocr delegate preview/rule --format json`, writes one review bundle
        per run to .admin-console/ocr-bundle-<agent-or-timestamp>-<pid>.json
        (never a shared file, so concurrent runs cannot clobber each other),
        prints the exact `record --bundle ...` command, and hands the review
        itself to the calling agent, which returns findings through `record`.
        --engine ocr: runs `ocr review --audience agent --format json` and
        persists findings directly; requires prior `ocr config provider` and
        `ocr config model`. ocr reports the run as complete, partial, failed
        or skipped (`status`, and `manifest.terminal_state` since ocr 1.8.5).
        complete: findings recorded (exit 1, or 0 when there are none).
        partial: the findings that exist are recorded, every file ocr could
        not review is printed with its reason, exit 2. failed, or a non-zero
        ocr exit: nothing is recorded, exit 2; it is never "0 findings
        recorded". skipped: nothing was selected for review, exit 0.
scan    Audit mode: full-file review of code no diff covers, via `ocr scan
        --audience agent --format json`. Endpoint engine only; delegate mode
        has no full-file scan. scan has no run manifest: its `status` is
        success, completed_with_warnings or completed_with_errors, and files
        it could not scan are named in `warnings[]`; they get the same
        verdicts as a review (completed_with_errors is partial).
record  Persist delegate-mode findings as manifest gaps[] through
        admin_console_manifest.py `add --kind gap` — one all-or-nothing
        batch, so a refusal leaves the manifest unmodified. The payload
        comes from --findings-file PATH or --findings (inline JSON, '@file',
        or - for stdin); a file or stdin may be UTF-8, with or without a
        byte-order mark, or UTF-16 with one. With a bundle
        (--bundle, or the one live unrecorded bundle beside the manifest),
        every bundled (path, status) must end reviewed or explicitly skipped
        with a reason, and findings/skips for paths outside the bundle are
        refused; silent omissions and invented paths alike. A successful
        record closes its bundle (kept as evidence, no longer discoverable);
        a bundle older than 24 hours is ignored. Without a bundle nothing can
        be checked, and the summary says `coverage: unknown` instead of
        claiming 100%. Findings already recorded as live gaps (same path,
        start_line and content) are not written twice.

Findings use OCR's comment schema: path, content, start_line, end_line,
category (bug|security|performance|maintainability|test|style|documentation|
other), severity (critical|high|medium|low). Severity must be exact — it maps
1:1 onto gap severities; an unknown category degrades to "other" with a notice
rather than refusing a real finding over taxonomy drift. File coverage and
line anchoring are deterministic; the findings themselves are judgments. They
are evidence for the gap report, never a substitute for validate, coverage,
policy tests, or the adversarial pass. A clean run is a floor, not a proof.

Every gap written carries `source: "ocr"` and never an `entity` or
`capability` field: the validator ignores ocr-sourced gaps when deciding
whether a lifecycle state is covered by an accepted escape, and a gap naming
an entity or capability would count as one.

--project-root defaults to the manifest's project root: the optional
top-level `projectRoot` (a path relative to the directory containing the
manifest), else that directory's parent. Each ocr call is bounded by
--timeout (default 900 s, or ADMINWRIGHT_OCR_TIMEOUT).

Exit codes: 0 clean or nothing reviewable, 1 findings recorded, 2 usage/IO/
validation failure (including a refused add, which leaves the manifest
unmodified — gaps are written as one atomic batch — an endpoint review that
failed, and one that left files unreviewed, whose findings for the other
files are recorded). Exit 3 is intentionally unused: capability claim
conflicts do not apply to this script.

ADMINWRIGHT_OCR_BIN overrides the ocr executable path (tests and exotic
installs); otherwise the script uses `ocr` from PATH. A value that names no
file is a configuration error (exit 2), not "ocr absent".

Windows: the npm install puts an `ocr.cmd` shim on PATH. A .cmd/.bat file runs
under cmd.exe, which would interpret `&`, `%`, `^` and `!` in a repository
file name as commands, so the shim is never handed our arguments. It is read,
and `node <ocr.js>` is run directly instead (node from ADMINWRIGHT_NODE_BIN,
the shim's own directory, or PATH); a shim that cannot be resolved that way is
refused with exit 2 and a pointer to ADMINWRIGHT_OCR_BIN. Every ocr call also
sets OCR_NO_UPDATE=1: the launcher otherwise contacts npm and can run
`npm i -g` in the background on each invocation.
"""

from __future__ import annotations

import argparse
import codecs
import hashlib
import json
import math
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_SCRIPT = SKILL_ROOT / "scripts" / "admin_console_manifest.py"
DEFAULT_RULE_PATH = SKILL_ROOT / "assets" / "adminwright-ocr-rules.json"

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_USAGE = 2

SEVERITIES = ("critical", "high", "medium", "low")
CATEGORIES = (
    "bug",
    "security",
    "performance",
    "maintainability",
    "test",
    "style",
    "documentation",
    "other",
)

CONTENT_KEYS = ("content", "message", "body")
LINE_KEYS = ("start_line", "startLine", "line")


class ReviewError(Exception):
    """A usage or IO problem: always maps to exit 2."""


def stderr(message):
    print(message, file=sys.stderr)


ENV_OCR_BIN = "ADMINWRIGHT_OCR_BIN"
ENV_NODE_BIN = "ADMINWRIGHT_NODE_BIN"
ENV_TIMEOUT = "ADMINWRIGHT_OCR_TIMEOUT"

# A review can legitimately run for minutes (endpoint mode calls an LLM per
# file); this only bounds a hang. --timeout / ADMINWRIGHT_OCR_TIMEOUT raise it.
DEFAULT_TIMEOUT_SECONDS = 900

# The most either may ask for. A day is far past any real review, and it keeps
# the value inside what the platform's waits accept: `--timeout 1e30` used to
# make communicate() raise OverflowError, which left ocr running and exited 1
# ("findings recorded") with a traceback. A non-finite value is refused.
MAX_TIMEOUT_SECONDS = 24 * 3600

# A review bundle nobody recorded within this long is treated as abandoned.
BUNDLE_TTL_SECONDS = 24 * 3600

# Files Windows hands to cmd.exe when CreateProcess is asked to run them.
CMD_SHIM_SUFFIXES = (".cmd", ".bat")

# The entry-point path inside an npm cmd-shim, in both the current
# (`"%dp0%\node_modules\...\ocr.js"`) and the npm 6 (`"%~dp0\node_modules\...`)
# spellings. Percent signs inside the captured path are not expanded: a path
# that needs environment expansion is not one we can resolve safely.
SHIM_SCRIPT = re.compile(r'"%~?dp0%?[\\/]+([^"\r\n%]+?\.[cm]?js)"', re.IGNORECASE)


def ocr_executable():
    """The ocr command to run, or None when it is not installed.

    An ADMINWRIGHT_OCR_BIN that names no file is a misconfiguration and raises;
    it must not degrade to the "ocr is not on PATH" notice (exit 0), which
    would hide the typo behind a pass that reviewed nothing.
    """
    override = os.environ.get(ENV_OCR_BIN, "").strip()
    if override:
        if Path(override).is_file():
            return override
        found = shutil.which(override)  # a bare command name that is on PATH
        if found:
            return found
        raise ReviewError(
            ENV_OCR_BIN + " is set to '" + override + "' but no such executable exists"
            " — fix the path, or unset it to use `ocr` from PATH"
        )
    return shutil.which("ocr")


# The npm package's native binary. There is no ocr.exe: npm installs ocr.cmd,
# ocr.ps1 and an sh shim, and bin/ocr.js starts this file from the per-platform
# package (@alibaba-group/ocr-win32-x64/bin/ on x64 Windows).
NATIVE_BINARY_HINT = (
    "the package's native opencodereview.exe (<npm prefix>/node_modules/@alibaba-group/"
    "open-code-review/node_modules/@alibaba-group/ocr-win32-x64/bin/opencodereview.exe)"
)


def refuse_cmd_shim(shim, reason):
    return ReviewError(
        "refusing to run '" + str(shim) + "': Windows would hand a .cmd/.bat file to"
        " cmd.exe, which executes '&', '%', '^' and '!' in a repository file name as"
        " commands. " + reason + " Set " + ENV_OCR_BIN + " to a native executable, such as "
        + NATIVE_BINARY_HINT + ", or to the ocr.cmd of a standard npm install with `node`"
        " on PATH."
    )


def find_node(shim_dir):
    """node for a resolved npm shim: override, the shim's own node.exe, then PATH.

    That is the order the npm shim itself uses (plus the override). Returns
    None when node cannot be found; a wrong ADMINWRIGHT_NODE_BIN raises.
    """
    override = os.environ.get(ENV_NODE_BIN, "").strip()
    if override:
        found = override if Path(override).is_file() else shutil.which(override)
        if not found:
            raise ReviewError(
                ENV_NODE_BIN + " is set to '" + override + "' but no such executable exists"
            )
        return str(found)
    beside = Path(shim_dir) / "node.exe"
    if beside.is_file():
        return str(beside)
    found = shutil.which("node")
    if found and Path(found).suffix.lower() not in CMD_SHIM_SUFFIXES:
        return found
    return None


def resolve_cmd_shim(shim):
    """[node, ocr.js] for an npm-generated ocr.cmd; ReviewError when it is not one.

    The shim only exists to launch `node ocr.js %*`. Doing that ourselves keeps
    cmd.exe out of the path of every argument that comes from the repository.
    """
    shim = Path(shim)
    try:
        text = shim.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise refuse_cmd_shim(shim, "It could not be read (" + str(exc) + ").")
    script = None
    for match in SHIM_SCRIPT.finditer(text):
        relative = match.group(1).replace("\\", "/")
        if "open-code-review" in relative.lower():
            script = Path(os.path.abspath(str(shim.parent / relative)))
            break
    if script is None:
        raise refuse_cmd_shim(
            shim, "It is not an npm shim for @alibaba-group/open-code-review"
            " (no node_modules/.../ocr.js entry point in it)."
        )
    if not script.is_file():
        raise refuse_cmd_shim(shim, "Its entry point '" + str(script) + "' does not exist.")
    node = find_node(shim.parent)
    if not node:
        raise refuse_cmd_shim(shim, "`node` was not found next to it or on PATH.")
    return [node, str(script)]


def command_prefix(executable):
    """The argv prefix that runs ocr with no shell between us and its arguments."""
    if os.name != "nt":
        return [executable]
    suffix = Path(executable).suffix.lower()
    if suffix in CMD_SHIM_SUFFIXES:
        return resolve_cmd_shim(executable)
    if not suffix:
        # npm also drops an extensionless POSIX-shell shim beside ocr.cmd;
        # Windows cannot execute that one, so prefer its runnable sibling.
        for extension in (".cmd", ".exe"):
            sibling = Path(executable + extension)
            if sibling.is_file():
                return command_prefix(str(sibling))
    return [executable]


def ocr_timeout(args=None):
    """Seconds one ocr call may run: --timeout, else ADMINWRIGHT_OCR_TIMEOUT, else 900.

    Finite and positive, and at most MAX_TIMEOUT_SECONDS (a larger value is
    lowered to it, with a note).
    """
    value = getattr(args, "timeout", None)
    source = "--timeout"
    if value is None:
        raw = os.environ.get(ENV_TIMEOUT, "").strip()
        if not raw:
            return DEFAULT_TIMEOUT_SECONDS
        source = ENV_TIMEOUT
        try:
            value = float(raw)
        except ValueError:
            raise ReviewError(ENV_TIMEOUT + " must be a number of seconds, not '" + raw + "'")
    if not value > 0:  # NaN fails this too
        raise ReviewError("the ocr timeout must be a positive number of seconds")
    if math.isinf(value):
        raise ReviewError(
            source + " must be a finite number of seconds, at most " + str(MAX_TIMEOUT_SECONDS)
            + "; ocr runs are always bounded"
        )
    if value > MAX_TIMEOUT_SECONDS:
        stderr(
            "note: " + source + " " + format(value, "g") + " is above the "
            + str(MAX_TIMEOUT_SECONDS) + "-second maximum; using " + str(MAX_TIMEOUT_SECONDS)
        )
        return MAX_TIMEOUT_SECONDS
    return value


def kill_tree(proc):
    """Stop proc and everything it started.

    The npm launcher is a node process that spawns the native binary, so
    killing only the direct child would leave the review running (and, in
    endpoint mode, spending tokens) after the timeout has been reported.
    """
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True
            )
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        pass
    try:
        proc.kill()
    except OSError:
        pass


def run_bounded(command, env, timeout):
    """subprocess.run with a real timeout: on expiry the whole tree is stopped."""
    options = {}
    if os.name != "nt":
        options["start_new_session"] = True  # own process group, so killpg reaches children
    proc = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,  # an ocr prompt must fail, not hang an agent's harness
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        **options
    )
    try:
        stdout, errors = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        kill_tree(proc)
        try:
            proc.communicate(timeout=10)
        except (subprocess.TimeoutExpired, OSError, ValueError):
            pass
        raise
    except BaseException:
        # Ctrl-C, or anything communicate() itself raises (an OverflowError
        # from a wait the platform could not honour once left the review
        # running): whatever happens here, ocr and its children do not outlive it.
        kill_tree(proc)
        raise
    return subprocess.CompletedProcess(command, proc.returncode, stdout, errors)


def run_ocr(arguments, repo=None, trailing=None, timeout=None):
    """Build the ocr command: flags first, the positional file list last.

    Options placed after a greedy positional list are swallowed as positionals
    by some CLI parsers, so --repo must never trail the file list.
    """
    executable = ocr_executable()
    if not executable:
        raise ReviewError("the ocr CLI is not on PATH")
    command = command_prefix(executable) + [str(a) for a in arguments]
    if repo:
        command += ["--repo", str(repo)]
    if trailing:
        command += [str(a) for a in trailing]
    env = dict(os.environ)
    env["OCR_NO_UPDATE"] = "1"
    if timeout is None:
        timeout = ocr_timeout()
    try:
        return run_bounded(command, env, timeout)
    except subprocess.TimeoutExpired:
        raise ReviewError(
            "ocr did not finish within " + format(timeout, "g") + " seconds and was stopped;"
            " narrow the review, or raise --timeout / " + ENV_TIMEOUT
        )
    except OSError as exc:
        raise ReviewError(
            "could not execute '" + executable + "' (on Windows point " + ENV_OCR_BIN
            + " at the npm install's ocr.cmd or at " + NATIVE_BINARY_HINT
            + ", not at the extensionless npm shell shim): " + str(exc)
        )


def project_root_of(manifest):
    """The project root a manifest describes, as an absolute path.

    The optional top-level `projectRoot` (a path relative to the directory
    containing the manifest file; default `..`) decides it, so the parent of
    `.admin-console/` unless the manifest says otherwise. Read locally on
    purpose: this script never imports the manifest script.
    """
    manifest = Path(manifest)
    declared = ".."
    try:
        data = json.loads(manifest.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        data = None
    if isinstance(data, dict):
        value = data.get("projectRoot")
        if isinstance(value, str) and value.strip():
            declared = value.strip()
    return Path(os.path.abspath(str(manifest.parent / declared)))


def repo_for(args, manifest):
    """The directory handed to ocr as --repo: --project-root, else the manifest's root."""
    if getattr(args, "project_root", None):
        return Path(args.project_root)
    root = project_root_of(manifest)
    if not root.is_dir():
        raise ReviewError(
            "the manifest's project root '" + str(root) + "' does not exist;"
            " fix `projectRoot` in the manifest or pass --project-root"
        )
    return root


def parse_json(text, what):
    try:
        return json.loads(text)
    except ValueError:
        raise ReviewError("could not parse " + what + " output as JSON")


# What Windows tools write, for the --findings file and pipe. Windows
# PowerShell 5.1: `>` and Out-File write UTF-16LE with a byte-order mark;
# Set-Content writes the ANSI code page unless given -Encoding (utf8 adds a
# BOM); a pipe into a native program is encoded with $OutputEncoding, which is
# ASCII by default, so every non-ASCII character arrives as '?' before this
# script sees a byte. PowerShell 7 writes and pipes UTF-8 without a BOM.
DECODE_HINT_FILE = (
    " Re-save it as UTF-8 (a byte-order mark is fine); in Windows PowerShell 5.1 use"
    " `Set-Content -Encoding utf8` or `Out-File -Encoding utf8`."
)
DECODE_HINT_PIPE = (
    " Pipe UTF-8 (in Windows PowerShell 5.1 set"
    " `$OutputEncoding = [System.Text.UTF8Encoding]::new()` first, or non-ASCII text"
    " arrives as '?'), or write the JSON to a file and pass --findings-file <path>."
)
POWERSHELL_JSON_HINT = (
    " In PowerShell, double quotes inside single-quoted JSON are stripped before Python"
    " sees them: write the JSON to a file and pass --findings-file <path>."
)


def decode_text_bytes(data, source, hint):
    """Decode a findings payload the way Windows tools actually write it.

    The rules of admin_console_manifest.decode_text_bytes: a UTF-8 or UTF-16
    byte-order mark is honoured, anything else must be strict UTF-8. A legacy
    code-page file is refused (exit 2) rather than guessed at; a traceback here
    used to exit 1, which reads as "findings recorded" with nothing persisted.
    """
    encoding = "utf-8"
    if data.startswith(codecs.BOM_UTF8):
        data = data[len(codecs.BOM_UTF8):]
    elif data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        encoding = "utf-16"  # reads the BOM, then drops it
    try:
        return data.decode(encoding)
    except UnicodeDecodeError:
        raise ReviewError(
            source + " is not valid " + ("UTF-16" if encoding == "utf-16" else "UTF-8")
            + " (a legacy code-page file, or UTF-16 without a byte-order mark?)." + hint
        )


def read_findings_file(name):
    try:
        data = Path(name).read_bytes()
    except OSError as exc:
        raise ReviewError("could not read findings file '" + name + "': " + str(exc))
    return decode_text_bytes(data, "the findings file '" + name + "'", DECODE_HINT_FILE)


def findings_text(args):
    """The findings payload: --findings-file, or --findings as JSON, @file or - (stdin)."""
    if getattr(args, "findings_file", None) is not None:
        return read_findings_file(args.findings_file)
    raw = args.findings
    if raw == "-":
        if sys.stdin is None:
            raise ReviewError("--findings - reads standard input, but there is none")
        stream = getattr(sys.stdin, "buffer", None)
        data = stream.read() if stream is not None else sys.stdin.read().encode("utf-8")
        return decode_text_bytes(data, "standard input", DECODE_HINT_PIPE)
    if raw.startswith("@"):
        return read_findings_file(raw[1:])
    return raw


def extract_reviewable(preview):
    """Normalize preview's reviewable_files to [{"path": ..., "status": ...}].

    Workspace mode can report the same path twice (a staged deletion followed
    by an untracked recreation), so the pair, not the path, is the identity.
    An entry without a path is refused rather than dropped: a dropped file
    would leave the coverage gate satisfied for a file nobody reviewed.
    """
    if not isinstance(preview, dict):
        raise ReviewError("ocr delegate preview did not return a JSON object")
    entries = preview.get("reviewable_files", [])
    if not isinstance(entries, list):
        raise ReviewError("ocr delegate preview's reviewable_files is not a list")
    files = []
    for entry in entries:
        if isinstance(entry, dict):
            path = entry.get("path") or entry.get("file")
            if not path:
                raise ReviewError(
                    "ocr delegate preview returned a reviewable_files entry without a path: "
                    + json.dumps(entry)[:200]
                )
            files.append({"path": str(path), "status": str(entry.get("status") or "changed")})
        elif isinstance(entry, str) and entry:
            files.append({"path": entry, "status": "changed"})
    return files


FINDINGS_KEYS = ("comments", "findings", "results")


def is_findings_list(node):
    """True when node is a non-empty list of {path, comment-body} objects."""
    return (
        isinstance(node, list)
        and bool(node)
        and all(isinstance(item, dict) for item in node)
        and all(
            isinstance(item.get("path"), str) and any(key in item for key in CONTENT_KEYS)
            for item in node
        )
    )


def find_findings(node):
    """Locate the findings list in an ocr review/scan JSON document.

    Prefer a list under a known key (comments/findings/results) so a document
    that also echoes rule groups or file lists cannot win by appearing first;
    otherwise return the first list that satisfies the shape contract. A
    known key that is empty or null (a failed ocr run says `"comments": null`)
    means "no findings": the search stops there rather than mistaking some
    other list of path-bearing objects for review comments.
    """
    if isinstance(node, dict):
        for key in FINDINGS_KEYS:
            if is_findings_list(node.get(key)):
                return node[key]
        if any(key in node and node[key] in (None, []) for key in FINDINGS_KEYS):
            return []
        for value in node.values():
            found = find_findings(value)
            if found is not None:
                return found
    elif isinstance(node, list):
        if is_findings_list(node):
            return node
        for item in node:
            found = find_findings(item)
            if found is not None:
                return found
    return None


def line_number(value):
    """An OCR line anchor as an int, or None when there isn't one.

    Accepts the digit strings LLM-authored JSON routinely emits ("10"), because
    silently dropping the anchor would discard the one thing this pass
    guarantees. Booleans are not line numbers, though bool is an int subclass.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str) and value.strip().isdigit():
        number = int(value.strip())
        return number if number > 0 else None
    return None


def normalize_finding(item):
    """Validate one OCR-schema finding.

    The payload is authored by an LLM agent in delegate mode, so malformed
    input is the expected failure mode and must surface as ReviewError (exit
    2), never as a crash. Severity must be exact — it maps 1:1 onto gap
    severities; an unknown category degrades to "other" with a visible notice.
    """
    if not isinstance(item, dict):
        raise ReviewError("each finding must be a JSON object with a path")
    path = item.get("path")
    if not isinstance(path, str) or not path.strip():
        raise ReviewError("a finding is missing its 'path' string")
    content = ""
    for key in CONTENT_KEYS:
        if item.get(key):
            content = str(item[key])
            break
    start_line = None
    for key in LINE_KEYS:
        start_line = line_number(item.get(key))
        if start_line is not None:
            break
    end_line = line_number(item.get("end_line"))
    severity = str(item.get("severity") or "medium").lower()
    category = str(item.get("category") or "other").lower()
    if severity not in SEVERITIES:
        raise ReviewError(
            "finding for '" + path + "' has severity '" + severity
            + "' (expected critical|high|medium|low)"
        )
    if category not in CATEGORIES:
        stderr("note: category '" + category + "' for " + path + " is not known; recorded as 'other'")
        category = "other"
    return {
        "path": path,
        "content": content,
        "start_line": start_line,
        "end_line": end_line,
        "severity": severity,
        "category": category,
    }


def slug_for(path):
    # Cutting at 40 characters can leave a trailing "-", which the id pattern
    # refuses once the "-<n>" suffix is appended ("--1"): strip after the cut.
    slug = re.sub(r"[^a-z0-9]+", "-", path.lower()).strip("-")[:40].rstrip("-")
    return slug or "file"


# Gap statuses that still stand for an unresolved finding. A `fixed` gap that
# OCR reports again is a regression and is recorded anew.
LIVE_GAP_STATUSES = ("open", "accepted", "blocked")
# Gaps recorded before the `fingerprint` field existed carry it at the end of
# their rationale instead.
LEGACY_FINGERPRINT = re.compile(r"\bfp:([0-9a-f]{12})\b")


def finding_fingerprint(finding):
    """Identity of a finding across re-runs: path, start line, content hash."""
    material = "\0".join([
        finding["path"].replace("\\", "/"),
        str(finding["start_line"] or ""),
        finding["content"],
    ])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:12]


def read_gaps(manifest):
    """Best-effort read of gaps[]: (ids taken, fingerprints of live gaps).

    Ids keep new gap ids unique; the fingerprints keep a re-run from recording
    a finding that is already an open, accepted or blocked gap. A gap keeps its
    fingerprint in its own `fingerprint` field, so accepting it the documented
    way (status `accepted` plus a rewritten, accepted-risk rationale) does not
    turn it into a new open gap on the next run. The `fp:<12 hex>` rationale
    suffix that older gaps carry is still honoured while it is there.
    """
    try:
        data = json.loads(manifest.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return set(), set()
    gaps = data.get("gaps") if isinstance(data, dict) else None
    if not isinstance(gaps, list):
        return set(), set()
    ids = set()
    prints = set()
    for gap in gaps:
        if not isinstance(gap, dict):
            continue
        if isinstance(gap.get("id"), str):
            ids.add(gap["id"])
        if gap.get("status") not in LIVE_GAP_STATUSES:
            continue
        fingerprint = gap.get("fingerprint")
        if isinstance(fingerprint, str) and fingerprint.strip():
            prints.add(fingerprint.strip().lower())
        rationale = gap.get("rationale")
        if isinstance(rationale, str):
            prints.update(LEGACY_FINGERPRINT.findall(rationale))
    return ids, prints


def unique_gap_id(base_id, taken):
    """First free id for base_id: base, base-r2, base-r3, ... — no fixed budget.

    A repair loop that re-runs the same pass must never hit a wall of
    "the manifest refused the gap" after N retries.
    """
    if base_id not in taken:
        return base_id
    suffix = 2
    while base_id + "-r" + str(suffix) in taken:
        suffix += 1
    return base_id + "-r" + str(suffix)


def add_gaps(manifest, gaps):
    """Run one all-or-nothing manifest `add --kind gap` for the whole batch.

    The batch goes through a temp file (`--json @file`), never argv: Windows
    caps a command line near 32k characters, which about 60 findings exceed,
    and the crash used to leave nothing persisted. The file sits beside the
    manifest, is removed on every path, and its name ends in .tmp.
    """
    try:
        handle, name = tempfile.mkstemp(
            prefix=".ocr-gaps-", suffix=".json.tmp", dir=str(manifest.parent)
        )
    except OSError as exc:
        raise ReviewError("could not create a temp file beside the manifest: " + str(exc))
    try:
        try:
            with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as out:
                json.dump(gaps, out)
        except OSError as exc:
            raise ReviewError("could not write the gap batch to '" + name + "': " + str(exc))
        # The manifest script's output is read back as UTF-8 below; a cp1252
        # console default must not be able to break it on a non-ASCII path.
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        try:
            return subprocess.run(
                [
                    sys.executable,
                    str(MANIFEST_SCRIPT),
                    "add",
                    "--manifest",
                    str(manifest),
                    "--kind",
                    "gap",
                    "--json",
                    "@" + name,
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
            )
        except OSError as exc:
            raise ReviewError("could not run the manifest script: " + str(exc))
    finally:
        try:
            os.unlink(name)
        except OSError:
            pass


def persist_findings(manifest, findings, engine, mode, evidence_path):
    """Write findings as gaps[] in one all-or-nothing batch.

    Returns (written, duplicates): `duplicates` counts findings skipped because
    the same (path, start_line, content) is already an open, accepted or
    blocked gap, or repeats within this batch, so re-running a pass never
    piles up copies.

    Ids are chosen against the manifest's taken ids up front, and the whole
    batch goes through a single manifest `add`, so a refusal leaves the
    manifest exactly as it was and a retry can never duplicate a half-written
    run.

    Each gap is marked `source: "ocr"` and carries no `entity` or
    `capability`: the validator ignores ocr-sourced gaps when it looks for a
    lifecycle escape, and a gap naming an entity would count as one.
    """
    if not findings:
        return 0, 0
    date = time.strftime("%Y-%m-%d")
    # Evidence paths are relative to the project root the manifest describes;
    # abspath both sides so a `..` in projectRoot cannot defeat relative_to.
    project_root = project_root_of(manifest)
    evidence_abs = Path(os.path.abspath(str(evidence_path)))
    try:
        evidence_rel = str(evidence_abs.relative_to(project_root)).replace("\\", "/")
    except ValueError:
        evidence_rel = str(evidence_abs).replace("\\", "/")
    stamp = time.strftime("%Y%m%d")

    def build(taken, known, attempt):
        gaps = []
        duplicates = 0
        seen = set(known)
        for index, finding in enumerate(findings, 1):
            fingerprint = finding_fingerprint(finding)
            if fingerprint in seen:
                duplicates += 1
                continue
            seen.add(fingerprint)
            where = finding["path"] + ":" + str(finding["start_line"] or "?")
            description = where + " — " + finding["content"][:280]
            description += " (" + finding["category"] + ", " + finding["severity"] + ")"
            base = "ocr-" + stamp + "-" + slug_for(finding["path"]) + "-" + str(index)
            if attempt:
                # Contenders that re-read the same manifest compute the same
                # next free id and collide again in lockstep. The pid makes a
                # retry's candidate unique per process, so one round settles it.
                base += "-p" + str(os.getpid())
            gap_id = unique_gap_id(base, taken)
            taken.add(gap_id)
            gaps.append(
                {
                    "id": gap_id,
                    "severity": finding["severity"],
                    "description": description,
                    "status": "open",
                    "rationale": "OCR " + engine + " pass, " + mode + " mode, " + date,
                    "evidence": [evidence_rel],
                    "source": "ocr",
                    "fingerprint": fingerprint,
                }
            )
        return gaps, duplicates

    # Ids are chosen from an unlocked read, so a concurrent `record` — qa and
    # security may both run this pass over one domain — can take an id between
    # the read and the locked add. That refusal is not the caller's fault and
    # its findings are still valid, so re-read the taken ids (and the live
    # fingerprints: the other agent may have recorded the same finding) and
    # rebuild rather than making the agent diagnose a race.
    last_error = ""
    for attempt in range(4):
        taken, known = read_gaps(manifest)
        gaps, duplicates = build(taken, known, attempt)
        if not gaps:
            return 0, duplicates
        proc = add_gaps(manifest, gaps)
        if proc.returncode == 0:
            return len(gaps), duplicates
        last_error = proc.stderr.strip() or proc.stdout.strip() or "the manifest refused the gap batch"
        if "already exists" not in last_error:
            break
        time.sleep(0.05 * (attempt + 1))
    stderr(last_error)
    raise ReviewError(
        "the manifest refused the batch of " + str(len(gaps))
        + " gap(s); nothing was persisted — fix the payload and re-run"
        + (" (id still taken after 4 attempts; another agent may be recording"
           " concurrently)" if "already exists" in last_error else "")
    )


def rule_arguments(args):
    """--rule for ocr: the given file, else the shipped admin-console rules.

    A rule file that is not there is an error, not a quiet review without the
    admin-console contract: the shipped rules are the point of this bridge.
    """
    if args.rule:
        rule = Path(args.rule)
        missing = "the --rule file '" + str(rule) + "' does not exist"
    else:
        rule = DEFAULT_RULE_PATH
        missing = (
            "the shipped rules '" + str(rule) + "' are missing (an incomplete install?);"
            " restore the file or pass --rule <file>"
        )
    if not rule.is_file():
        raise ReviewError(missing)
    return ["--rule", os.path.abspath(str(rule))]


def reference_arguments(args):
    references = []
    if getattr(args, "from_ref", None):
        references += ["--from", args.from_ref]
    if getattr(args, "to_ref", None):
        references += ["--to", args.to_ref]
    if getattr(args, "commit", None):
        references += ["--commit", args.commit]
    return references


def require_ocr(args):
    executable = ocr_executable()
    if executable:
        return executable
    notice = "ocr is not on PATH — skipping the external review pass; nothing was blocked."
    if getattr(args, "strict", False):
        stderr("ERROR: " + notice)
        raise ReviewError("--strict was given but ocr is unavailable")
    print(notice)
    return None


def write_json_atomic(path, document):
    """Write a JSON file so a reader never sees half of it (temp file + replace)."""
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    os.replace(str(temp), str(path))


def bundle_name(agent):
    """One bundle file per `diff` run: agent id (if given), timestamp, pid.

    A single shared file let a concurrent `diff` overwrite another agent's
    review contract; a per-run name cannot collide, and `record` is handed
    the path.
    """
    label = re.sub(r"[^A-Za-z0-9._-]+", "-", agent or "").strip("-.")[:60]
    parts = ["ocr-bundle"] + ([label] if label else [])
    parts += [time.strftime("%Y%m%d-%H%M%S"), str(os.getpid())]
    return "-".join(parts) + ".json"


def bundle_paths(manifest):
    """Every bundle beside the manifest (the pre-2026-09 shared name included)."""
    return sorted(manifest.parent.glob("ocr-bundle*.json"))


def load_bundle(path):
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ReviewError("could not read the OCR bundle " + str(path) + ": " + str(exc))
    try:
        bundle = json.loads(text)
    except ValueError:
        raise ReviewError(
            "the OCR bundle " + str(path) + " is not valid JSON; delete it and re-run diff"
        )
    if not isinstance(bundle, dict):
        raise ReviewError("the OCR bundle must be a JSON object; re-run diff to rebuild it")
    required = bundle.get("reviewable_files", [])
    if not isinstance(required, list):
        raise ReviewError("the OCR bundle's reviewable_files must be a list; re-run diff to rebuild it")
    for entry in required:
        if not (isinstance(entry, dict) and isinstance(entry.get("path"), str) and entry["path"]):
            raise ReviewError("the OCR bundle has a malformed reviewable_files entry; re-run diff to rebuild it")
    return bundle


def write_bundle(manifest, bundle, agent=None):
    """Write a new per-run bundle beside the manifest and return its path."""
    path = manifest.parent / bundle_name(agent)
    write_json_atomic(path, bundle)
    return path


def close_bundle(path, bundle, **facts):
    """Mark a bundle spent. It stays on disk: recorded gaps cite it as evidence."""
    closed = dict(facts)
    closed["at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    bundle["closed"] = closed
    write_json_atomic(path, bundle)


def supersede_bundles(manifest, agent):
    """A new run by an agent replaces that agent's own unrecorded bundles.

    Only the same named agent's: bundles of other agents (or of runs without
    --agent) are never touched, or two concurrent diffs would void each other.
    """
    if not agent:
        return
    for path in bundle_paths(manifest):
        try:
            bundle = load_bundle(path)
            if bundle.get("agent") == agent and not bundle.get("closed"):
                close_bundle(path, bundle, by="diff", reason="superseded by a later diff by the same agent")
        except (ReviewError, OSError):
            continue


def select_bundle(manifest, explicit):
    """The bundle `record` must satisfy, as (path, document); (None, None) without one.

    Explicit --bundle wins. Otherwise the single live bundle beside the
    manifest: unrecorded and younger than BUNDLE_TTL_SECONDS. Several live
    bundles are ambiguous, and guessing would let one agent's record be
    checked against another agent's review, so that is refused.
    """
    if explicit:
        path = Path(explicit)
        if not path.is_file():
            raise ReviewError("review bundle not found: " + str(path))
        bundle = load_bundle(path)
        if bundle.get("closed"):
            closed = bundle["closed"] if isinstance(bundle["closed"], dict) else {}
            raise ReviewError(
                "the review bundle " + str(path) + " is already closed ("
                + str(closed.get("by", "?")) + " at " + str(closed.get("at", "?"))
                + "); re-run diff for a fresh one, or omit --bundle to record without"
                " a coverage check"
            )
        return path, bundle
    live = []
    for path in bundle_paths(manifest):
        bundle = load_bundle(path)
        if bundle.get("closed"):
            continue
        try:
            age = time.time() - path.stat().st_mtime
        except OSError:
            continue
        if age > BUNDLE_TTL_SECONDS:
            stderr(
                "note: ignoring the expired review bundle " + str(path)
                + " (older than " + str(BUNDLE_TTL_SECONDS // 3600) + " hours)"
            )
            continue
        live.append((path, bundle))
    if len(live) > 1:
        listing = "\n".join(
            "  " + str(path) + " (agent: " + str(bundle.get("agent") or "none")
            + ", created " + str(bundle.get("created", "?")) + ")"
            for path, bundle in live
        )
        raise ReviewError(
            "several review bundles are waiting to be recorded; pass --bundle <path> for"
            " yours:\n" + listing
        )
    return live[0] if live else (None, None)


def quote_arg(text):
    text = str(text)
    return '"' + text + '"' if re.search(r"\s", text) else text


BUNDLE_INSTRUCTIONS = (
    "Review every file yourself, or skip it with a concrete reason; never stop "
    "at the first high-severity finding. Workspace mode can list the same path "
    "twice — one review covers both entries. Report only paths from "
    "reviewable_files; findings for any other path are refused. Return "
    "findings through `record --bundle <this file>` as JSON: {\"findings\": "
    "[{\"path\", \"content\", "
    "\"start_line\", \"end_line\", \"category\": bug|security|performance|"
    "maintainability|test|style|documentation|other, \"severity\": critical|"
    "high|medium|low}], \"skipped\": [{\"path\", \"reason\"}]}. Every bundled "
    "file must appear in one list or the other; silent omissions are refused."
)


def cmd_diff(args):
    manifest = Path(args.manifest)
    if not manifest.exists():
        raise ReviewError("manifest not found: " + str(manifest))
    if require_ocr(args) is None:
        return EXIT_OK
    references = reference_arguments(args)
    rules = rule_arguments(args)
    repo = repo_for(args, manifest)
    timeout = ocr_timeout(args)

    if args.engine == "delegate":
        preview = run_ocr(
            ["delegate", "preview", "--format", "json"] + references + rules, repo, timeout=timeout
        )
        if preview.returncode != 0:
            stderr((preview.stderr or "").strip() or "ocr delegate preview failed")
            return EXIT_USAGE
        data = parse_json(preview.stdout, "ocr delegate preview")
        files = extract_reviewable(data)
        if not files:
            # Nothing to review now means this agent's earlier, unrecorded
            # bundle describes a workspace that no longer exists; left alone it
            # would keep gating `record` for a review nobody needs.
            supersede_bundles(manifest, args.agent)
            print("OCR: nothing to review in " + str(data.get("mode", "workspace")) + " mode.")
            return EXIT_OK
        rule_out = run_ocr(
            ["delegate", "rule", "--format", "json"] + rules,
            repo,
            trailing=[f["path"] for f in files],
            timeout=timeout,
        )
        if rule_out.returncode != 0:
            stderr((rule_out.stderr or "").strip() or "ocr delegate rule failed")
            return EXIT_USAGE
        rule_data = parse_json(rule_out.stdout, "ocr delegate rule")
        if not isinstance(rule_data, dict):
            raise ReviewError("ocr delegate rule did not return a JSON object")
        groups = rule_data.get("groups", [])
        refs = {}
        for key, value in (("from", args.from_ref), ("to", args.to_ref), ("commit", args.commit)):
            if value:
                refs[key] = value
        bundle = {
            "schema_version": "1",
            "engine": "delegate",
            "mode": data.get("mode", "workspace"),
            "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "agent": args.agent or None,
            "refs": refs,
            "reviewable_files": files,
            "rule_groups": groups,
            "instructions": BUNDLE_INSTRUCTIONS,
        }
        supersede_bundles(manifest, args.agent)
        path = write_bundle(manifest, bundle, args.agent)
        print("OCR bundle written: " + str(path))
        print(str(len(files)) + " file(s) to review. Next:")
        # --findings-file, not `--findings @file`: a bare leading @ is splatting
        # in PowerShell, which refuses to parse the pasted line.
        print(
            "  python " + quote_arg(Path(__file__).resolve()) + " record --manifest "
            + quote_arg(manifest) + " --bundle " + quote_arg(path)
            + " --findings-file <findings.json>"
        )
        return EXIT_OK

    review = run_ocr(
        ["review", "--audience", "agent", "--format", "json"] + references + rules,
        repo,
        timeout=timeout,
    )
    return persist_endpoint_result(manifest, review, "diff")


def cmd_scan(args):
    manifest = Path(args.manifest)
    if not manifest.exists():
        raise ReviewError("manifest not found: " + str(manifest))
    if require_ocr(args) is None:
        return EXIT_OK
    command = ["scan", "--audience", "agent", "--format", "json"]
    if args.path:
        command += ["--path", args.path]
    command += rule_arguments(args)
    repo = repo_for(args, manifest)
    scan = run_ocr(command, repo, timeout=ocr_timeout(args))
    return persist_endpoint_result(manifest, scan, "scan")


# How ocr says a review or scan ended. Taken from the ocr source (the run
# manifest in internal/session/manifest.go, the reply in
# cmd/opencodereview/output.go, the exit code in review_cmd.go; 1.8.5 to
# 1.12.9) and checked against real replies, kept in tests/fixtures/ocr:
#
# * A review carries a run manifest whose `terminal_state` is complete,
#   partial, failed or skipped, and the top-level `status` repeats it. ocr
#   exits non-zero only when the run itself failed or every selected file
#   failed, so a partial review arrives with exit 0. skipped means nothing was
#   selected: a clean workspace, or only excluded files changed.
# * scan has no run manifest (nor had a review before ocr 1.8.5): `status` is
#   success, completed_with_warnings or completed_with_errors, and a file that
#   could not be reviewed is named in `warnings[]`.
#
# A state outside these lists is not guessed at; see endpoint_verdict.
COMPLETE_STATES = ("complete", "completed", "success", "completed_with_warnings")
PARTIAL_STATES = ("partial", "completed_with_errors")
FAILED_STATES = ("failed", "failure", "error", "errored")
SKIPPED_STATES = ("skipped",)
# When fields disagree, the worst one decides.
STATE_RANK = {"skipped": 0, "complete": 1, "partial": 2, "unknown": 3, "failed": 4}

# Warning types that mean a file was not (fully) reviewed. They are read only
# from a reply without a run manifest: with one, coverage.failed names those
# files already (and ocr drops the per-file error warnings from the reply).
COVERAGE_WARNINGS = (
    "subtask_error", "scan_subtask_error", "token_threshold_exceeded", "token_budget_reached",
)

# A failure ocr classifies this way, or a message with one of these phrases
# ("no valid LLM endpoint configured", "check your LLM configuration and API
# key"), is a provider set-up problem; only then is the set-up hint printed.
PROVIDER_CLASSES = ("provider", "configuration")
PROVIDER_PHRASES = ("llm configuration", "llm endpoint", "llm completion error", "api key", "provider")
PROVIDER_HINT = (
    "hint: the LLM provider is missing, unreachable or refusing requests. The endpoint"
    " engine needs `ocr config provider` and `ocr config model` (INTEGRATIONS.md, Setup);"
    " check the gateway URL and key, or use the default delegate engine, which needs no"
    " provider."
)
FAILED_LISTING_LIMIT = 20


def result_nodes(document):
    """(node, is_root) for each place ocr reports how a run went.

    The document itself, its `manifest` / `run_manifest` / `coverage` objects,
    and a `coverage` object nested inside those. The exact nesting differs
    between ocr versions, so every plausible home is read rather than one.
    """
    if not isinstance(document, dict):
        return []
    nodes = [(document, True)]
    for key in ("manifest", "run_manifest", "coverage"):
        node = document.get(key)
        if isinstance(node, dict):
            nodes.append((node, False))
            inner = node.get("coverage")
            if isinstance(inner, dict):
                nodes.append((inner, False))
    return nodes


def has_run_manifest(document):
    return isinstance(document, dict) and any(
        isinstance(document.get(key), dict) for key in ("manifest", "run_manifest", "coverage")
    )


def run_failure(document):
    """The manifest's run_failure ({classification, reason}), if ocr gave one."""
    for node, _root in result_nodes(document):
        failure = node.get("run_failure")
        if isinstance(failure, dict):
            return failure
    return None


def tool_messages(document, stderr_text):
    """What ocr itself said: the reply's message or error, then its `Error:` lines.

    stderr is read only for the lines ocr's CLI prints as `Error: ...`; the JSON
    usage record a failed review also writes there is noise here. With no
    message at all, the last few non-JSON stderr lines stand in.
    """
    messages = []
    for node, _root in result_nodes(document):
        for key in ("error", "error_message", "message", "last_error"):
            value = node.get(key)
            if isinstance(value, str) and value.strip():
                messages.append(value.strip())
                break
        else:
            errors = node.get("errors")
            if isinstance(errors, list) and errors:
                messages.append("; ".join(str(item)[:200] for item in errors[:5]))
        if messages:
            break
    failure = run_failure(document)
    if failure:
        messages.append(
            "run failure (" + str(failure.get("classification") or "unknown") + ")"
            + (": " + str(failure["reason"]) if failure.get("reason") else "")
        )
    lines = [line.strip() for line in (stderr_text or "").splitlines() if line.strip()]
    said = [line[len("Error:"):].strip() for line in lines if line.startswith("Error:")]
    if not said and not messages:
        said = [line for line in lines if not line.startswith(("{", "}", "[", "]", '"'))][-5:]
    for line in said:
        if line and line not in messages:
            messages.append(line)
    return messages


def state_category(state):
    state = state.strip().lower()
    for category, states in (
        ("complete", COMPLETE_STATES), ("partial", PARTIAL_STATES),
        ("failed", FAILED_STATES), ("skipped", SKIPPED_STATES),
    ):
        if state in states:
            return category
    return "unknown"


def reported_states(document):
    """[(field, state)] for every place the reply says how the run ended."""
    states = []
    if not isinstance(document, dict):
        return states
    status = document.get("status")
    if isinstance(status, str) and status.strip():
        states.append(("status", status.strip()))
    for node, _root in result_nodes(document):
        state = node.get("terminal_state")
        if isinstance(state, str) and state.strip():
            states.append(("terminal_state", state.strip()))
    return states


def describe_failure(entry):
    """One file ocr could not review, as `path (classification: reason)`."""
    if isinstance(entry, dict):
        path = entry.get("path") or entry.get("file") or entry.get("name") or json.dumps(entry)[:120]
        kind = entry.get("classification") or entry.get("type")
        reason = entry.get("reason") or entry.get("error") or entry.get("message")
        detail = ": ".join(str(part) for part in (kind, reason) if part)
        return str(path) + (" (" + detail[:200] + ")" if detail else "")
    return str(entry)


def failed_files(document):
    """Files ocr says it could not review: (descriptions, unnamed count, classes).

    Read from the run manifest's coverage (`failed`; `failed_files` is accepted
    too) when the reply has one, else from the warnings that mean a file went
    unreviewed (scan, and reviews before ocr 1.8.5). `classes` holds the
    failure classifications (provider, timeout, budget, ...) ocr attached.
    """
    named = []
    unnamed = 0
    classes = set()
    for node, is_root in result_nodes(document):
        for key in ("failed", "failed_files"):
            if is_root and key == "failed":
                continue  # a top-level `failed` is not the coverage list
            value = node.get(key)
            if isinstance(value, list):
                for entry in value:
                    text = describe_failure(entry)
                    if text not in named:
                        named.append(text)
                    if isinstance(entry, dict) and entry.get("classification"):
                        classes.add(str(entry["classification"]).strip().lower())
            elif isinstance(value, dict):
                for name in value:
                    if str(name) not in named:
                        named.append(str(name))
            elif isinstance(value, int) and not isinstance(value, bool) and value > 0:
                unnamed = max(unnamed, value)
    failure = run_failure(document)
    if failure and failure.get("classification"):
        classes.add(str(failure["classification"]).strip().lower())
    if isinstance(document, dict) and not has_run_manifest(document):
        warnings = document.get("warnings")
        for warning in warnings if isinstance(warnings, list) else []:
            if isinstance(warning, dict) and warning.get("type") in COVERAGE_WARNINGS:
                text = describe_failure(warning)
                if text not in named:
                    named.append(text)
                if warning.get("type") in ("subtask_error", "scan_subtask_error"):
                    message = str(warning.get("message") or "").lower()
                    if any(phrase in message for phrase in PROVIDER_PHRASES):
                        classes.add("provider")
    return named, unnamed, classes


def warning_notes(document):
    """ocr's other warnings, passed on as notes (the unreviewed-file ones are listed instead)."""
    warnings = document.get("warnings") if isinstance(document, dict) else None
    notes = []
    for warning in warnings if isinstance(warnings, list) else []:
        if not isinstance(warning, dict):
            continue
        kind = str(warning.get("type") or "warning")
        if kind in COVERAGE_WARNINGS and not has_run_manifest(document):
            continue
        where = str(warning["file"]) + ": " if warning.get("file") else ""
        notes.append("ocr warning [" + kind + "] " + where + str(warning.get("message") or "")[:200])
    return notes


def selected_count(document):
    """How many files the run manifest says ocr selected, or None."""
    for node, is_root in result_nodes(document):
        selected = None if is_root else node.get("selected")
        if isinstance(selected, list):
            return len(selected)
    return None


class Verdict(object):
    """What the bridge does with one ocr reply (see endpoint_verdict)."""

    def __init__(self):
        self.outcome = "complete"
        self.state = ""  # the deciding field, e.g. "terminal_state 'partial'"
        self.reasons = []  # why the run failed; empty unless outcome == "failed"
        self.failed = []  # "path (classification: reason)" per unreviewed file
        self.unnamed = 0  # unreviewed files ocr counted without naming them
        self.classes = set()  # ocr's failure classifications
        self.selected = None  # files ocr selected, when the manifest says
        self.notes = []  # other ocr warnings


def endpoint_verdict(document, returncode):
    """Classify an ocr review/scan reply.

    outcome:
      complete     every selected file was reviewed (a reply that states no
                   outcome at all counts too): record the findings.
      partial      some files could not be reviewed: record the findings that
                   exist, list the others, exit 2.
      skipped      ocr selected nothing to review: exit 0.
      unconfirmed  a state this bridge does not know, with exit 0: ocr's exit
                   code says the coverage is usable, so the findings are
                   recorded, but completeness cannot be confirmed: exit 2.
      failed       a non-zero exit, a failed state, or no JSON at all: record
                   nothing, exit 2.

    The previous check accepted only "completed", a word ocr never uses, and
    so failed every real review; the states are compared as sets now.
    """
    verdict = Verdict()
    ranked = []
    for field, state in reported_states(document):
        category = state_category(state)
        label = field + " '" + state + "'"
        ranked.append((STATE_RANK[category], category, label))
        if category == "failed":
            verdict.reasons.append(label)
    if returncode != 0:
        verdict.reasons.insert(0, "exit code " + str(returncode))
    elif document is None:
        verdict.reasons.append("no parseable JSON on stdout")
    verdict.failed, verdict.unnamed, verdict.classes = failed_files(document)
    verdict.notes = warning_notes(document)
    verdict.selected = selected_count(document)
    _rank, worst, verdict.state = max(ranked) if ranked else (1, "complete", "")
    if verdict.reasons:
        verdict.outcome = "failed"
    elif worst == "unknown":
        verdict.outcome = "unconfirmed"
    elif worst == "partial" or verdict.failed or verdict.unnamed:
        verdict.outcome = "partial"
    else:
        verdict.outcome = worst
    return verdict


def provider_problem(verdict, messages):
    """Whether a failure points at the provider set-up (and so earns the hint)."""
    if verdict.classes.intersection(PROVIDER_CLASSES):
        return True
    text = " ".join(messages).lower()
    return any(phrase in text for phrase in PROVIDER_PHRASES)


def report_failed_run(mode, verdict, messages):
    head = messages[0] if messages else "ocr " + mode + " produced no message"
    stderr("ERROR: ocr " + mode + " did not complete (" + ", ".join(verdict.reasons) + "): " + head)
    for line in messages[1:]:
        stderr("  ocr: " + line)
    if verdict.failed:
        stderr("Files ocr could not review:")
        for entry in verdict.failed[:FAILED_LISTING_LIMIT]:
            stderr("  " + entry)
        if len(verdict.failed) > FAILED_LISTING_LIMIT:
            stderr("  ... and " + str(len(verdict.failed) - FAILED_LISTING_LIMIT) + " more")
    stderr("Nothing was recorded.")
    if provider_problem(verdict, messages):
        stderr(PROVIDER_HINT)


def report_partial(verdict, messages):
    count = len(verdict.failed) if verdict.failed else verdict.unnamed
    said = " (" + messages[0] + ")" if messages else ""
    if count:
        scope = " of " + str(verdict.selected) + " selected" if verdict.selected else ""
        stderr(
            "ERROR: ocr could not review " + str(count) + " file(s)" + scope
            + "; coverage is incomplete" + said + "."
        )
    else:
        stderr("ERROR: ocr reported " + verdict.state + "; coverage is incomplete" + said + ".")
    for entry in verdict.failed:
        stderr("  " + entry)
    stderr(
        "Findings for the files it did review were recorded above; re-run once the failures"
        " are resolved (findings already recorded are not written twice)."
    )
    if "provider" in verdict.classes:
        stderr(
            "hint: the provider failed for these files while it served the others; a rate"
            " limit or an upstream error is the usual cause, so a later re-run often succeeds."
        )


def report_unconfirmed(verdict):
    stderr(
        "ERROR: ocr reported " + verdict.state + ", a state this bridge does not know (it acts"
        " on complete, partial, failed and skipped, and on scan's success,"
        " completed_with_warnings and completed_with_errors). The findings above were"
        " recorded, but coverage is not confirmed; compare `ocr --version` with the"
        " version INTEGRATIONS.md was checked against."
    )


def duplicate_note(duplicates):
    """Suffix telling a re-run how many findings were already gaps, or nothing."""
    return " (" + str(duplicates) + " already recorded)" if duplicates else ""


def persist_endpoint_result(manifest, proc, mode):
    """Parse an ocr review/scan reply, keep it as evidence, persist findings.

    What happens depends on how ocr says the run ended (endpoint_verdict). A
    failed run exits 2 with ocr's own message instead of "0 finding(s)
    recorded", and records nothing. A partial one records the findings that
    exist (real findings are not thrown away), lists every file ocr could not
    review with its reason, and exits 2 so nobody reads the gap list as
    complete coverage. A skipped one had nothing to review.
    """
    document = None
    if proc.stdout.strip():
        try:
            document = json.loads(proc.stdout)
        except ValueError:
            document = None
    verdict = endpoint_verdict(document, proc.returncode)
    messages = tool_messages(document, proc.stderr)
    if verdict.outcome == "failed":
        report_failed_run(mode, verdict, messages)
        return EXIT_USAGE
    found = find_findings(document) or []
    if verdict.outcome == "skipped" and not found:
        print("OCR " + mode + ": nothing to review" + ("; ocr: " + messages[0] if messages else "."))
        return EXIT_OK
    raw = manifest.parent / (
        "ocr-" + mode + "-" + time.strftime("%Y%m%d-%H%M%S") + "-" + str(os.getpid()) + ".json"
    )
    write_json_atomic(raw, document)
    findings = [normalize_finding(item) for item in found]
    written, duplicates = persist_findings(manifest, findings, "endpoint", mode, raw)
    print(
        "OCR " + mode + ": " + str(written) + " finding(s) recorded as gaps"
        + duplicate_note(duplicates) + "; raw output kept at " + str(raw)
    )
    for note in verdict.notes:
        stderr("note: " + note)
    if verdict.outcome == "partial":
        report_partial(verdict, messages)
        return EXIT_USAGE
    if verdict.outcome == "unconfirmed":
        report_unconfirmed(verdict)
        return EXIT_USAGE
    if written or duplicates:
        return EXIT_FINDINGS
    return EXIT_OK


def cmd_record(args):
    manifest = Path(args.manifest)
    if not manifest.exists():
        raise ReviewError("manifest not found: " + str(manifest))
    text = findings_text(args)
    try:
        payload = json.loads(text)
    except (ValueError, RecursionError) as exc:
        inline = args.findings is not None and args.findings != "-" and not args.findings.startswith("@")
        raise ReviewError(
            "the findings are not valid JSON: " + str(exc)[:200] + "."
            + (POWERSHELL_JSON_HINT if inline else "")
        )
    if isinstance(payload, dict):
        findings_raw = payload.get("findings", [])
        skipped_raw = payload.get("skipped", [])
        if not isinstance(findings_raw, list) or not isinstance(skipped_raw, list):
            raise ReviewError("findings and skipped must be JSON lists")
    elif isinstance(payload, list):
        findings_raw = payload
        skipped_raw = []
    else:
        raise ReviewError("findings must be a JSON list or an object with findings/skipped")
    findings = [normalize_finding(item) for item in findings_raw]
    skipped = []
    for item in skipped_raw:
        if not isinstance(item, dict) or not item.get("path") or not item.get("reason"):
            raise ReviewError("every skipped entry needs a path and a reason")
        skipped.append({"path": str(item["path"]), "reason": str(item["reason"])})
    covered = {f["path"] for f in findings} | {s["path"] for s in skipped}

    bundle_path, bundle = select_bundle(manifest, args.bundle)
    bundle_files = None
    if bundle is not None:
        required = bundle.get("reviewable_files", [])
        bundle_files = {f["path"] for f in required}
        unknown = sorted(covered - bundle_files)
        if unknown:
            stderr("ERROR: these paths are not in the review bundle:")
            for path in unknown:
                stderr("  " + path)
            return EXIT_USAGE
        missing = [f for f in required if f["path"] not in covered]
        if missing:
            stderr("ERROR: these bundled files were neither reviewed nor skipped:")
            for f in missing:
                stderr("  " + f["path"] + " (" + str(f.get("status", "changed")) + ")")
            return EXIT_USAGE
        evidence_path = bundle_path
    else:
        # No `diff` bundle: there is no reviewable-file set to check the
        # payload against, so it is taken as given and coverage is unknown.
        stderr(
            "note: no review bundle found; paths are not checked against a diff."
            " Run `diff` first for guaranteed file coverage."
        )
        evidence_path = manifest.parent / (
            "ocr-record-" + time.strftime("%Y%m%d-%H%M%S") + "-" + str(os.getpid()) + ".json"
        )
        write_json_atomic(evidence_path, payload)

    written, duplicates = persist_findings(
        manifest, findings, "delegate", "delegate review", evidence_path
    )
    if bundle is not None:
        # Spent: the next `record` must not be gated by, or checked against,
        # this review. The file stays as evidence and now also holds the skip
        # reasons, which nothing else persists.
        try:
            close_bundle(
                bundle_path, bundle, by="record", findings=written,
                duplicates=duplicates, skipped=skipped,
            )
        except OSError as exc:
            stderr("note: could not mark " + str(bundle_path) + " as recorded: " + str(exc))
    reviewed = len({f["path"] for f in findings})
    skipped_files = len({s["path"] for s in skipped})
    line = "recorded " + str(written) + " finding(s) as gaps" + duplicate_note(duplicates)
    if bundle_files is not None:
        # The same path can appear twice in workspace mode (staged deletion +
        # untracked recreation); coverage counts paths, not bundle entries.
        total = len(bundle_files)
        covered_count = len(covered & bundle_files)
        rate = "100%" if total == 0 or covered_count >= total else (
            str(int(100 * covered_count / total)) + "%"
        )
        line += (
            "; total_files=" + str(total) + " reviewed_files=" + str(reviewed)
            + " skipped_files=" + str(skipped_files) + " coverage_rate=" + rate
            + " bundle=" + str(bundle_path)
        )
    else:
        line += (
            "; reviewed_files=" + str(reviewed) + " skipped_files=" + str(skipped_files)
            + " coverage: unknown (no review bundle, so paths were not checked against a diff)"
        )
    print(line)
    if written or duplicates:
        return EXIT_FINDINGS
    return EXIT_OK


def build_parser():
    parser = argparse.ArgumentParser(
        prog="code_review.py",
        description="Optional Open Code Review (OCR) bridge for adminwright.",
        epilog=(
            "Exit codes: 0 clean or nothing reviewable, 1 findings recorded, "
            "2 usage/IO/validation failure (including a failed review). Exit 3 "
            "is intentionally unused (claim conflicts do not apply). "
            "ADMINWRIGHT_OCR_BIN overrides the ocr executable path; "
            "ADMINWRIGHT_OCR_TIMEOUT sets the per-call timeout in seconds."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    project_root_help = (
        "the git repository under review (default: the manifest's project root,"
        " i.e. its `projectRoot`, else the parent of the manifest's directory)"
    )
    timeout_help = (
        "seconds one ocr call may run before it is stopped (default "
        + str(DEFAULT_TIMEOUT_SECONDS) + " or ADMINWRIGHT_OCR_TIMEOUT; at most "
        + str(MAX_TIMEOUT_SECONDS) + ")"
    )

    diff_parser = subparsers.add_parser(
        "diff", help="Review current changes (Build/Repair mode)"
    )
    diff_parser.add_argument("--manifest", required=True)
    diff_parser.add_argument("--engine", choices=["delegate", "ocr"], default="delegate")
    diff_parser.add_argument("--from", dest="from_ref")
    diff_parser.add_argument("--to", dest="to_ref")
    diff_parser.add_argument("--commit")
    diff_parser.add_argument("--rule")
    diff_parser.add_argument("--strict", action="store_true")
    diff_parser.add_argument("--project-root", help=project_root_help)
    diff_parser.add_argument(
        "--agent",
        help="your agent id: names the bundle, and replaces your own earlier"
        " unrecorded bundle (never another agent's)",
    )
    diff_parser.add_argument("--timeout", type=float, help=timeout_help)
    diff_parser.set_defaults(func=cmd_diff)

    scan_parser = subparsers.add_parser(
        "scan", help="Full-file audit review (Audit mode; endpoint engine)"
    )
    scan_parser.add_argument("--manifest", required=True)
    scan_parser.add_argument("--path", help="repo-relative directory or file list")
    scan_parser.add_argument("--rule")
    scan_parser.add_argument("--strict", action="store_true")
    scan_parser.add_argument("--project-root", help=project_root_help)
    scan_parser.add_argument("--timeout", type=float, help=timeout_help)
    scan_parser.set_defaults(func=cmd_scan)

    record_parser = subparsers.add_parser(
        "record", help="Persist reviewed findings as manifest gaps[]"
    )
    record_parser.add_argument("--manifest", required=True)
    payload = record_parser.add_mutually_exclusive_group(required=True)
    payload.add_argument(
        "--findings",
        help="JSON, '@file' (quote it in PowerShell, where a bare @ is splatting), or - for"
        " stdin (from Windows PowerShell 5.1 only with $OutputEncoding set to UTF-8)",
    )
    payload.add_argument(
        "--findings-file", metavar="PATH",
        help="read the findings JSON from a file (PowerShell-safe): UTF-8, with or without"
        " a byte-order mark, or UTF-16 with one, as Windows PowerShell's `>` writes it",
    )
    record_parser.add_argument(
        "--bundle",
        help="the review bundle `diff` printed (default: the one live unrecorded"
        " bundle beside the manifest; several are refused)",
    )
    record_parser.set_defaults(func=cmd_record)
    return parser


def main():
    try:
        # Review text is arbitrary Unicode; a cp1252 console must not crash on it.
        # stdin is not reconfigured: `record --findings -` reads its bytes and
        # decodes them itself (findings_text), UTF-16 and byte-order marks included.
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    parser = build_parser()
    args = parser.parse_args()
    try:
        return args.func(args)
    except ReviewError as exc:
        stderr("ERROR: " + str(exc))
        return EXIT_USAGE
    except OSError as exc:
        # Writing a bundle or evidence file beside the manifest failed (read-only
        # directory, full disk, permissions): a usage/IO failure, not a traceback.
        stderr("ERROR: " + str(exc))
        return EXIT_USAGE
    except KeyboardInterrupt:
        stderr("Interrupted")
        return EXIT_USAGE


if __name__ == "__main__":
    raise SystemExit(main())
