"""Drill: a graceful reload with a chat turn and a document job running.

Roadmap theme 2 (2026-11): every recovery the docs promise is drilled with
the real thing. The 2026-11 functional audit's reload drills (FA-261..264)
were done by hand on the live install; their fixes were each held by an
in-process gate. This drill does it the way the owner's reloads happen: the
REAL guard (``scripts/service/kazma_guard.py``) supervising the REAL server
(``serve.py``) in a private data folder, asked to reload with ``--reload``
while a web chat turn is streaming and an OCR job is running.

It checks what the owner sees:

* the stop finished inside the guard's grace, by the app's own shutdown
  (``child.stopped_gracefully``, never ``child.graceful_timeout``);
* the streamed turn ended with ``done`` flagged ``stopped_for_shutdown``,
  never a made-up "paused" (FA-262);
* the server came back and the document job finished on the attempt it had
  (``released_for_shutdown``, FA-264).

The negative control is a hard kill -- what a crash, or a stop past the
grace, does: the same job comes back on its NEXT attempt after its lease
expires (``lease_expired``). So the drill can tell a graceful reload from
one that is not.

Not drilled here: a Knowledge crawl in flight. A crawl's seed must be a
public address (admission refuses a private one, correctly), so a crawl
would need the internet and would not be repeatable; the crawl's reload
(``interrupted_by: process_ended``, resumed once at the next start) is held
by ``tests/test_crawl_auto_resume.py``.

Opt-in (two full server boots, real OCR): ``KAZMA_TEST_RELOAD_DRILL=1``.
The recovery drills CI job runs it and fails on a skip. Needs Tesseract.
"""

from __future__ import annotations

import json
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

httpx = pytest.importorskip("httpx")

ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / "scripts" / "service" / "kazma_guard.py"
SERVE = ROOT / "serve.py"

pytestmark = [
    pytest.mark.timeout(900),
    pytest.mark.skipif(os.environ.get("KAZMA_TEST_RELOAD_DRILL") != "1",
                       reason="set KAZMA_TEST_RELOAD_DRILL=1 (two real server boots and real OCR)"),
]

#: Words the fake model streams, one every STEP_S: long enough that the turn
#: is still streaming when the reload lands.
WORDS = 200
STEP_S = 0.25
PAGES = 24
LEASE_S = 20


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class _Model(BaseHTTPRequestHandler):
    """An OpenAI-compatible model on loopback: streams slowly, answers at once."""

    def log_message(self, *args: Any) -> None:  # quiet
        return

    def _json(self, code: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        self._json(200, {"object": "list", "data": [{"id": "drill-model", "object": "model"}]})

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        if not body.get("stream"):
            self._json(200, {
                "id": "drill", "object": "chat.completion", "model": "drill-model",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"},
                             "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            })
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        try:
            for i in range(WORDS):
                chunk = {"id": "drill", "object": "chat.completion.chunk", "model": "drill-model",
                         "choices": [{"index": 0, "delta": {"content": f"word{i} "}, "finish_reason": None}]}
                self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                self.wfile.flush()
                time.sleep(STEP_S)
            end = {"id": "drill", "object": "chat.completion.chunk", "model": "drill-model",
                   "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
            self.wfile.write(f"data: {json.dumps(end)}\n\ndata: [DONE]\n\n".encode())
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            return  # the server stopped reading: the reload


def _pdf(path: Path) -> None:
    import fitz  # PyMuPDF: the test makes its input, product code never opens it here

    doc = fitz.open()
    for i in range(PAGES):
        page = doc.new_page()
        for line in range(18):
            page.insert_text((60, 80 + line * 36),
                             f"Reload drill page {i + 1}, line {line + 1}: harbour saffron lantern.",
                             fontsize=14)
    doc.save(str(path))
    doc.close()


_SEED = r"""
import sys
from kazma_core.config_store import get_config_store
from kazma_core.model_registry import initialize_model_registry
store = get_config_store()
store.batch_set([
    ("documents.security.malware_scan", "off", "documents"),
    ("documents.workers.lease_seconds", int(sys.argv[2]), "documents"),
    ("documents.workers.heartbeat_seconds", 5, "documents"),
])
reg = initialize_model_registry(store)
out = reg.upsert_provider({"name": "drill", "base_url": sys.argv[1], "api_key": "",
                           "models": ["drill-model"], "enabled": True})
assert "error" not in out, out
store.close()
print("seeded")
"""


class Install:
    """A private Kazma install: data, home and workspace in *tmp*, its own port."""

    def __init__(self, tmp: Path, model_url: str) -> None:
        self.root = tmp / "install"
        for name in ("install", "home", "workspace", "install/kazma-data", "guard"):
            (tmp / name).mkdir(parents=True, exist_ok=True)
        self.port = _free_port()
        self.secret = secrets.token_urlsafe(32)
        self.guard_dir = tmp / "guard"
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("PYTEST", "KAZMA_", "COV_", "_PYTEST"))}
        env.update({
            "KAZMA_DATA_DIR": str(self.root / "kazma-data"),
            "KAZMA_USER_HOME": str(tmp / "home"),
            "KAZMA_SKILLS_HOME": str(tmp / "home"),
            "KAZMA_WORKSPACE": str(tmp / "workspace"),
            "KAZMA_DB_BACKEND": "sqlite",
            "KAZMA_HOST": "127.0.0.1",
            "KAZMA_PORT": str(self.port),
            "KAZMA_SECRET": self.secret,
            "KAZMA_PROVIDER": "drill",
            "KAZMA_MODEL": "drill-model",
            "KAZMA_EMBEDDER_PREWARM": "0",
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "KAZMA_OPS_ALERTS": "0",
            "KAZMA_PROCESS_PRIORITY": "keep",
            # The guard: this install's server, nothing else.
            "KAZMA_GUARD_CMD": json.dumps([sys.executable, str(SERVE)]),
            "KAZMA_GUARD_CWD": str(self.root),
            "KAZMA_GUARD_HEALTH_URL": f"http://127.0.0.1:{self.port}/health/ready",
            "KAZMA_GUARD_LOG": str(self.guard_dir / "guard.log"),
            "KAZMA_GUARD_STATE": str(self.guard_dir / "state.json"),
            "KAZMA_GUARD_PAUSE_FILE": str(self.guard_dir / "paused"),
            "KAZMA_GUARD_RELOAD_FILE": str(self.guard_dir / "guard.reload"),
            "KAZMA_GUARD_START_TIMEOUT": "240",
            "KAZMA_GUARD_INTERVAL": "3",
            "KAZMA_GUARD_PROBE_TIMEOUT": "10",
            "KAZMA_GUARD_DOCUMENT_BROKER": "off",
            "KAZMA_GUARD_TELEGRAM_TOKEN": "",
            "KAZMA_GUARD_TELEGRAM_CHAT": "",
        })
        self.env = env
        seeded = subprocess.run([sys.executable, "-c", _SEED, model_url, str(LEASE_S)], env=env,
                                cwd=str(self.root), capture_output=True, text=True, timeout=180)
        assert seeded.returncode == 0 and "seeded" in seeded.stdout, seeded.stderr[-2000:]
        self.guard: subprocess.Popen | None = None

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def headers(self) -> dict[str, str]:
        return {"X-Kazma-Secret": self.secret}

    # -- the guard ------------------------------------------------------

    def start(self) -> None:
        self.guard = subprocess.Popen([sys.executable, str(GUARD)], env=self.env, cwd=str(self.root),
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def stop(self) -> None:
        if self.guard and self.guard.poll() is None:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(self.guard.pid), "/T", "/F"],
                               capture_output=True, check=False)
            else:
                self.guard.terminate()
            try:
                self.guard.wait(timeout=30)
            except Exception:  # noqa: BLE001
                self.guard.kill()

    def events(self) -> list[dict]:
        try:
            text = (self.guard_dir / "guard.log").read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []
        out = []
        for line in text.splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out

    def child_pid(self) -> int:
        try:
            return int(json.loads((self.guard_dir / "state.json").read_text(encoding="utf-8")).get("child_pid") or 0)
        except (OSError, ValueError):
            return 0

    def wait_ready(self, timeout: float = 300.0, *, not_pid: int = 0) -> int:
        end = time.time() + timeout
        while time.time() < end:
            pid = self.child_pid()
            if pid and pid != not_pid:
                try:
                    if httpx.get(f"{self.base}/health/ready", timeout=10).status_code == 200:
                        return pid
                except httpx.HTTPError:
                    pass
            time.sleep(1.0)
        raise AssertionError(f"the server never became ready; guard saw {[e.get('event') for e in self.events()][-20:]}")

    def reload(self) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(GUARD), "--reload"], env=self.env, cwd=str(self.root),
                              capture_output=True, text=True, timeout=240)

    # -- documents --------------------------------------------------------

    def upload(self, pdf: Path) -> str:
        res = httpx.post(f"{self.base}/api/documents", params={"force_ocr": "1"},
                         headers={**self.headers(), "X-Document-Filename": pdf.name},
                         content=pdf.read_bytes(), timeout=120)
        assert res.status_code == 200, res.text
        body = res.json()
        job = body.get("job_id") or (body.get("job") or {}).get("job_id") or (body.get("job") or {}).get("id")
        assert job, body
        return str(job)

    def job(self, job_id: str) -> dict:
        res = httpx.get(f"{self.base}/api/documents/jobs/{job_id}", headers=self.headers(), timeout=30)
        assert res.status_code == 200, res.text
        return res.json()["job"]

    def job_events(self, job_id: str) -> list[str]:
        res = httpx.get(f"{self.base}/api/documents/jobs/{job_id}/events", headers=self.headers(), timeout=30)
        assert res.status_code == 200, res.text
        return [str(e.get("event_type") or e.get("type") or "") for e in res.json()["events"]]

    def wait_job(self, job_id: str, states: set[str], timeout: float) -> dict:
        end = time.time() + timeout
        seen: list[str] = []
        while time.time() < end:
            try:
                job = self.job(job_id)
            except (httpx.HTTPError, AssertionError):
                time.sleep(1.0)
                continue
            if not seen or seen[-1] != job.get("state"):
                seen.append(str(job.get("state")))
            if job.get("state") in states:
                return job
            if job.get("state") in {"rejected", "dead_letter", "cancelled"}:
                raise AssertionError(f"the job ended {job.get('state')}: {job}")
            time.sleep(0.5)
        raise AssertionError(f"job never reached {states}; went {seen}")


class _Turn(threading.Thread):
    """A web chat turn read frame by frame, as the page reads it."""

    def __init__(self, install: Install) -> None:
        super().__init__(daemon=True)
        self.install = install
        self.frames: list[tuple[str, dict]] = []
        self.first_token = threading.Event()
        self.error: BaseException | None = None

    def run(self) -> None:
        try:
            with httpx.stream("POST", f"{self.install.base}/api/chat/stream", headers=self.install.headers(),
                              json={"message": "Tell me a very long story, please.", "session_id": "drill-chat"},
                              timeout=httpx.Timeout(240, read=240)) as response:
                assert response.status_code == 200, response.status_code
                event = ""
                for line in response.iter_lines():
                    if line.startswith("event:"):
                        event = line[6:].strip()
                    elif line.startswith("data:"):
                        try:
                            data = json.loads(line[5:].strip() or "{}")
                        except ValueError:
                            data = {"raw": line[5:]}
                        self.frames.append((event, data if isinstance(data, dict) else {"value": data}))
                        if event == "token":
                            self.first_token.set()
        except httpx.HTTPError:
            pass  # the server went away mid-read: judged from the frames
        except BaseException as exc:  # noqa: BLE001
            self.error = exc


@pytest.fixture
def model():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Model)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/v1"
    server.shutdown()


def test_a_graceful_reload_keeps_the_turn_honest_and_the_job_on_its_attempt(tmp_path, model):
    install = Install(tmp_path, model)
    pdf = tmp_path / "drill-scan.pdf"
    _pdf(pdf)
    install.start()
    try:
        first = install.wait_ready()
        job_id = install.upload(pdf)
        running = install.wait_job(job_id, {"ocr_running", "parsing"}, timeout=180)
        attempt = int(running.get("attempt") or 0)
        assert attempt >= 1, running

        turn = _Turn(install)
        turn.start()
        assert turn.first_token.wait(120), f"no token before the reload: {turn.frames[-5:]}"

        asked = time.monotonic()
        reload = install.reload()
        assert reload.returncode == 0, (reload.stdout[-1500:], reload.stderr[-1500:])
        turn.join(timeout=120)
        assert not turn.is_alive() and turn.error is None, turn.error

        # The stop was the app's own, inside the guard's grace.
        names = [e.get("event") for e in install.events()]
        assert "child.stopped_gracefully" in names, names
        assert "child.graceful_timeout" not in names, names
        stopped = next(e for e in install.events() if e.get("event") == "child.stopped_gracefully")
        assert float(stopped.get("after_s") or 0) < 50.0, stopped

        # The streamed turn says it was stopped for the reload -- not "paused".
        done = [d for e, d in turn.frames if e == "done"]
        assert done and done[-1].get("stopped_for_shutdown") is True, turn.frames[-5:]
        text = json.dumps([d for _, d in turn.frames]).lower()
        assert "paused mid-turn" not in text and "no approval card" not in text

        # The server came back, and the job finished on the attempt it had.
        install.wait_ready(not_pid=first)
        finished = install.wait_job(job_id, {"ready"}, timeout=600)
        assert int(finished.get("attempt") or 0) == attempt, (attempt, finished)
        events = install.job_events(job_id)
        assert "released_for_shutdown" in events, events
        assert "lease_expired" not in events, events
        assert time.monotonic() - asked < 900
    finally:
        install.stop()


def test_negative_control_a_hard_kill_costs_the_job_an_attempt(tmp_path, model):
    """A crash (or a stop past the grace) is not a graceful reload: the job's
    lease expires and the next process runs it on its NEXT attempt. The drill
    above would fail on this outcome."""
    install = Install(tmp_path, model)
    pdf = tmp_path / "drill-scan.pdf"
    _pdf(pdf)
    install.start()
    try:
        first = install.wait_ready()
        job_id = install.upload(pdf)
        running = install.wait_job(job_id, {"ocr_running", "parsing"}, timeout=180)
        attempt = int(running.get("attempt") or 0)
        pid = install.child_pid()
        assert pid
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, check=False)
        else:
            os.killpg(os.getpgid(pid), 9)
        install.wait_ready(not_pid=first)
        finished = install.wait_job(job_id, {"ready"}, timeout=600)
        assert int(finished.get("attempt") or 0) == attempt + 1, (attempt, finished)
        events = install.job_events(job_id)
        assert "lease_expired" in events and "released_for_shutdown" not in events, events
    finally:
        install.stop()
