#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TarikAI
"""Golden-fixture eval runner.

The learning loop changes guidance and the validator over time. Without a fitness
function that is drift, not improvement: a rule tightened for one project can
quietly start failing builds that were always correct, and nobody notices until a
user does.

Each fixture is a small project with a manifest and a recorded expectation of
which rules fire, at which profile, at which severity. The runner asserts the
finding SET, not the message text, so wording can improve freely while behaviour
stays pinned.

When a change flips a fixture, one of two things is true and you must decide
which: the change is wrong, or the expectation was. Update the expectation in the
same commit as the change, with the reason in the message. Never update it to
make CI quiet.

Run:      python evals/run.py
Refresh:  python evals/run.py --update   (review the diff before committing)
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

EVAL_ROOT = Path(__file__).resolve().parent
SKILL_ROOT = EVAL_ROOT.parent
SCRIPT = SKILL_ROOT / "scripts" / "admin_console_manifest.py"
PROFILES = ("internal", "standard", "regulated")


def validate(project_root, profile):
    """Run the validator and return {rule: severity} plus the exit code."""
    manifest = project_root / ".admin-console" / "manifest.json"
    completed = subprocess.run(
        [
            sys.executable, str(SCRIPT), "validate",
            "--manifest", str(manifest),
            "--project-root", str(project_root),
            "--phase", "release",
            "--profile", profile,
            "--json",
        ],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        raise SystemExit(
            "validator did not return JSON for " + project_root.name + " at " + profile
            + "\nstdout: " + completed.stdout[:500]
            + "\nstderr: " + completed.stderr[:500]
        )
    findings = {}
    for item in payload.get("findings", []):
        rule = item.get("rule")
        severity = item.get("severity")
        # Keep the strongest severity seen for a rule; that is what decides pass/fail.
        if findings.get(rule) != "error":
            findings[rule] = severity
    return {"exit": completed.returncode, "findings": findings}


def observed(fixture):
    return {profile: validate(fixture, profile) for profile in PROFILES}


def compare(name, expected, actual):
    """Return a list of human-readable differences."""
    problems = []
    for profile in PROFILES:
        want = expected.get(profile, {})
        got = actual.get(profile, {})
        if want.get("exit") != got.get("exit"):
            problems.append(
                "  %s @ %s: exit %s, expected %s"
                % (name, profile, got.get("exit"), want.get("exit"))
            )
        want_rules = want.get("findings", {})
        got_rules = got.get("findings", {})
        for rule in sorted(set(want_rules) | set(got_rules)):
            if want_rules.get(rule) == got_rules.get(rule):
                continue
            if rule not in want_rules:
                problems.append("  %s @ %s: NEW %s (%s)" % (name, profile, rule, got_rules[rule]))
            elif rule not in got_rules:
                problems.append("  %s @ %s: GONE %s (was %s)" % (name, profile, rule, want_rules[rule]))
            else:
                problems.append(
                    "  %s @ %s: %s severity %s, expected %s"
                    % (name, profile, rule, got_rules[rule], want_rules[rule])
                )
    return problems


def main():
    parser = argparse.ArgumentParser(description="Run golden-fixture evals")
    parser.add_argument("--update", action="store_true",
                        help="rewrite expectations from current behaviour; review the diff")
    args = parser.parse_args()

    fixtures = sorted(d for d in (EVAL_ROOT / "fixtures").iterdir() if d.is_dir())
    if not fixtures:
        raise SystemExit("no fixtures found under evals/fixtures/")

    failures = []
    for fixture in fixtures:
        actual = observed(fixture)
        expectation_path = fixture / "expected-findings.json"
        if args.update:
            expectation_path.write_text(
                json.dumps(actual, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            print("updated  " + fixture.name)
            continue
        if not expectation_path.exists():
            failures.append("  " + fixture.name + ": no expected-findings.json (run --update)")
            continue
        expected = json.loads(expectation_path.read_text(encoding="utf-8"))
        problems = compare(fixture.name, expected, actual)
        if problems:
            failures.extend(problems)
            print("FAIL     " + fixture.name)
        else:
            print("ok       " + fixture.name)

    if args.update:
        print("\nExpectations rewritten. Read the diff before committing: a change you did not")
        print("intend is exactly what this suite exists to catch.")
        return 0

    if failures:
        print("\nBehaviour changed against the golden fixtures:\n")
        print("\n".join(failures))
        print("\nDecide which is wrong: the change, or the expectation. If the change is")
        print("correct, update the fixture in the same commit and say why in the message.")
        return 1

    print("\nAll fixtures match.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
