"""Every deploy is checked, and undone if it fails (resilience plan C2).

``scripts/deploy_live.py`` pulls into the live install, reloads it, checks it
(the new commit answering, ready and deep health, one test turn kept out of
memory, a clean log since the boot) and, when a check fails, reverts the
deployed commits on ``main``, pulls the revert into the live install, reloads
and pages the owner naming the failed check.

Real git repositories in a temporary folder (an origin, a dev clone, a live
clone) and a stub server; the reload and the page are recorded, never run. A
good deploy passes; a planted bad one is undone; ``--no-revert`` (the
negative control) leaves it deployed.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("_deploy_live", ROOT / "scripts" / "deploy_live.py")
deploy_live = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = deploy_live
spec.loader.exec_module(deploy_live)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def repos(tmp_path, monkeypatch):
    # The machine's git settings do not decide the deploy: with none (a fresh
    # machine, CI on 2026-11-09), a pull into a checkout with its own commit
    # stopped at "Need to specify how to reconcile divergent branches". The
    # developer's own `pull.rebase` had hidden it.
    empty = tmp_path / "gitconfig"
    empty.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(origin)], capture_output=True, check=True)
    dev = tmp_path / "dev"
    subprocess.run(["git", "clone", str(origin), str(dev)], capture_output=True, check=True)
    for repo in (dev,):
        _git(repo, "config", "user.email", "t@example.com")
        _git(repo, "config", "user.name", "t")
    (dev / "app.txt").write_text("v1\n", encoding="utf-8")
    _git(dev, "add", "app.txt")
    _git(dev, "commit", "-m", "v1")
    _git(dev, "push", "origin", "HEAD:main")
    live = tmp_path / "live"
    subprocess.run(["git", "clone", str(origin), str(live)], capture_output=True, check=True)
    _git(live, "config", "user.email", "t@example.com")
    _git(live, "config", "user.name", "t")
    return dev, live


def _push(dev: Path, text: str) -> str:
    (dev / "app.txt").write_text(text, encoding="utf-8")
    _git(dev, "commit", "-am", text.strip())
    _git(dev, "push", "origin", "HEAD:main")
    return _git(dev, "rev-parse", "HEAD")


class _Stub(BaseHTTPRequestHandler):
    commit = ""
    answer = "OK"
    calls: list[str] = []

    def _send(self, status, body, ctype="application/json"):
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        self.calls.append(f"GET {self.path}")
        if self.path == "/health/live":
            return self._send(200, {"build": {"commit": self.commit[:12]}})
        return self._send(200, {"status": "ready"})

    def do_PUT(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.calls.append(f"PUT {self.path}")
        self._send(200, {"ok": True})

    def do_DELETE(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.calls.append(f"DELETE {self.path}")
        self._send(200, {"ok": True})

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.calls.append(f"POST {self.path}")
        frame = f"event: done\ndata: {json.dumps({'content': self.answer})}\n\n".encode()
        self._send(200, frame, "text/event-stream")

    def log_message(self, *args):
        return


@pytest.fixture
def stub():
    handler = type("H", (_Stub,), {"calls": [], "answer": "OK", "commit": ""})
    server = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield handler, f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


@pytest.fixture
def recorded(monkeypatch):
    calls = {"live": [], "pages": []}

    def run_live(live, *args, timeout=1800):
        calls["live"].append(args)
        return 0, ""

    monkeypatch.setattr(deploy_live, "run_live", run_live)
    monkeypatch.setattr(deploy_live, "page", lambda live, title, detail: calls["pages"].append((title, detail)))
    return calls


def _server(live: Path, url: str, handler, commit_after: str):
    handler.commit = commit_after
    return deploy_live.Server(live, base=url)


def test_a_good_deploy_passes(repos, stub, recorded):
    dev, live = repos
    handler, url = stub
    after = _push(dev, "v2\n")
    assert deploy_live.deploy(live, dev=dev, server=_server(live, url, handler, after)) == 0
    assert _git(live, "rev-parse", "HEAD") == after
    assert any(a[:2] == ("scripts/service/kazma_guard.py", "--reload") for a in recorded["live"])
    assert recorded["pages"] == []
    # The test chat was kept out of memory first, and deleted after.
    puts = [c for c in handler.calls if c.startswith("PUT /api/memory/v2/chats/deploy-check-")]
    deletes = [c for c in handler.calls if c.startswith("DELETE /api/chat/sessions/deploy-check-")]
    assert puts and deletes and handler.calls.index(puts[0]) < handler.calls.index(deletes[0])


def test_a_bad_deploy_is_undone_and_the_page_names_the_check(repos, stub, recorded):
    dev, live = repos
    handler, url = stub
    before = _git(live, "rev-parse", "HEAD")
    after = _push(dev, "v2 broken\n")
    handler.answer = "Something went wrong."
    assert deploy_live.deploy(live, dev=dev, server=_server(live, url, handler, after)) == 1
    # Reverted on main and pulled: the live tree is the one before the deploy.
    assert (live / "app.txt").read_text(encoding="utf-8") == "v1\n"
    assert _git(live, "rev-parse", "HEAD") != before          # a revert commit, never a reset
    assert "Revert" in _git(dev, "log", "-1", "--format=%s")
    (title, detail), = recorded["pages"]
    assert title == "A deploy failed and was undone" and "the test turn answered" in detail


def test_without_revert_the_bad_deploy_stays(repos, stub, recorded):
    # The negative control.
    dev, live = repos
    handler, url = stub
    after = _push(dev, "v2 broken\n")
    handler.answer = "nope"
    assert deploy_live.deploy(live, dev=dev, revert=False, server=_server(live, url, handler, after)) == 1
    assert (live / "app.txt").read_text(encoding="utf-8") == "v2 broken\n"
    assert recorded["pages"][0][0] == "A deploy failed its checks"


def test_a_modified_live_checkout_is_left_alone(repos, recorded):
    dev, live = repos
    (live / "app.txt").write_text("edited by hand\n", encoding="utf-8")
    _push(dev, "v2\n")
    assert deploy_live.deploy(live, dev=dev) == 2
    assert recorded["live"] == []


def test_errors_in_the_log_since_the_boot_fail_the_deploy(tmp_path):
    log = tmp_path / ".kazma" / "kazma.log"
    log.parent.mkdir()
    lines = [
        {"timestamp": "2026-11-06T10:00:00+00:00", "level": "ERROR", "logger": "old", "message": "before"},
        {"timestamp": "2026-11-06T10:05:00+00:00", "level": "INFO", "logger": "x", "message": "fine"},
        {"timestamp": "2026-11-06T10:06:00+00:00", "level": "CRITICAL", "logger": "y", "message": "after"},
    ]
    log.write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")
    from datetime import datetime

    since = datetime.fromisoformat("2026-11-06T10:01:00+00:00").timestamp()
    assert deploy_live.log_errors(tmp_path, since) == ["CRITICAL y: after"]


def _guard_events(live: Path, *events: tuple[str, str]) -> None:
    log = live / ".kazma" / "guard.log"
    log.parent.mkdir(exist_ok=True)
    log.write_text("\n".join(json.dumps({"ts": ts, "level": "info", "event": e}) for ts, e in events),
                   encoding="utf-8")


def test_the_old_servers_stop_is_not_the_new_builds_error(tmp_path):
    """2026-11-12: the guard's stop also stopped the old server's restic
    check, which logged an ERROR 4 s before the new server was spawned; read
    from the reload request, the deploy reverted a good commit."""
    from datetime import datetime

    log = tmp_path / ".kazma" / "kazma.log"
    log.parent.mkdir()
    log.write_text("\n".join(json.dumps(x) for x in [
        {"timestamp": "2026-11-12T17:02:40+00:00", "level": "ERROR", "logger": "old", "message": "stopped mid-check"},
        {"timestamp": "2026-11-12T17:03:30+00:00", "level": "ERROR", "logger": "new", "message": "a new error"},
    ]), encoding="utf-8")
    _guard_events(tmp_path, ("2026-11-12T16:00:00+00:00", "child.spawned"),
                  ("2026-11-12T17:02:44+00:00", "child.stopped_gracefully"),
                  ("2026-11-12T17:02:45+00:00", "child.spawned"))
    requested = datetime.fromisoformat("2026-11-12T17:02:30+00:00").timestamp()
    boot = deploy_live.new_boot(tmp_path, requested)
    assert boot == datetime.fromisoformat("2026-11-12T17:02:45+00:00").timestamp()
    assert deploy_live.log_errors(tmp_path, boot) == ["ERROR new: a new error"], "an error after the spawn still fails"
    # The negative control: read from the request, the old server's line counts.
    assert len(deploy_live.log_errors(tmp_path, requested)) == 2


def test_without_a_guard_log_the_window_starts_at_the_request(tmp_path):
    assert deploy_live.new_boot(tmp_path, 123.0) == 123.0


def test_a_live_checkout_with_its_own_commit_is_undone_too(repos, stub, recorded, monkeypatch):
    # The live install keeps a local commit (its kazma.yaml): its pull is a
    # merge, and what was deployed is what origin/main gained.
    dev, live = repos
    handler, url = stub
    (live / "local.yaml").write_text("mine\n", encoding="utf-8")
    _git(live, "add", "local.yaml")
    _git(live, "commit", "-m", "local settings")
    _push(dev, "v2 broken\n")
    handler.answer = "no"
    handler.commit = ""
    server = deploy_live.Server(live, base=url)
    monkeypatch.setattr(deploy_live, "wait_for_build", lambda srv, commit, timeout=900.0: "")
    assert deploy_live.deploy(live, dev=dev, server=server) == 1
    assert (live / "app.txt").read_text(encoding="utf-8") == "v1\n"
    assert (live / "local.yaml").read_text(encoding="utf-8") == "mine\n"
