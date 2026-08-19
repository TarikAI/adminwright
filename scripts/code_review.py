#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TarikAI
"""Optional Open Code Review (OCR) bridge for adminwright.

Standard library only. Runs on Windows and POSIX. Never required: when the
`ocr` CLI is absent this script exits 0 with a notice (exit 2 under --strict),
so the skill keeps its offline, dependency-free contract.

Commands: diff, scan, record.

diff    Build/Repair mode. --engine delegate (default, no OCR-side LLM): runs
        `ocr delegate preview/rule --format json`, writes the review bundle to
        .admin-console/ocr-bundle.json, and hands the review itself to the
        calling agent, which returns findings through `record`. --engine ocr:
        runs `ocr review --audience agent --format json` and persists findings
        directly; requires prior `ocr config provider` and `ocr config model`.
scan    Audit mode: full-file review of code no diff covers, via `ocr scan
        --audience agent --format json`. Endpoint engine only; delegate mode
        has no full-file scan.
record  Persist delegate-mode findings as manifest gaps[] through
        admin_console_manifest.py `add --kind gap` — one all-or-nothing
        batch, so a refusal leaves the manifest unmodified. When a bundle
        exists, every bundled (path, status) must end reviewed or explicitly
        skipped with a reason, and findings/skips for paths outside the
        bundle are refused; silent omissions and invented paths alike.

Findings use OCR's comment schema: path, content, start_line, end_line,
category (bug|security|performance|maintainability|test|style|documentation|
other), severity (critical|high|medium|low). Severity must be exact — it maps
1:1 onto gap severities; an unknown category degrades to "other" with a notice
rather than refusing a real finding over taxonomy drift. File coverage and
line anchoring are deterministic; the findings themselves are judgments. They
are evidence for the gap report, never a substitute for validate, coverage,
policy tests, or the adversarial pass. A clean run is a floor, not a proof.

Exit codes: 0 clean or nothing reviewable, 1 findings recorded, 2 usage/IO/
validation failure (including a refused add, which leaves the manifest
unmodified — gaps are written as one atomic batch). Exit 3 is intentionally
unused: capability claim conflicts do not apply to this script.

ADMINWRIGHT_OCR_BIN overrides the ocr executable path (tests and exotic
installs); otherwise the script uses `ocr` from PATH.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
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


def ocr_executable():
    """The ocr command to run, or None when it is not installed."""
    override = os.environ.get("ADMINWRIGHT_OCR_BIN")
    if override:
        return override if Path(override).exists() else None
    return shutil.which("ocr")


def run_ocr(arguments, repo=None, trailing=None):
    """Build the ocr command: flags first, the positional file list last.

    Options placed after a greedy positional list are swallowed as positionals
    by some CLI parsers, so --repo must never trail the file list.
    """
    executable = ocr_executable()
    if not executable:
        raise ReviewError("the ocr CLI is not on PATH")
    command = [executable] + [str(a) for a in arguments]
    if repo:
        command += ["--repo", str(repo)]
    if trailing:
        command += [str(a) for a in trailing]
    try:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        raise ReviewError(
            "could not execute '" + executable + "' ("
            "on Windows point ADMINWRIGHT_OCR_BIN at ocr.cmd or ocr.exe, not the"
            " extensionless npm shell shim): " + str(exc)
        )


def parse_json(text, what):
    try:
        return json.loads(text)
    except ValueError:
        raise ReviewError("could not parse " + what + " output as JSON")


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
    otherwise return the first list that satisfies the shape contract.
    """
    if isinstance(node, dict):
        for key in FINDINGS_KEYS:
            if is_findings_list(node.get(key)):
                return node[key]
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
    slug = re.sub(r"[^a-z0-9]+", "-", path.lower()).strip("-")
    return slug[:40] or "file"


def existing_gap_ids(manifest):
    """Best-effort read of the ids already in gaps[], so new ids stay unique."""
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    gaps = data.get("gaps") if isinstance(data, dict) else None
    if not isinstance(gaps, list):
        return set()
    return {
        gap.get("id") for gap in gaps
        if isinstance(gap, dict) and isinstance(gap.get("id"), str)
    }


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


def persist_findings(manifest, findings, engine, mode, evidence_path):
    """Write findings as gaps[] in one all-or-nothing batch; returns the count.

    Ids are chosen against the manifest's taken ids up front, and the whole
    batch goes through a single manifest `add`, so a refusal leaves the
    manifest exactly as it was and a retry can never duplicate a half-written
    run.
    """
    if not findings:
        return 0
    date = time.strftime("%Y-%m-%d")
    project_root = manifest.parent.parent
    try:
        evidence_rel = str(evidence_path.relative_to(project_root)).replace("\\", "/")
    except ValueError:
        evidence_rel = str(evidence_path).replace("\\", "/")
    stamp = time.strftime("%Y%m%d")

    def build(taken, attempt):
        gaps = []
        for index, finding in enumerate(findings, 1):
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
                }
            )
        return gaps

    # Ids are chosen from an unlocked read, so a concurrent `record` — qa and
    # security may both run this pass over one domain — can take an id between
    # the read and the locked add. That refusal is not the caller's fault and
    # its findings are still valid, so re-read the taken ids and rebuild rather
    # than making the agent diagnose a race.
    last_error = ""
    for attempt in range(4):
        gaps = build(existing_gap_ids(manifest), attempt)
        proc = subprocess.run(
            [
                sys.executable,
                str(MANIFEST_SCRIPT),
                "add",
                "--manifest",
                str(manifest),
                "--kind",
                "gap",
                "--json",
                json.dumps(gaps),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if proc.returncode == 0:
            return len(gaps)
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
    if args.rule:
        rule = Path(args.rule)
    else:
        rule = DEFAULT_RULE_PATH
    if rule.exists():
        return ["--rule", str(rule)]
    return []


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


def write_bundle(manifest, bundle):
    path = manifest.parent / "ocr-bundle.json"
    path.write_text(json.dumps(bundle, indent=2) + "\n", encoding="utf-8")
    return path


BUNDLE_INSTRUCTIONS = (
    "Review every file yourself, or skip it with a concrete reason; never stop "
    "at the first high-severity finding. Workspace mode can list the same path "
    "twice — one review covers both entries. Report only paths from "
    "reviewable_files; findings for any other path are refused. Return "
    "findings through `record` as JSON: {\"findings\": [{\"path\", \"content\", "
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
    repo = Path(args.project_root) if args.project_root else None

    if args.engine == "delegate":
        preview = run_ocr(["delegate", "preview", "--format", "json"] + references + rules, repo)
        if preview.returncode != 0:
            stderr((preview.stderr or "").strip() or "ocr delegate preview failed")
            return EXIT_USAGE
        data = parse_json(preview.stdout, "ocr delegate preview")
        files = extract_reviewable(data)
        if not files:
            print("OCR: nothing to review in " + str(data.get("mode", "workspace")) + " mode.")
            return EXIT_OK
        rule_out = run_ocr(
            ["delegate", "rule", "--format", "json"] + rules,
            repo,
            trailing=[f["path"] for f in files],
        )
        if rule_out.returncode != 0:
            stderr((rule_out.stderr or "").strip() or "ocr delegate rule failed")
            return EXIT_USAGE
        rule_data = parse_json(rule_out.stdout, "ocr delegate rule")
        if not isinstance(rule_data, dict):
            raise ReviewError("ocr delegate rule did not return a JSON object")
        groups = rule_data.get("groups", [])
        bundle = {
            "schema_version": "1",
            "engine": "delegate",
            "mode": data.get("mode", "workspace"),
            "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "reviewable_files": files,
            "rule_groups": groups,
            "instructions": BUNDLE_INSTRUCTIONS,
        }
        path = write_bundle(manifest, bundle)
        print("OCR bundle written: " + str(path))
        print(str(len(files)) + " file(s) to review. Next:")
        print(
            "  python " + str(Path(__file__).resolve()) + " record --manifest " + str(manifest)
            + " --findings '<json>'"
        )
        return EXIT_OK

    review = run_ocr(
        ["review", "--audience", "agent", "--format", "json"] + references + rules, repo
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
    repo = Path(args.project_root) if args.project_root else None
    scan = run_ocr(command, repo)
    return persist_endpoint_result(manifest, scan, "scan")


def persist_endpoint_result(manifest, proc, mode):
    """Parse an ocr review/scan reply, keep it as evidence, persist findings."""
    document = None
    if proc.stdout.strip():
        try:
            document = json.loads(proc.stdout)
        except ValueError:
            document = None
    if document is None:
        stderr((proc.stderr or "").strip() or "ocr " + mode + " produced no parseable JSON")
        stderr("hint: endpoint engine needs `ocr config provider` and `ocr config model` first")
        return EXIT_USAGE
    raw = manifest.parent / ("ocr-" + mode + "-" + time.strftime("%Y%m%d-%H%M%S") + ".json")
    raw.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    found = find_findings(document)
    findings = [normalize_finding(item) for item in (found or [])]
    persisted = persist_findings(manifest, findings, "endpoint", mode, raw)
    print(
        "OCR " + mode + ": " + str(persisted) + " finding(s) recorded as gaps; raw output kept at "
        + str(raw)
    )
    if persisted:
        return EXIT_FINDINGS
    return EXIT_OK


def cmd_record(args):
    manifest = Path(args.manifest)
    if not manifest.exists():
        raise ReviewError("manifest not found: " + str(manifest))
    raw = args.findings
    if raw == "-":
        text = sys.stdin.read()
    elif raw.startswith("@"):
        try:
            text = Path(raw[1:]).read_text(encoding="utf-8")
        except OSError as exc:
            raise ReviewError("could not read findings file '" + raw[1:] + "': " + str(exc))
    else:
        text = raw
    payload = parse_json(text, "findings")
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

    bundle_path = manifest.parent / "ocr-bundle.json"
    bundle_paths = None
    if bundle_path.exists():
        try:
            bundle_text = bundle_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ReviewError("could not read the OCR bundle " + str(bundle_path) + ": " + str(exc))
        bundle = parse_json(bundle_text, "OCR bundle " + str(bundle_path))
        if not isinstance(bundle, dict):
            raise ReviewError("the OCR bundle must be a JSON object; re-run diff to rebuild it")
        required = bundle.get("reviewable_files", [])
        if not isinstance(required, list):
            raise ReviewError("the OCR bundle's reviewable_files must be a list; re-run diff to rebuild it")
        for entry in required:
            if not (isinstance(entry, dict) and isinstance(entry.get("path"), str) and entry["path"]):
                raise ReviewError("the OCR bundle has a malformed reviewable_files entry; re-run diff to rebuild it")
        bundle_paths = {f["path"] for f in required}
        unknown = sorted(covered - bundle_paths)
        if unknown:
            stderr("ERROR: these paths are not in the review bundle:")
            for path in unknown:
                stderr("  " + path)
            return EXIT_USAGE
        missing = [f for f in required if f["path"] not in covered]
        if missing:
            stderr("ERROR: these bundled files were neither reviewed nor skipped:")
            for f in missing:
                stderr("  " + f["path"] + " (" + f["status"] + ")")
            return EXIT_USAGE
        evidence_path = bundle_path
    else:
        evidence_path = manifest.parent / ("ocr-record-" + time.strftime("%Y%m%d-%H%M%S") + ".json")
        evidence_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    persisted = persist_findings(manifest, findings, "delegate", "delegate review", evidence_path)
    if bundle_paths is not None:
        # The same path can appear twice in workspace mode (staged deletion +
        # untracked recreation); coverage counts paths, not bundle entries.
        total = len(bundle_paths)
        covered_count = len(covered & bundle_paths)
    else:
        total = len({f["path"] for f in findings})
        covered_count = total
    reviewed = len({f["path"] for f in findings})
    rate = "100%" if total == 0 or covered_count >= total else (
        str(int(100 * covered_count / total)) + "%"
    )
    print(
        "recorded " + str(persisted) + " finding(s) as gaps; total_files=" + str(total)
        + " reviewed_files=" + str(reviewed)
        + " skipped_files=" + str(len({s["path"] for s in skipped}))
        + " coverage_rate=" + rate
    )
    if persisted:
        return EXIT_FINDINGS
    return EXIT_OK


def build_parser():
    parser = argparse.ArgumentParser(
        prog="code_review.py",
        description="Optional Open Code Review (OCR) bridge for adminwright.",
        epilog=(
            "Exit codes: 0 clean or nothing reviewable, 1 findings recorded, "
            "2 usage/IO/validation failure. Exit 3 is intentionally unused "
            "(claim conflicts do not apply). ADMINWRIGHT_OCR_BIN overrides the "
            "ocr executable path."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

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
    diff_parser.add_argument("--project-root")
    diff_parser.set_defaults(func=cmd_diff)

    scan_parser = subparsers.add_parser(
        "scan", help="Full-file audit review (Audit mode; endpoint engine)"
    )
    scan_parser.add_argument("--manifest", required=True)
    scan_parser.add_argument("--path", help="repo-relative directory or file list")
    scan_parser.add_argument("--rule")
    scan_parser.add_argument("--strict", action="store_true")
    scan_parser.add_argument("--project-root")
    scan_parser.set_defaults(func=cmd_scan)

    record_parser = subparsers.add_parser(
        "record", help="Persist reviewed findings as manifest gaps[]"
    )
    record_parser.add_argument("--manifest", required=True)
    record_parser.add_argument(
        "--findings", required=True, help="JSON, @file, or - for stdin"
    )
    record_parser.set_defaults(func=cmd_record)
    return parser


def main():
    try:
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
    except KeyboardInterrupt:
        stderr("Interrupted")
        return EXIT_USAGE


if __name__ == "__main__":
    raise SystemExit(main())
