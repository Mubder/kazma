"""The outside watch, Kazma's side (resilience plan B1).

``kazma observer pair`` registers the install with the observer and writes the
pairing file the guard reads; Settings -> System and the daily digest read
back what the observer saw, with a request signed by the key. Held here:

* pairing writes the file only after the observer accepted, restricted to
  this account before the key is in it; a refusal or a file that cannot be
  restricted leaves no key behind;
* the guard reads exactly what pairing writes (the install id and address);
* the summary request is signed the way the observer checks it, and the key
  never appears in what Settings is given;
* the digest line says the week's uptime and incidents, and nothing when the
  install is not paired.

The observer is a local HTTP server; nothing leaves the machine. Each rule
has a negative control.
"""

from __future__ import annotations

import hashlib
import hmac
import importlib.util
import json
import shutil
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from kazma_core.observability import daily_digest, outside_watch

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "t" * 40
VECTOR = json.loads((ROOT / "tests" / "fixtures" / "observer" / "heartbeat_vector.json")
                    .read_text(encoding="utf-8"))


def _v2(key: str, ts: str | int, method: str, path: str, body: bytes) -> str:
    """The Worker's v2 signature, written from verify.js's v2Message."""
    message = f"v2\n{ts}\n{method}\n{path}\n".encode("ascii") + body
    return "v2=" + hmac.new(key.encode(), message, hashlib.sha256).hexdigest()


def _v1(key: str, ts: int, body: bytes) -> str:
    """The old v1 form, which bound no route."""
    return "v1=" + hmac.new(key.encode(), f"{ts}.".encode("ascii") + body, hashlib.sha256).hexdigest()


class _Observer(BaseHTTPRequestHandler):
    """A stand-in observer: /v1/pair, /v1/unpair, /v1/summary."""

    paired: dict[str, str] = {}
    refuse = False
    summary: dict = {}
    seen: list[dict] = []

    def _reply(self, status: int, body: dict) -> None:
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        body = json.loads(raw or b"{}")
        self.seen.append({"path": self.path, "auth": self.headers.get("Authorization"), "body": body})
        if self.refuse or self.headers.get("Authorization") != f"Bearer {TOKEN}":
            return self._reply(401, {"error": "bad_pairing_token"})
        if self.path == "/v1/pair":
            self.paired[body["install"]] = body["key"]
            return self._reply(200, {"ok": True, "paired": body["install"]})
        if self.path == "/v1/unpair":
            self.paired.pop(body["install"], None)
            return self._reply(200, {"ok": True, "unpaired": body["install"]})
        return self._reply(404, {"error": "not_found"})

    def do_GET(self):
        install = self.headers.get("X-Kazma-Install", "")
        ts = self.headers.get("X-Kazma-Timestamp", "")
        sig = self.headers.get("X-Kazma-Signature", "")
        self.seen.append({"path": self.path, "install": install})
        key = self.paired.get(install)
        # The Worker's v2 check (verify.js verifyRequest): method, path, body.
        want = _v2(key, ts, "GET", self.path, b"") if key else None
        if self.path != "/v1/summary" or not key or not hmac.compare_digest(want, sig):
            return self._reply(401, {"error": "bad_signature"})
        return self._reply(200, {"install": install, **self.summary})

    def log_message(self, *args):
        return


@pytest.fixture(autouse=True)
def _own_home(tmp_path, monkeypatch):
    """Each test its own <install>/.kazma: the session's is shared."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("KAZMA_USER_HOME", str(home))
    monkeypatch.delenv("KAZMA_GUARD_STATE_FILE", raising=False)


@pytest.fixture
def observer():
    handler = type("H", (_Observer,), {"paired": {}, "refuse": False, "summary": {}, "seen": []})
    server = HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    handler.url = f"http://127.0.0.1:{server.server_address[1]}"
    outside_watch._forget_cached_summary()
    yield handler
    server.shutdown()


@pytest.fixture
def restricted(monkeypatch):
    calls: list[Path] = []

    def fake(path: Path) -> bool:
        calls.append(Path(path))
        return fake.ok

    fake.ok = True
    monkeypatch.setattr(outside_watch, "_restrict_to_owner", fake)
    return fake, calls


# -- pairing ------------------------------------------------------------------------


def test_pairing_registers_and_writes_the_file_the_guard_reads(observer, restricted):
    _fake, calls = restricted
    done = outside_watch.pair_with_observer(observer.url, TOKEN)
    path = outside_watch._pairing_path()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["install"] == done["install"] == outside_watch._install_hash()
    assert observer.paired == {data["install"]: data["key"]}
    assert len(data["key"]) >= 32 and data["url"] == observer.url
    # Restricted while still empty (the .new file), then again in place.
    assert [c.name for c in calls] == ["observer.key.new", "observer.key"]
    assert outside_watch._read_pairing() == {"install": data["install"], "key": data["key"],
                                            "url": observer.url}


def test_the_guard_reads_what_pairing_writes(observer, restricted, monkeypatch):
    outside_watch.pair_with_observer(observer.url, TOKEN)
    spec = importlib.util.spec_from_file_location(
        "_pair_guard", ROOT / "scripts" / "service" / "kazma_guard.py")
    guard = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = guard
    spec.loader.exec_module(guard)
    monkeypatch.delenv("KAZMA_GUARD_OUTSIDE_WATCH_URL", raising=False)

    class _G:
        log = staticmethod(lambda *a, **k: None)

    watch = guard.OutsideWatch(_G(), key_path=outside_watch._pairing_path(), restrict=lambda p: True)
    key = watch._load_key()
    pairing = outside_watch._read_pairing()
    assert key == pairing["key"].encode()
    assert watch._paired_install == pairing["install"]
    assert guard._outside_watch_url(watch._paired_url) == f"{observer.url}/v1/heartbeat"


def test_a_refused_pairing_leaves_no_key(observer, restricted):
    observer.refuse = True
    with pytest.raises(outside_watch.PairingError, match="HTTP 401"):
        outside_watch.pair_with_observer(observer.url, TOKEN)
    assert not outside_watch._pairing_path().exists()


def test_a_file_that_cannot_be_restricted_never_holds_the_key(observer, restricted):
    fake, _calls = restricted
    fake.ok = False
    with pytest.raises(outside_watch.PairingError, match="restrict"):
        outside_watch.pair_with_observer(observer.url, TOKEN)
    assert not outside_watch._pairing_path().exists()
    assert not outside_watch._pairing_path().with_name("observer.key.new").exists()


def test_a_short_token_or_a_plain_http_observer_is_refused_before_any_request(observer, restricted):
    with pytest.raises(outside_watch.PairingError, match="token"):
        outside_watch.pair_with_observer(observer.url, "short")
    with pytest.raises(outside_watch.PairingError, match="https"):
        outside_watch.pair_with_observer("http://observer.example", TOKEN)
    assert observer.seen == []


def test_unpairing_removes_the_file(observer, restricted):
    outside_watch.pair_with_observer(observer.url, TOKEN)
    done = outside_watch.unpair_from_observer(TOKEN, forget=True)
    assert done["forgotten"] is True
    assert observer.paired == {}
    assert observer.seen[-1]["body"]["forget"] is True
    assert outside_watch._read_pairing() is None


@pytest.mark.parametrize("content", [
    "plain-key-without-json-0123456789abcdef",
    json.dumps({"install": "0123456789abcdef", "key": "short"}),
    json.dumps({"install": "not-hex", "key": "k" * 40}),
    json.dumps({"install": "0123456789abcdef", "key": "k" * 40, "url": "http://observer.example"}),
])
def test_a_pairing_file_the_guard_would_refuse_reads_as_not_paired(content):
    path = outside_watch._pairing_path()
    path.write_text(content, encoding="utf-8")
    assert outside_watch._read_pairing() is None


# -- the signature binds the route (v2) ---------------------------------------------


def test_kazmas_signer_matches_the_shared_v2_vector():
    """Kazma's ``_sign`` and the Worker's ``signV2`` (the node suite checks
    the same vector) sign every route to the same bytes."""
    v2 = VECTOR["v2"]
    for route in v2["routes"]:
        url = f"https://status.kazma.example{route['path']}"
        got = outside_watch._sign(VECTOR["key"], VECTOR["timestamp"], route["method"], url,
                                  route["body"].encode())
        assert got == route["signature"], route
    assert len({r["signature"] for r in v2["routes"]}) == len(v2["routes"])


def _worker_accepts(key: str, ts: int, method: str, path: str, body: bytes, signature: str, *,
                    v1_only: bool = False) -> bool:
    """The Worker's check, as verify.js makes it; ``v1_only`` is the old one."""
    if v1_only:
        return hmac.compare_digest(_v1(key, ts, body), signature)
    return hmac.compare_digest(_v2(key, ts, method, path, body), signature)


def test_a_signature_for_one_route_is_refused_on_another():
    key, ts, body = VECTOR["key"], VECTOR["timestamp"], b'{"v":1,"install":"a1b2c3d4e5f6a7b8"}'
    settings = outside_watch._sign(key, ts, "POST", "https://o.example/v1/settings", body)
    assert settings.startswith("v2=")
    assert _worker_accepts(key, ts, "POST", "/v1/settings", body, settings)
    assert not _worker_accepts(key, ts, "POST", "/v1/leave", body, settings)
    assert not _worker_accepts(key, ts, "GET", "/v1/settings", body, settings)
    # Negative control: the old v1-only check takes a settings request's
    # signature on /v1/leave -- the route is not in what it signed.
    old = _v1(key, ts, body)
    assert _worker_accepts(key, ts, "POST", "/v1/settings", body, old, v1_only=True)
    assert _worker_accepts(key, ts, "POST", "/v1/leave", body, old, v1_only=True)


def test_every_signed_request_kazma_sends_is_v2(observer, restricted, monkeypatch):
    """The settings, test-page and leave requests carry a v2 signature over
    their own route (and the summary over its own, checked by the stand-in)."""
    outside_watch.pair_with_observer(observer.url, TOKEN)
    pairing = outside_watch._read_pairing()
    sent: list[tuple[str, str, bytes, dict]] = []

    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def post(self, url, content=b"", headers=None):
            sent.append(("POST", url, content, dict(headers or {})))
            import httpx

            return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(outside_watch.httpx, "Client", _Client)
    for route in ("/v1/settings", "/v1/test-page", "/v1/leave"):
        outside_watch._signed_post(pairing, route, {"x": 1})
    assert [s[1].removeprefix(observer.url) for s in sent] == ["/v1/settings", "/v1/test-page", "/v1/leave"]
    for method, url, raw, headers in sent:
        path = url.removeprefix(observer.url)
        ts = headers["X-Kazma-Timestamp"]
        assert headers["X-Kazma-Signature"] == _v2(pairing["key"], ts, method, path, raw)


# -- reading back ---------------------------------------------------------------------


def test_the_summary_is_signed_and_the_key_never_reaches_settings(observer, restricted, monkeypatch, tmp_path):
    outside_watch.pair_with_observer(observer.url, TOKEN)
    observer.summary = {"condition": "up", "detail": None, "heartbeat_age_s": 20,
                        "up_7d_pct": 99.9, "incidents_7d": []}
    state = tmp_path / "guard.state.json"
    state.write_text(json.dumps({"outside_watch": {"state": "reached", "last_ack": time.time() - 30,
                                                   "mode": "auto"}}), encoding="utf-8")
    monkeypatch.setenv("KAZMA_GUARD_STATE_FILE", str(state))
    view = outside_watch.outside_status()
    assert view["paired"] is True
    assert view["observer"]["ok"] is True and view["observer"]["up_7d_pct"] == 99.9
    assert view["guard"]["state"] == "reached"
    key = outside_watch._read_pairing()["key"]
    assert key not in json.dumps(view)
    # Control: a request signed with another key is refused by the observer.
    observer.paired[view["install"]] = "x" * 43
    outside_watch._forget_cached_summary()
    assert outside_watch._observer_summary()["ok"] is False


def test_status_from_a_shell_reads_the_guards_own_state_file(monkeypatch, tmp_path):
    """Run from the owner's shell, nothing hands over KAZMA_GUARD_STATE_FILE:
    the guard's file is read where the guard keeps it (live 2026-11-06 the
    status said "Kazma was not started by the guard" over a running guard)."""
    home = tmp_path / "home"  # the autouse fixture's KAZMA_USER_HOME
    (home / "guard.state.json").write_text(json.dumps({"outside_watch": {"state": "rejected"}}),
                                           encoding="utf-8")
    assert outside_watch._guard_view()["state"] == "rejected"
    # Control: a handed path wins (the server's own view).
    handed = tmp_path / "handed.json"
    handed.write_text(json.dumps({"outside_watch": {"state": "reached"}}), encoding="utf-8")
    monkeypatch.setenv("KAZMA_GUARD_STATE_FILE", str(handed))
    assert outside_watch._guard_view()["state"] == "reached"


def test_the_summary_is_kept_a_minute_unless_asked_fresh(observer, restricted):
    outside_watch.pair_with_observer(observer.url, TOKEN)
    observer.summary = {"condition": "up", "up_7d_pct": 100, "incidents_7d": []}
    outside_watch._observer_summary()
    outside_watch._observer_summary()
    assert sum(1 for s in observer.seen if s["path"] == "/v1/summary") == 1
    outside_watch._observer_summary(fresh=True)
    assert sum(1 for s in observer.seen if s["path"] == "/v1/summary") == 2


def test_not_paired(observer):
    view = outside_watch.outside_status()
    assert view["paired"] is False and view["observer"] is None
    assert outside_watch.outside_digest_line() is None
    assert observer.seen == []


# -- the digest line -------------------------------------------------------------------


def test_the_digest_line_says_the_week(observer, restricted):
    outside_watch.pair_with_observer(observer.url, TOKEN)
    now = time.time()
    observer.summary = {"condition": "up", "up_7d_pct": 99.9, "incidents_7d": [
        {"cause": "machine_unreachable", "started_at": now - 3 * 3600, "ended_at": now - 3 * 3600 + 14 * 60},
        {"cause": "tunnel_down", "started_at": now - 600, "ended_at": None},
    ]}
    line = outside_watch.outside_digest_line(now)
    assert line == ("Outside watch: up 99.9 % this week, 2 incidents "
                    "(machine not reachable, 14 min; tunnel down, 10 min, ongoing)")
    observer.summary = {"condition": "up", "up_7d_pct": 100.0, "incidents_7d": []}
    assert outside_watch.outside_digest_line(now) == "Outside watch: up 100.0 % this week, no incidents"


def test_an_observer_that_does_not_answer_is_said(restricted, observer):
    outside_watch.pair_with_observer(observer.url, TOKEN)
    observer.paired.clear()   # the observer no longer knows the install
    assert outside_watch.outside_digest_line() == "Outside watch: the observer did not answer (refused)"


def test_the_daily_digest_carries_the_line(monkeypatch):
    monkeypatch.setattr(outside_watch, "outside_digest_line",
                        lambda now=None: "Outside watch: up 99.9 % this week, no incidents")
    assert "Outside watch: up 99.9 % this week, no incidents" in daily_digest.build_digest(24)
    # Control: not paired, no line.
    monkeypatch.setattr(outside_watch, "outside_digest_line", lambda now=None: None)
    assert "Outside watch" not in daily_digest.build_digest(24)


# -- the observer Worker's own tests ----------------------------------------------------------


def _node_with_sqlite() -> str | None:
    node = shutil.which("node")
    if not node:
        return None
    probe = subprocess.run([node, "-e", "require('node:sqlite')"], capture_output=True, timeout=60,
                           check=False)
    return node if probe.returncode == 0 else None


def test_the_observer_worker_tests_pass():
    """deploy/observer's own tests (node:test; Node 22.5+ for node:sqlite)."""
    node = _node_with_sqlite()
    if node is None:
        pytest.skip("needs Node 22.5+ (node:sqlite) for the observer Worker's tests")
    # Every test file the Worker has (*.test.mjs), not a list kept by hand.
    files = sorted(p.name for p in (ROOT / "deploy" / "observer" / "test").glob("*.test.mjs"))
    assert "observer.test.mjs" in files and "one_page.test.mjs" in files
    assert "watch_settings.test.mjs" in files
    result = subprocess.run([node, "--test", *(f"test/{name}" for name in files)],
                            cwd=ROOT / "deploy" / "observer", capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=300, check=False)
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-2000:]
