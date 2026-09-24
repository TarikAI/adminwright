# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TarikAI
"""A stand-in OpenAI-compatible chat-completions endpoint for the real `ocr` CLI.

Standard library only. It never runs a model: every request ocr sends during a
`review` or `scan` (grouping, planning, the review itself, the comment filter,
dedup, the project summary) gets a fixed, valid answer, so a real run finishes
in seconds. Two knobs shape the outcome:

* `comments` maps a repository path to one finding; the first review request
  that shows that path answers with a `code_comment` call for it (once per path,
  so repeated review rounds do not duplicate it).
* `fail_paths` (or `fail_all`) answers HTTP 500 to every request that shows one
  of those paths, the way an overloaded gateway does. ocr retries, then marks
  the file failed: that is how a real `partial` or `failed` run is produced.

Used by capture.py (which recorded the fixtures in this directory) and by the
opt-in RealCliSmoke tests in tests/test_code_review.py.
"""

import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REVIEW_FILE = re.compile(r'<file path="([^"]+)"')
SCAN_FILE = re.compile(r"<current_file_path>([^<]+)</current_file_path>")
GROUPING_LINE = re.compile(r"^\[(\d+)\]\s+\S+\s+(.+?)(?:\s+\(\+\d+/-\d+\))?\s*$", re.M)
COMMENT_ID = re.compile(r"\bc-\d+\b")


def text_of(message):
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            part.get("text", "") for part in content if isinstance(part, dict)
        )
    return ""


class FakeLLM(object):
    def __init__(self, comments=None, fail_paths=(), fail_all=False):
        self.comments = dict(comments or {})
        self.fail_paths = set(fail_paths)
        self.fail_all = fail_all
        self.requests = []
        self.failures = 0
        self._commented = set()
        self._lock = threading.Lock()
        self._server = None
        self._thread = None
        self._counter = 0

    # -- lifecycle -----------------------------------------------------------

    def start(self, port=0):
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                self.reply(200, {"object": "list", "data": [{"id": "fake-model", "object": "model"}]})

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length)
                try:
                    request = json.loads(raw.decode("utf-8") or "{}")
                except ValueError:
                    request = {}
                status, body = fake.answer(request)
                self.reply(status, body)

            def reply(self, status, body):
                data = json.dumps(body).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self._server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self):
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    @property
    def url(self):
        return "http://127.0.0.1:%d/v1" % self._server.server_address[1]

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()

    # -- answers ---------------------------------------------------------------

    def answer(self, request):
        messages = request.get("messages") or []
        tools = [
            (tool.get("function") or {}).get("name")
            for tool in (request.get("tools") or [])
            if isinstance(tool, dict)
        ]
        system = "\n".join(text_of(m) for m in messages if m.get("role") == "system")
        user = "\n".join(text_of(m) for m in messages if m.get("role") == "user")
        paths = REVIEW_FILE.findall(user) + [p.strip() for p in SCAN_FILE.findall(user)]
        with self._lock:
            self._counter += 1
            number = self._counter
            self.requests.append({"tools": tools, "paths": paths})
            if self.fail_all or self.fail_paths.intersection(paths):
                self.failures += 1
                return 500, {"error": {"message": "fake upstream failure", "type": "server_error"}}
            pending = []
            if "code_comment" in tools and not any(m.get("role") == "tool" for m in messages):
                for path in paths:
                    if path in self.comments and path not in self._commented:
                        self._commented.add(path)
                        pending.append(dict(self.comments[path], path=path))
        if "code_comment" in tools:
            if pending:
                return 200, self.tool_call(number, request, "code_comment", {"comments": pending})
            return 200, self.tool_call(number, request, "task_done", {"state": "DONE"})
        if "approve_all_comments" in tools:
            return 200, self.tool_call(number, request, "approve_all_comments", {})
        return 200, self.text(number, request, self.plain_answer(system, user))

    @staticmethod
    def plain_answer(system, user):
        lowered = system.lower()
        if "file grouping assistant" in lowered:
            groups = [
                {"label": path.strip(), "files": [int(index)]}
                for index, path in GROUPING_LINE.findall(user)
            ]
            return json.dumps(groups)
        if "scan-mode review planner" in lowered:
            return json.dumps({"summary": "fake plan", "checkpoints": []})
        if "task planning" in lowered:
            return "Summary: fake plan\n\nIssues\n"
        if "near-duplicate" in lowered:
            ids = sorted(set(COMMENT_ID.findall(user)), key=lambda text: int(text[2:]))
            return json.dumps({"groups": [{"members": [item]} for item in ids]})
        if "project-level summary" in lowered:
            return "### Top Issues\n- none"
        return "ok"

    @staticmethod
    def envelope(number, request, message, finish):
        return {
            "id": "chatcmpl-fake-%d" % number,
            "object": "chat.completion",
            "created": int(time.time()),
            "model": request.get("model") or "fake-model",
            "choices": [{"index": 0, "message": message, "finish_reason": finish}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25},
        }

    def tool_call(self, number, request, name, arguments):
        message = {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "call_fake_%d" % number,
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)},
            }],
        }
        return self.envelope(number, request, message, "tool_calls")

    def text(self, number, request, content):
        return self.envelope(number, request, {"role": "assistant", "content": content}, "stop")


def isolated_ocr_home(home, url, provider="fakegw", model="fake-model"):
    """Write an ocr config.json under <home>/.opencodereview pointing at url.

    Run ocr with HOME and USERPROFILE set to <home> (ocr resolves its config and
    session directory through the user's home directory), so the real user's
    ~/.opencodereview is never read or written.
    """
    import os

    directory = os.path.join(home, ".opencodereview")
    os.makedirs(directory, exist_ok=True)
    config = {
        "provider": provider,
        "custom_providers": {
            provider: {"api_key": "sk-fake", "url": url, "protocol": "openai", "model": model}
        },
    }
    with open(os.path.join(directory, "config.json"), "w", encoding="utf-8", newline="\n") as handle:
        json.dump(config, handle, indent=2)
    return os.path.join(directory, "config.json")
