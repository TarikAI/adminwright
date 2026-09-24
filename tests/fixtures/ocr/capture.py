#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TarikAI
"""Re-record the ocr reply fixtures in this directory from the REAL `ocr` CLI.

    python tests/fixtures/ocr/capture.py            # all scenarios
    python tests/fixtures/ocr/capture.py review-partial scan-success

Each scenario builds a throwaway git repository, starts fake_llm.FakeLLM (an
OpenAI-compatible endpoint that answers instantly, or fails with HTTP 500 for
chosen files), runs the exact ocr command code_review.py runs, and writes
<scenario>.json: the ocr version, the argv, the exit code and the raw stdout
and stderr. The only edit is replacing local paths (the throwaway repository and
home, and this checkout's rule file) with <repo>, <home> and <skill>, so no
machine's paths are committed. tests/test_code_review.py replays these bodies through
a fake ocr, so the bridge is tested against what ocr really prints rather than
against shapes written from memory (which is how `terminal_state: complete`
came to be rejected).

Isolation: HOME and USERPROFILE (ocr's config and session directory hang off
the user's home directory), APPDATA and LOCALAPPDATA all point into a
temporary directory, and OCR_NO_UPDATE=1 stops the launcher's self-update. The
real ~/.opencodereview is never read or written.
"""

import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
RULES = os.path.join(SKILL_ROOT, "assets", "adminwright-ocr-rules.json")


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fake_llm = load("fake_llm", os.path.join(HERE, "fake_llm.py"))
bridge = load("code_review", os.path.join(SKILL_ROOT, "scripts", "code_review.py"))

BASE = {
    "src/a.py": "x = 1\n",
    "src/b.py": "y = 2\n",
    "src/c.py": "z = 3\n",
    "src/d.py": "w = 4\n",
    "README.md": "# demo\n",
}
CHANGES = {
    "src/a.py": "def run(expression):\n    return eval(expression)\n",
    "src/b.py": "def total(items):\n    return sum(item.price for item in items)\n",
    "src/c.py": "def ratio(a, b):\n    return a / b\n",
    "src/d.py": "def greet(name):\n    return 'hello ' + name\n",
}
COMMENTS = {
    "src/a.py": {
        "content": "eval() on caller input executes arbitrary code; parse the expression instead",
        "existing_code": "    return eval(expression)",
        "category": "security",
        "severity": "high",
    },
    "src/c.py": {
        "content": "Division by zero when b is 0; reject or handle a zero divisor",
        "existing_code": "    return a / b",
        "category": "bug",
        "severity": "medium",
    },
}
REVIEW = ["review", "--audience", "agent", "--format", "json", "--rule", RULES]
SCAN = ["scan", "--audience", "agent", "--format", "json", "--path", "src", "--rule", RULES]

# name: (ocr arguments, workspace changes, FakeLLM options, what it demonstrates)
SCENARIOS = {
    "review-complete": (REVIEW, CHANGES, {}, "every file reviewed: status/terminal_state complete, exit 0"),
    "review-partial": (
        REVIEW, CHANGES, {"fail_paths": ["src/b.py"]},
        "HTTP 500 for src/b.py only: status/terminal_state partial, coverage.failed, exit 0",
    ),
    "review-failed": (
        REVIEW, CHANGES, {"fail_all": True},
        "HTTP 500 for every request: status/terminal_state failed, exit 1",
    ),
    "review-skipped": (REVIEW, {}, {}, "no changes: status/terminal_state skipped, exit 0"),
    "review-no-provider": (REVIEW, CHANGES, None, "no provider configured: nothing on stdout, exit 1"),
    "review-bad-ref": (
        REVIEW + ["--from", "no-such-ref", "--to", "main"], CHANGES, {},
        "an unknown --from ref: an input error, not a provider one, exit 1",
    ),
    "scan-success": (SCAN, CHANGES, {}, "scan has no run manifest: status success, exit 0"),
    "scan-partial": (
        SCAN, CHANGES, {"fail_paths": ["src/b.py"]},
        "HTTP 500 for src/b.py: status completed_with_errors with a scan_subtask_error warning, exit 0",
    ),
    "scan-failed": (SCAN, CHANGES, {"fail_all": True}, "every file failed: nothing on stdout, exit 1"),
}


def writable(func, path, _info):
    os.chmod(path, stat.S_IWRITE)  # git marks its object files read-only on Windows
    func(path)


def git(repo, *args):
    subprocess.run(
        ["git", "-c", "user.name=fixture", "-c", "user.email=fixture@example.invalid",
         "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false"] + list(args),
        cwd=repo, check=True, capture_output=True,
    )


def write_files(root, files):
    for relative, text in files.items():
        path = os.path.join(root, *relative.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)


def make_repo(root, changes):
    os.makedirs(root)
    git(root, "init", "-q")
    write_files(root, BASE)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "base")
    write_files(root, changes)


def isolated_env(home):
    env = dict(os.environ)
    env["HOME"] = home
    env["USERPROFILE"] = home
    env["APPDATA"] = os.path.join(home, "AppData", "Roaming")
    env["LOCALAPPDATA"] = os.path.join(home, "AppData", "Local")
    for key in ("APPDATA", "LOCALAPPDATA"):
        os.makedirs(env[key], exist_ok=True)
    env["OCR_NO_UPDATE"] = "1"
    for key in ("OCR_CONFIG_PATH", "OCR_LLM_URL", "OCR_LLM_TOKEN", "OCR_LLM_MODEL",
                "ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_MODEL"):
        env.pop(key, None)
    return env


def ocr_prefix():
    executable = bridge.ocr_executable()
    if not executable:
        sys.exit("the real ocr CLI is not on PATH (npm install -g @alibaba-group/open-code-review)")
    return bridge.command_prefix(executable)


def scrub(text, replacements):
    for old, new in replacements:
        text = text.replace(old, new).replace(old.replace("\\", "/"), new)
        text = text.replace(old.replace("\\", "\\\\"), new)  # the JSON-escaped spelling
    return text


def capture(name, prefix, version, work):
    arguments, changes, options, note = SCENARIOS[name]
    repo = os.path.join(work, name, "repo")
    home = os.path.join(work, name, "home")
    os.makedirs(home)
    make_repo(repo, changes)
    llm = fake_llm.FakeLLM(comments=COMMENTS, **(options or {})).start()
    try:
        if options is not None:
            fake_llm.isolated_ocr_home(home, llm.url)
        proc = subprocess.run(
            prefix + arguments + ["--repo", repo], env=isolated_env(home),
            capture_output=True, stdin=subprocess.DEVNULL,
        )
    finally:
        llm.stop()
    replacements = [(repo, "<repo>"), (home, "<home>"), (RULES, "<skill>/assets/adminwright-ocr-rules.json")]
    record = {
        "captured_with": version,
        "scenario": note,
        "argv": ["ocr"] + [scrub(a, replacements) for a in arguments] + ["--repo", "<repo>"],
        "returncode": proc.returncode,
        "stdout": scrub(proc.stdout.decode("utf-8"), replacements),
        "stderr": scrub(proc.stderr.decode("utf-8"), replacements),
    }
    target = os.path.join(HERE, name + ".json")
    with open(target, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(record, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    print("%-20s exit %d  %s" % (name, proc.returncode, target))


def main(names):
    unknown = [name for name in names if name not in SCENARIOS]
    if unknown:
        sys.exit("unknown scenario(s): " + ", ".join(unknown) + "; known: " + ", ".join(SCENARIOS))
    prefix = ocr_prefix()
    work = tempfile.mkdtemp(prefix="ocr-capture-")
    try:
        probe = subprocess.run(
            prefix + ["--version"], env=isolated_env(os.path.join(work, "version-home")),
            capture_output=True, stdin=subprocess.DEVNULL,
        )
        version = probe.stdout.decode("utf-8").splitlines()[0].strip()
        for name in names or list(SCENARIOS):
            capture(name, prefix, version, work)
    finally:
        shutil.rmtree(work, onerror=writable)


if __name__ == "__main__":
    main(sys.argv[1:])
