"""The guard's heartbeat to the outside observer (resilience plan B1).

Everything else in the guard runs on this machine, so none of it can say the
machine is off, asleep or cut off. Once the owner pairs the install
(``<install>/.kazma/observer.key``), the guard's ``OutsideWatch`` sends a
signed heartbeat to the observer every minute. The rules held here:

* off until paired, and ``KAZMA_GUARD_OUTSIDE_WATCH=off`` wins over a key;
* the body is an allowlist: states, numbers and check NAMES -- a probe's
  detail, a path, chat text or an error never leave the machine, whatever
  the snapshot holds;
* signed ``v1=<HMAC-SHA256>`` over ``"<timestamp>.<body>"``, the vector
  shared with the observer's verifier (tests/fixtures/observer);
* a key file the guard cannot restrict to its own account, or one too short,
  is never used; the address must be https (http only to this machine);
* one send at a time, on its own thread: an observer that hangs never holds
  up the guard;
* the readiness probe carries the failing checks' names (ProbeResult.checks)
  and still unpacks as ``(healthy, detail)``.

Each rule has a negative control. Nothing here reaches the network.
"""

from __future__ import annotations

import hashlib
import hmac
import importlib.util
import json
import os
import sys
import threading
import time
import urllib.error
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / "scripts" / "service" / "kazma_guard.py"
VECTOR = json.loads((ROOT / "tests" / "fixtures" / "observer" / "heartbeat_vector.json")
                    .read_text(encoding="utf-8"))
KEY = "k" * 40


def _load_guard():
    spec = importlib.util.spec_from_file_location("_outside_guard", GUARD)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


guard = _load_guard()


class _Resp:
    def __init__(self, status: int) -> None:
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Opener:
    """urllib.request.urlopen, recorded."""

    def __init__(self, status: int = 200, error: Exception | None = None,
                 hold: threading.Event | None = None) -> None:
        self.status = status
        self.error = error
        self.hold = hold
        self.requests: list = []

    def __call__(self, req, timeout=None):
        self.requests.append(req)
        if self.hold is not None:
            self.hold.wait(10)
        if self.error is not None:
            raise self.error
        return _Resp(self.status)


class _Harness:
    def __init__(self, monkeypatch, tmp_path, *, key: str | None = KEY, restrict=True,
                 opener: _Opener | None = None) -> None:
        self.now = 1000.0
        wall0 = time.time()
        monkeypatch.setattr(guard, "time", SimpleNamespace(
            monotonic=lambda: self.now, time=lambda: wall0 + self.now, sleep=lambda s: None))
        monkeypatch.setenv("KAZMA_GUARD_OUTSIDE_WATCH", "auto")
        monkeypatch.delenv("KAZMA_GUARD_OUTSIDE_WATCH_URL", raising=False)
        monkeypatch.setenv("KAZMA_GUARD_DOCUMENT_BROKER", "off")
        monkeypatch.setenv("KAZMA_GUARD_TELEGRAM_TOKEN", "")
        monkeypatch.setenv("KAZMA_GUARD_TELEGRAM_CHAT", "")
        monkeypatch.setattr(guard, "_cloudflared_state", lambda: "running")
        monkeypatch.setattr(guard, "_power_state", lambda: None)
        self.key_path = tmp_path / "observer.key"
        if key is not None:
            self.key_path.write_text(key, encoding="utf-8")
        self.restricted: list[Path] = []

        def _restrict(path: Path) -> bool:
            self.restricted.append(path)
            return restrict

        self.opener = opener or _Opener()
        self.g = guard.Guard()
        self.g.broker.wanted = False
        self.g.started_at = guard.time.time() - 30
        self.g.outside = guard.OutsideWatch(self.g, opener=self.opener, key_path=self.key_path,
                                            restrict=_restrict)
        self.watch = self.g.outside

    def tick(self, seconds: float = 1.0) -> None:
        self.g.outside.tick()
        thread = self.watch._thread
        if thread is not None and (self.opener.hold is None or self.opener.hold.is_set()):
            thread.join(5)
        self.now += seconds

    def events(self) -> list[str]:
        log = Path(os.environ["KAZMA_GUARD_LOG"])
        out = []
        for line in log.read_text(encoding="utf-8").splitlines() if log.exists() else []:
            try:
                out.append(json.loads(line).get("event", ""))
            except ValueError:
                continue
        return out


# -- the signature ------------------------------------------------------------


def test_the_signature_matches_the_shared_vector():
    sig = guard.sign_heartbeat(VECTOR["key"].encode(), VECTOR["timestamp"],
                               VECTOR["body"].encode())
    assert sig == VECTOR["signature"]
    # Control: another timestamp, or another body, is another signature.
    assert guard.sign_heartbeat(VECTOR["key"].encode(), VECTOR["timestamp"] + 1,
                                VECTOR["body"].encode()) != VECTOR["signature"]
    assert guard.sign_heartbeat(VECTOR["key"].encode(), VECTOR["timestamp"],
                                b'{"v":2}') != VECTOR["signature"]


# -- off until paired ----------------------------------------------------------------


def test_off_until_paired(monkeypatch, tmp_path):
    h = _Harness(monkeypatch, tmp_path, key=None)
    for _ in range(200):
        h.tick()
    assert h.opener.requests == []
    assert h.watch.state == "off"


def test_the_off_switch_wins_over_a_key(monkeypatch, tmp_path):
    h = _Harness(monkeypatch, tmp_path)
    monkeypatch.setenv("KAZMA_GUARD_OUTSIDE_WATCH", "off")
    for _ in range(200):
        h.tick()
    assert h.opener.requests == []
    assert h.watch.record()["mode"] == "off"


def test_a_key_the_guard_cannot_restrict_is_never_used(monkeypatch, tmp_path):
    h = _Harness(monkeypatch, tmp_path, restrict=False)
    for _ in range(200):
        h.tick()
    assert h.opener.requests == []
    assert h.watch.state == "key_unprotected"
    assert "outside_watch.key_unprotected" in h.events()


def test_a_short_key_is_refused(monkeypatch, tmp_path):
    h = _Harness(monkeypatch, tmp_path, key="short")
    h.tick()
    assert h.opener.requests == [] and h.watch.state == "key_invalid"


def test_the_address_must_be_https_or_this_machine(monkeypatch, tmp_path):
    h = _Harness(monkeypatch, tmp_path)
    monkeypatch.setenv("KAZMA_GUARD_OUTSIDE_WATCH_URL", "http://observer.example/v1/heartbeat")
    h.tick()
    assert h.opener.requests == [] and h.watch.state == "bad_url"
    monkeypatch.setenv("KAZMA_GUARD_OUTSIDE_WATCH_URL", "http://127.0.0.1:8787/v1/heartbeat")
    assert guard._outside_watch_url() == "http://127.0.0.1:8787/v1/heartbeat"
    monkeypatch.delenv("KAZMA_GUARD_OUTSIDE_WATCH_URL")
    assert guard._outside_watch_url() == guard.DEFAULT_OUTSIDE_WATCH_URL


# -- the send ------------------------------------------------------------------


def test_a_paired_guard_sends_a_signed_heartbeat_a_minute(monkeypatch, tmp_path):
    h = _Harness(monkeypatch, tmp_path)
    for _ in range(185):
        h.tick()
    assert len(h.opener.requests) == 4  # at 0, 60, 120 and 180 s
    req = h.opener.requests[0]
    headers = {k.lower(): v for k, v in req.header_items()}
    ts = int(headers["x-kazma-timestamp"])
    assert headers["x-kazma-signature"] == guard.sign_heartbeat(KEY.encode(), ts, req.data)
    body = json.loads(req.data)
    assert body["v"] == 1 and body["install"] == headers["x-kazma-install"]
    assert [json.loads(r.data)["seq"] for r in h.opener.requests] == [1, 2, 3, 4]
    assert h.watch.state == "reached" and h.watch.last_ack is not None
    assert guard._read_state()["outside_watch"]["state"] == "reached"
    assert "outside_watch.reached" in h.events()
    assert h.restricted == [h.key_path]  # restricted once, not every send


def test_every_answered_heartbeat_reaches_the_state_file(monkeypatch, tmp_path):
    """The age --status shows is the last answer's, not the first's (live
    2026-11-06: "last answered 403 s ago" over a minute's heartbeats)."""
    h = _Harness(monkeypatch, tmp_path)
    for _ in range(125):
        h.tick()
    assert len(h.opener.requests) == 3
    recorded = guard._read_state()["outside_watch"]["last_ack"]
    assert recorded == h.watch.last_ack
    # Control: the first answer's time is older than the one recorded.
    first_answer = guard.time.time() - 125 + 1  # the harness clock at the first tick
    assert recorded > first_answer + 100


def test_the_heartbeat_names_the_guard_not_urllib(monkeypatch, tmp_path):
    """Live 2026-11-06: Cloudflare's bot check refused urllib's own name
    ("Python-urllib/3.x", error 1010), so every heartbeat was a 403 before it
    reached the observer, while the observer itself answered."""
    h = _Harness(monkeypatch, tmp_path)
    h.tick()
    headers = {k.lower(): v for k, v in h.opener.requests[0].header_items()}
    assert headers["user-agent"] == guard.GUARD_USER_AGENT
    assert "urllib" not in headers["user-agent"].lower()


def _requests_not_built_by_the_helper(source: str) -> list[str]:
    """Each request in *source* the guard does not build through ``_request``:
    a urllib Request made anywhere else, or urlopen handed anything but a
    request ``_request`` built (a bare URL goes out as "Python-urllib")."""
    import ast

    def is_attr(node, name):
        return isinstance(node, ast.Attribute) and node.attr == name

    def from_helper(node):
        return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_request"

    found = []
    for fn in ast.walk(ast.parse(source)):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        built = {t.id for n in ast.walk(fn) if isinstance(n, ast.Assign) and from_helper(n.value)
                 for t in n.targets if isinstance(t, ast.Name)}
        for call in ast.walk(fn):
            if not isinstance(call, ast.Call):
                continue
            if is_attr(call.func, "Request") and fn.name != "_request":
                found.append(f"{fn.name}:{call.lineno} Request")
            if is_attr(call.func, "urlopen"):
                arg = call.args[0] if call.args else None
                if not (from_helper(arg) or (isinstance(arg, ast.Name) and arg.id in built)):
                    found.append(f"{fn.name}:{call.lineno} urlopen")
    return found


def test_every_request_the_guard_makes_carries_its_name():
    assert _requests_not_built_by_the_helper(GUARD.read_text(encoding="utf-8")) == []


def test_negative_control_a_bare_url_or_request_is_caught():
    planted = (
        "import urllib.request\n"
        "def ping(url):\n"
        "    with urllib.request.urlopen(url, timeout=5) as r:\n"
        "        pass\n"
        "def post(url):\n"
        "    req = urllib.request.Request(url, method='POST')\n"
        "    urllib.request.urlopen(req)\n"
    )
    assert _requests_not_built_by_the_helper(planted) == ["ping:3 urlopen", "post:6 Request", "post:7 urlopen"]


def test_the_name_goes_out_on_a_real_request():
    """Through urllib itself, to a server on this machine: the probe's
    request carries the guard's name."""
    seen: list[str] = []

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - the stdlib's name
            seen.append(self.headers.get("User-Agent", ""))
            raw = b'{"status":"ok"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *a):
            pass

    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        guard.probe(f"http://127.0.0.1:{server.server_port}/health/ready", 5.0)
    finally:
        server.shutdown()
        server.server_close()
    assert seen == [guard.GUARD_USER_AGENT]


def test_a_refusal_and_an_outage_are_told_apart(monkeypatch, tmp_path):
    err = urllib.error.HTTPError("https://x", 401, "no", {}, None)
    h = _Harness(monkeypatch, tmp_path, opener=_Opener(error=err))
    h.tick()
    h.tick()
    assert h.watch.state == "rejected"
    assert "outside_watch.rejected" in h.events()

    h.opener.error = urllib.error.URLError("down")
    h.now += 60
    h.tick()
    h.tick()
    assert h.watch.state == "unreachable"


def test_a_hung_observer_never_holds_up_the_guard(monkeypatch, tmp_path):
    hold = threading.Event()
    h = _Harness(monkeypatch, tmp_path, opener=_Opener(hold=hold))
    began = time.perf_counter()
    for _ in range(185):
        h.tick()
    assert time.perf_counter() - began < 3.0
    assert len(h.opener.requests) == 1   # one send in flight, the rest skipped
    assert h.watch.skipped == 3
    hold.set()
    h.watch._thread.join(5)
    h.tick()
    assert h.watch.state == "reached"


# -- the allowlist -----------------------------------------------------------------

CANARY = "CANARY-chat-text-7f3a"


def _planted_snapshot() -> dict:
    return {
        "install": "a1b2c3d4e5f6a7b8",
        "sent_at": 1700000000,
        "seq": 3,
        "detail": f"{CANARY} from a probe",
        "guard": {"state": "supervising", "uptime_s": 12.0, "code": "abcdef0123456789",
                  "path": "C:/Users/someone/kazma"},
        "kazma": {"state": "not_ready", "ready": False,
                  "why": ["database", f"{CANARY}: password for user x", "C:/secret/path"],
                  "uptime_s": 5, "build": "0123456789ab", "detail": CANARY},
        "maintenance": False,
        "database": {"state": "away", "confirmed": True, "error": CANARY},
        "docker": "up; rm -rf /",
        "cloudflared": "running",
        "disk": {"free_pct": 14.0, "level": "low", "path": "C:/Users/someone"},
        "power": {"on_battery": True, "percent": 81, "name": CANARY},
    }


def test_the_heartbeat_carries_only_the_allowlist():
    body = guard.heartbeat_body(_planted_snapshot())
    text = body.decode("utf-8")
    assert CANARY not in text
    assert "someone" not in text and "secret" not in text and "rm -rf" not in text
    data = json.loads(text)
    assert set(data) == {"v", "install", "sent_at", "seq", "guard", "kazma", "maintenance",
                         "database", "docker", "cloudflared", "disk", "power", "pages"}
    assert data["pages"] == []
    assert data["kazma"]["why"] == ["database"]
    assert data["kazma"]["build"] == "0123456789ab"
    assert data["docker"] is None
    assert data["database"] == {"state": "away", "confirmed": True}
    assert data["disk"] == {"free_pct": 14.0, "level": "low"}
    assert data["power"] == {"on_battery": True, "percent": 81.0}
    assert data["guard"]["code"] == "abcdef012345"
    # Control: the snapshot sent as it is would carry all of it.
    assert CANARY in json.dumps(_planted_snapshot())


def test_the_page_receipts_are_cut_as_the_observer_cuts_them(monkeypatch):
    """Unified alerts, Stage 2: the heartbeat carries the open inside pages
    -- a catalog key, a time, delivered -- and nothing else. The observer's
    sanitizePages cuts the same input to the same output (its Node tests
    read this vector too)."""
    vector = VECTOR["pages"]
    snap = {**_planted_snapshot(), "pages": vector["in"]}
    body = json.loads(guard.heartbeat_body(snap))
    assert body["pages"] == vector["out"]
    text = json.dumps(body)
    assert CANARY not in text and "someone" not in text and "deepseek" not in text
    # Control: the receipts as they came in carry a title, a path and a name.
    raw = json.dumps(vector["in"])
    assert "CANARY" in raw and "someone" in raw and "deepseek" in raw
    # A key is checked against the catalog, never trusted: with no readable
    # copy nothing leaves.
    monkeypatch.setattr(guard, "_alert_table_cache", {})
    assert json.loads(guard.heartbeat_body(snap))["pages"] == []


def test_the_snapshot_carries_the_guards_and_its_servers_receipts(monkeypatch, tmp_path):
    h = _Harness(monkeypatch, tmp_path)
    g = h.g
    pages_file = tmp_path / "kazma_pages.json"
    monkeypatch.setenv("KAZMA_GUARD_PAGES_FILE", str(pages_file))
    g.proc = _Proc()
    g.spawned_at = guard.time.time() - 60
    g.receipts.paged("disk.critical", True, "card")
    pages_file.write_text(json.dumps({
        "v": 1, "process_started": g.spawned_at + 2, "pages": [
            {"key": "database.away", "paged_at": int(guard.time.time()), "delivered": True}]}),
        encoding="utf-8")
    keys = [p["key"] for p in json.loads(guard.heartbeat_body(g._heartbeat_snapshot(1)))["pages"]]
    assert keys == ["database.away", "disk.critical"]
    # Control: a file an earlier server wrote says nothing about this one.
    g.spawned_at = guard.time.time() + 10
    keys = [p["key"] for p in json.loads(guard.heartbeat_body(g._heartbeat_snapshot(2)))["pages"]]
    assert keys == ["disk.critical"]


def test_a_build_that_is_not_a_commit_is_dropped():
    snap = _planted_snapshot()
    for bad in ("../../etc", "abcdefabcdefabcdef", "ABCDEF012345", 123456789012):
        snap["kazma"]["build"] = bad
        assert json.loads(guard.heartbeat_body(snap))["kazma"]["build"] is None


# -- the snapshot -----------------------------------------------------------------


class _Proc:
    def __init__(self, alive: bool = True) -> None:
        self.alive = alive
        self.pid = 4242

    def poll(self):
        return None if self.alive else 1


def test_the_snapshot_reads_the_guards_own_view(monkeypatch, tmp_path):
    h = _Harness(monkeypatch, tmp_path)
    g = h.g
    assert g._heartbeat_snapshot(1)["kazma"]["state"] == "down"

    g.proc = _Proc()
    g.spawned_at = guard.time.time()
    assert g._heartbeat_snapshot(2)["kazma"]["state"] == "starting"

    h.now += 5
    g._note_probe(guard.ProbeResult(
        False, "database: password authentication failed for user kazma",
        database={"state": "away", "confirmed": True}, checks=("database",)))
    snap = g._heartbeat_snapshot(3)
    assert snap["kazma"]["state"] == "not_ready"
    assert snap["kazma"]["why"] == ["database"]
    assert snap["database"]["state"] == "away"
    assert "password" not in guard.heartbeat_body(snap).decode()

    g._note_probe(guard.ProbeResult(False, "unreachable: refused", answered=False))
    assert g._heartbeat_snapshot(4)["kazma"]["state"] == "unreachable"
    g._note_probe(guard.ProbeResult(True, "ready"))
    snap = g._heartbeat_snapshot(5)
    assert snap["kazma"]["state"] == "ready" and snap["kazma"]["ready"] is True

    # An answer from the child before this one is not this child's.
    g.spawned_at = guard.time.time() + 1
    assert g._heartbeat_snapshot(6)["kazma"]["state"] == "starting"

    g.proc = _Proc(alive=False)
    assert g._heartbeat_snapshot(7)["kazma"]["state"] == "down"


def test_the_guard_loop_ticks_the_watch(monkeypatch, tmp_path):
    h = _Harness(monkeypatch, tmp_path)
    for _ in range(3):
        h.g._broker_tick()
        h.watch._thread.join(5)
    assert len(h.opener.requests) == 1
    assert "outside_watch" in guard.GUARD_FEATURES
    # Control: a guard without the watch (the one before B1) sends nothing.
    h.g.outside = None
    h.now += 120
    h.g._broker_tick()
    assert len(h.opener.requests) == 1


def test_status_says_the_state():
    lines = guard._outside_status_lines({"outside_watch": {"state": "reached",
                                                           "last_ack": time.time() - 30}})
    assert lines and lines[0].startswith("outside     : reached (last answered")
    assert guard._outside_status_lines({}) == []


# -- the probe names its checks ---------------------------------------------------------


class _Ready(BaseHTTPRequestHandler):
    reply: tuple[int, dict] = (200, {})

    def do_GET(self):
        status, body = self.reply
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):
        return


def _serve(reply):
    handler = type("H", (_Ready,), {"reply": reply})
    server = HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/health/ready"


def test_the_probe_names_failing_checks_and_still_unpacks():
    server, url = _serve((503, {"status": "not_ready", "checks": {
        "database": {"status": "failed", "error": "password for user kazma"},
        "config_store": {"status": "ok"},
        "Bad Name; drop": {"status": "failed"}}}))
    try:
        result = guard.probe(url, 5.0)
    finally:
        server.shutdown()
    ok, detail = result
    assert ok is False and "password" in detail   # the log keeps the text
    assert result.checks == ("database",)         # the heartbeat gets the name

    server, url = _serve((200, {"status": "degraded", "checks": {
        "mcp": {"status": "failed"}, "database": {"status": "ok"}}}))
    try:
        result = guard.probe(url, 5.0)
    finally:
        server.shutdown()
    assert result[0] is True and result.checks == ("mcp",)
    assert guard.ProbeResult(True, "x").checks == ()


def test_the_shared_vector_is_hmac_sha256():
    # The fixture's own arithmetic, so the observer's verifier reads a true vector.
    mac = hmac.new(VECTOR["key"].encode(), f"{VECTOR['timestamp']}.".encode()
                   + VECTOR["body"].encode(), hashlib.sha256).hexdigest()
    assert VECTOR["signature"] == "v1=" + mac


def test_the_pairing_file_names_the_install_and_the_observer(monkeypatch, tmp_path):
    # kazma observer pair writes JSON: the id the observer knows this install
    # by, the key and the observer's address. The guard sends under that id,
    # to that address.
    h = _Harness(monkeypatch, tmp_path, key=json.dumps({
        "v": 1, "install": "0123456789abcdef", "key": KEY, "url": "https://watch.example"}))
    h.tick()
    req = h.opener.requests[0]
    assert req.full_url == "https://watch.example/v1/heartbeat"
    assert dict(req.header_items())["X-kazma-install"] == "0123456789abcdef"
    assert json.loads(req.data)["install"] == "0123456789abcdef"
    # The operator's address in the guard's environment still wins.
    monkeypatch.setenv("KAZMA_GUARD_OUTSIDE_WATCH_URL", "https://other.example/v1/heartbeat")
    assert guard._outside_watch_url("https://watch.example") == "https://other.example/v1/heartbeat"


def test_a_pairing_file_that_is_not_json_or_has_no_key_is_refused(monkeypatch, tmp_path):
    h = _Harness(monkeypatch, tmp_path, key='{"install": "0123456789abcdef"')
    h.tick()
    assert h.opener.requests == [] and h.watch.state == "key_invalid"
    other = tmp_path / "other"
    other.mkdir()
    h2 = _Harness(monkeypatch, other, key=json.dumps({"install": "0123456789abcdef", "key": "short"}))
    h2.tick()
    assert h2.opener.requests == [] and h2.watch.state == "key_invalid"
