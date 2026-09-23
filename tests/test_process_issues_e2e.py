"""Сквозная проверка tools/process_issues.py: настоящий git и поддельный GitHub API.

Проект копируется во временный репозиторий с локальным «origin», обработчик
запускается отдельным процессом и ходит в API на localhost.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from helpers import ROOT, form_body, issue


class FakeGitHub(BaseHTTPRequestHandler):
    issues: list = []
    calls: list = []

    def log_message(self, *args):
        pass

    def _send(self, status, payload=None):
        body = json.dumps(payload).encode() if payload is not None else b""
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length) or b"null")

    def do_GET(self):
        self.calls.append(("GET", self.path, None))
        if self.path == "/repos/o/r":
            return self._send(200, {"default_branch": "main"})
        if self.path.startswith("/repos/o/r/issues?"):
            return self._send(200, self.issues if "page=1" in self.path else [])
        if self.path.endswith("/comments?per_page=100"):
            return self._send(200, [])
        self._send(404, {"message": "Not Found"})

    def do_POST(self):
        self.calls.append(("POST", self.path, self._body()))
        self._send(204 if self.path.endswith("/dispatches") else 201, None if self.path.endswith("/dispatches") else {})

    def do_PATCH(self):
        self.calls.append(("PATCH", self.path, self._body()))
        self._send(200, {})


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


@unittest.skipUnless(shutil.which("git"), "нужен git")
class ProcessIssuesEndToEndTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.origin = base / "origin.git"
        self.work = base / "work"
        git(base, "init", "--bare", "-b", "main", str(self.origin))
        shutil.copytree(ROOT, self.work, ignore=shutil.ignore_patterns("build", "__pycache__", ".git"))
        git(self.work, "init", "-b", "main")
        git(self.work, "add", "-A")
        git(self.work, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "init")
        git(self.work, "remote", "add", "origin", str(self.origin))
        git(self.work, "push", "origin", "main")

        FakeGitHub.calls = []
        FakeGitHub.issues = [
            issue(5, form_body(), login="ivan"),
            issue(6, form_body(name="Иван"), login="petr"),
            issue(7, "Имя: Анна Смирнова\nФакультет: ВМК\nКурс: аспирантура\nID ответа: 42\n", login="form-bot"),
            issue(8, "Просто вопрос про текст", login="someone"),
        ]
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeGitHub)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def run_bot(self):
        env = dict(
            os.environ,
            GITHUB_TOKEN="test",
            GITHUB_REPOSITORY="o/r",
            GITHUB_API_URL=f"http://127.0.0.1:{self.server.server_address[1]}",
            FORM_BOT_LOGIN="form-bot",
            NO_PROXY="127.0.0.1,localhost",
            no_proxy="127.0.0.1,localhost",
        )
        env.pop("GITHUB_ACTIONS", None)
        return subprocess.run(
            [sys.executable, str(self.work / "tools" / "process_issues.py")],
            env=env, capture_output=True, text=True, timeout=60,
        )

    def test_signatures_are_committed_and_issues_answered(self):
        result = self.run_bot()
        self.assertEqual(result.returncode, 0, result.stderr)

        csv_text = git(self.work, "--git-dir", str(self.origin), "show", "main:signatures.csv")
        lines = csv_text.strip().splitlines()
        self.assertEqual(len(lines), 3, csv_text)
        self.assertIn("gh-5,", lines[1])
        self.assertIn("gh:ivan", lines[1])
        self.assertTrue(lines[2].startswith("ya-42,"))
        self.assertIn("Подписи: +2 (#5, #7)", git(self.work, "--git-dir", str(self.origin), "log", "-1", "--format=%s"))

        calls = FakeGitHub.calls
        self.assertIn(("POST", "/repos/o/r/actions/workflows/publish.yml/dispatches", {"ref": "main"}), calls)
        comments = {path: body["body"] for method, path, body in calls if method == "POST" and path.endswith("/comments")}
        self.assertIn("под номером 1", comments["/repos/o/r/issues/5/comments"])
        self.assertIn("Не получилось", comments["/repos/o/r/issues/6/comments"])
        self.assertNotIn("/repos/o/r/issues/7/comments", comments)  # заявки формы закрываются молча
        closed = {path: body["state_reason"] for method, path, body in calls if method == "PATCH"}
        self.assertEqual(closed, {
            "/repos/o/r/issues/5": "completed",
            "/repos/o/r/issues/6": "not_planned",
            "/repos/o/r/issues/7": "completed",
        })

    def test_second_run_only_closes_leftovers(self):
        self.assertEqual(self.run_bot().returncode, 0)
        FakeGitHub.calls = []
        FakeGitHub.issues = FakeGitHub.issues[:1]  # будто закрыть #5 в прошлый раз не удалось
        result = self.run_bot()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("POST", [m for m, p, _ in FakeGitHub.calls if p.endswith("/dispatches")])
        self.assertIn(("PATCH", "/repos/o/r/issues/5", {"state": "closed", "state_reason": "completed"}), FakeGitHub.calls)
        csv_text = git(self.work, "--git-dir", str(self.origin), "show", "main:signatures.csv")
        self.assertEqual(len(csv_text.strip().splitlines()), 3)


if __name__ == "__main__":
    unittest.main()
