"""Settings -> Alerts, the server's side (the unified alerts plan, Stage 3).

The tab's routes (``kazma_ui/settings_alerts.py``) and what they drive:

* ``GET /api/settings/alerts`` -- routes, status messages, notices, the
  guard's pager, Kazma Watch; never a token or a key;
* ``PUT /api/settings/alerts`` -- every field through ``settings_validation``;
  one bad field and nothing is written; same-origin only;
* Kazma Watch from the page: pairing with the token used once (never kept,
  logged or answered), its test page, what it is told (the time zone from
  ``cron.timezone``, the email recipient), and unpairing with a request
  signed by the install's own key;
* Notices: with "only the bell and the digest", an info or success card
  stays off the chat apps and is in the bell; a problem and a recovery note
  still go;
* Recent alerts: Kazma's from the log with each one's receipt, the guard's
  from its log, Kazma Watch's from its summary.

The observer is a local HTTP server checking signatures the way the Worker
does (``deploy/observer/src/verify.js``). Each rule has a negative control.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import threading
import time
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from kazma_core.observability import ops_alerts, outside_watch, recent_alerts

TOKEN = "t" * 40
SECRET = "SENTINEL-telegram-bot-token-1234"


# -- a stand-in Kazma Watch -------------------------------------------------------------


class _Watch(BaseHTTPRequestHandler):
    keys: dict[str, str] = {}
    settings: dict[str, dict] = {}
    seen: list[dict] = []
    channels = {"telegram": "sent", "email": "off"}
    reasons: dict[str, str] = {}

    def _reply(self, status: int, body: dict) -> None:
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _signed(self, raw: bytes) -> tuple[str | None, str]:
        """``(install, "")`` when the request is signed by its key, else
        ``(None, error)`` in the Worker's words (verify.js, watch_settings.js)."""
        install = self.headers.get("X-Kazma-Install", "")
        ts = self.headers.get("X-Kazma-Timestamp", "")
        key = self.keys.get(install)
        if not key:
            return None, "unknown_install"
        want = "v1=" + hmac.new(key.encode(), ts.encode() + b"." + raw, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(want, self.headers.get("X-Kazma-Signature", "")):
            return None, "bad_signature"
        return install, ""

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.seen.append({"path": self.path, "auth": self.headers.get("Authorization"), "raw": raw})
        if self.path == "/v1/pair":
            if self.headers.get("Authorization") != f"Bearer {TOKEN}":
                return self._reply(401, {"error": "bad_pairing_token"})
            body = json.loads(raw)
            self.keys[body["install"]] = body["key"]
            return self._reply(200, {"ok": True, "paired": body["install"]})
        install, error = self._signed(raw)
        if install is None:
            return self._reply(401, {"error": error})
        body = json.loads(raw)
        if self.path == "/v1/settings":
            self.settings[install] = {"timezone": body.get("timezone"), "email_to": body.get("email_to")}
            return self._reply(200, {"ok": True, "settings": self.settings[install]})
        if self.path == "/v1/test-page":
            return self._reply(200, {"ok": True, "channels": dict(self.channels), "reasons": dict(self.reasons)})
        if self.path == "/v1/leave":
            self.keys.pop(install, None)
            return self._reply(200, {"ok": True, "unpaired": install, "forgotten": body.get("forget") is True})
        return self._reply(404, {"error": "not_found"})

    def do_GET(self):
        install = self.headers.get("X-Kazma-Install", "")
        ts = self.headers.get("X-Kazma-Timestamp", "")
        key = self.keys.get(install)
        want = "v1=" + hmac.new(key.encode(), f"{ts}.GET /v1/summary".encode(), hashlib.sha256).hexdigest() \
            if key else ""
        if not key or not hmac.compare_digest(want, self.headers.get("X-Kazma-Signature", "")):
            return self._reply(401, {"error": "bad_signature"})
        now = time.time()
        return self._reply(200, {
            "install": install, "condition": "up", "detail": None, "up_7d_pct": 99.5,
            "incidents_7d": [{"cause": "tunnel_down", "causes": ["tunnel_down"], "started_at": now - 3600,
                              "ended_at": now - 3000, "paged_at": now - 3500, "recovery_paged_at": None}],
            "settings": self.settings.get(install) or {"timezone": None, "email_to": None},
        })

    def log_message(self, *args):
        return


@pytest.fixture(autouse=True)
def _own_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("KAZMA_USER_HOME", str(home))
    monkeypatch.delenv("KAZMA_GUARD_STATE_FILE", raising=False)
    monkeypatch.setattr(outside_watch, "_restrict_to_owner", lambda path: True)
    outside_watch._forget_cached_summary()


@pytest.fixture
def watch():
    handler = type("W", (_Watch,), {"keys": {}, "settings": {}, "seen": [],
                                    "channels": {"telegram": "sent", "email": "off"}, "reasons": {}})
    server = HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    handler.url = f"http://127.0.0.1:{server.server_address[1]}"
    yield handler
    server.shutdown()


@pytest.fixture
def client(tmp_path):
    from fastapi import FastAPI
    from fastapi.templating import Jinja2Templates
    from fastapi.testclient import TestClient

    from kazma_core.config_store import ConfigStore
    from kazma_ui.settings import create_settings_router

    cs = ConfigStore(db_path=str(tmp_path / "alerts.db"))
    (tmp_path / "templates").mkdir()
    (tmp_path / "templates" / "settings.html").write_text("ok")
    app = FastAPI()
    app.include_router(create_settings_router(
        MagicMock(), cs, Jinja2Templates(directory=str(tmp_path / "templates"))))
    test_client = TestClient(app, headers={"X-Requested-With": "XMLHttpRequest"})
    test_client.store = cs
    return test_client


# -- reading and saving -----------------------------------------------------------------------


def test_the_tab_reads_every_section_and_no_secret(client):
    client.store.set("connectors.telegram.token", SECRET, category="connectors")
    client.store.set("notifications.ops.channels", "telegram,slack", category="notifications")
    client.store.set("guard.telegram.chat_id", "-1001234567890", category="guard")
    res = client.get("/api/settings/alerts")
    assert res.status_code == 200
    data = res.json()
    assert data["routes"]["chosen"] == ["telegram", "slack"]
    assert data["routes"]["names"] == ["telegram", "telegram-group", "discord", "slack"]
    assert data["routes"]["ready"]["telegram"] is True and data["routes"]["ready"]["discord"] is False
    assert data["lifecycle"]["events"] == ["started", "startup_failed"]
    assert data["notices_to_chats"] is True
    assert data["guard"]["chat"] == "-1001234567890" and data["guard"]["bot"] is True
    assert data["guard"]["guard"] == "none"
    assert data["watch"]["paired"] is False
    assert SECRET not in res.text


def test_a_save_writes_each_field_through_its_rule(client):
    res = client.put("/api/settings/alerts", json={
        "routes": ["slack", "telegram"], "lifecycle_events": ["shutting_down", "started"],
        "notices_to_chats": False, "guard_chat": "@kazma_owner"})
    assert res.status_code == 200, res.text
    assert sorted(res.json()["saved"]) == ["guard_chat", "lifecycle_events", "notices_to_chats", "routes"]
    store = client.store
    assert store.get("notifications.ops.channels") == "slack,telegram"
    assert store.get("notifications.lifecycle.events") == ["started", "shutting_down"]
    assert store.get("notifications.ops.notices_to_chats") is False
    assert store.get("guard.telegram.chat_id") == "@kazma_owner"
    assert ops_alerts.notices_to_chats(store) is False
    assert client.get("/api/settings/alerts").json()["notices_to_chats"] is False


@pytest.mark.parametrize(("field", "bad"), [
    ("guard_chat", "my chat"),
    ("guard_chat", "12ab"),
    ("routes", ["telegram", "pager"]),
    ("lifecycle_events", ["restarted"]),
    ("notices_to_chats", "sometimes"),
])
def test_one_bad_field_writes_nothing(client, field, bad):
    client.store.set("notifications.ops.channels", "telegram", category="notifications")
    res = client.put("/api/settings/alerts", json={"routes": ["discord"], field: bad})
    assert res.status_code == 400 and res.json()["code"] == "rejected"
    assert field in res.json()["error"]
    assert client.store.get("notifications.ops.channels") == "telegram", "the valid field was not written either"
    assert client.store.get("guard.telegram.chat_id") is None


def test_writes_are_same_origin_only(client):
    from fastapi.testclient import TestClient

    bare = TestClient(client.app)
    assert bare.put("/api/settings/alerts", json={"notices_to_chats": False}).status_code == 403
    other = client.put("/api/settings/alerts", json={"notices_to_chats": False},
                       headers={"Origin": "https://evil.example"})
    assert other.status_code == 403
    assert client.store.get("notifications.ops.notices_to_chats") is None
    # Control: the page's own request is taken.
    assert client.put("/api/settings/alerts", json={"notices_to_chats": False}).status_code == 200


# -- Kazma Watch from the page --------------------------------------------------------------------


def test_pairing_from_the_page_uses_the_token_once_and_tells_the_zone(client, watch, caplog):
    client.store.set("cron.timezone", "Asia/Kuwait")
    with caplog.at_level(logging.DEBUG):
        res = client.post("/api/settings/alerts/watch/pair", json={"token": TOKEN, "url": watch.url})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] and body["told"]["ok"]
    assert watch.settings[body["install"]] == {"timezone": "Asia/Kuwait", "email_to": None}
    # The token went to /v1/pair only, and nowhere here keeps it.
    assert [s["path"] for s in watch.seen if s["auth"]] == ["/v1/pair"]
    assert TOKEN not in res.text and TOKEN not in caplog.text
    assert TOKEN not in json.dumps(client.store.get_all(), default=str)
    assert TOKEN not in (outside_watch._pairing_path().read_text(encoding="utf-8"))
    state = client.get("/api/settings/alerts").json()["watch"]
    assert state["paired"] and state["observer"]["ok"]
    assert state["observer"]["settings"]["timezone"] == "Asia/Kuwait"


def test_a_refused_pairing_says_why_and_keeps_nothing(client, watch):
    res = client.post("/api/settings/alerts/watch/pair", json={"token": "w" * 40, "url": watch.url})
    assert res.status_code == 400 and res.json()["code"] == "bad_token"
    assert not outside_watch._pairing_path().exists()
    res = client.post("/api/settings/alerts/watch/pair", json={"token": TOKEN, "url": "http://example.com"})
    assert res.json()["code"] == "bad_url"


def test_the_test_page_and_what_kazma_watch_is_told(client, watch):
    assert client.post("/api/settings/alerts/watch/pair", json={"token": TOKEN, "url": watch.url}).status_code == 200
    res = client.post("/api/settings/alerts/watch/test")
    assert res.json() == {"ok": True, "channels": {"telegram": "sent", "email": "off"}, "reasons": {}}
    watch.channels = {"telegram": "failed", "email": "off"}
    assert client.post("/api/settings/alerts/watch/test").json()["ok"] is False
    # A refused email says Cloudflare's reason; anything that is not one of
    # Email Service's codes, or a reason for a channel that did not fail, is dropped.
    watch.channels = {"telegram": "sent", "email": "failed"}
    watch.reasons = {"email": "E_SENDER_NOT_VERIFIED"}
    assert client.post("/api/settings/alerts/watch/test").json()["reasons"] == {"email": "E_SENDER_NOT_VERIFIED"}
    watch.reasons = {"email": "<img src=x onerror=alert(1)>", "telegram": "E_X"}
    assert client.post("/api/settings/alerts/watch/test").json()["reasons"] == {}
    watch.channels, watch.reasons = {"telegram": "sent", "email": "sent"}, {"email": "E_SEND_FAILED"}
    assert client.post("/api/settings/alerts/watch/test").json()["reasons"] == {}
    # The email recipient: refused when it is not one, kept and told when it is.
    bad = client.put("/api/settings/alerts/watch/settings", json={"email_to": "not an address"})
    assert bad.status_code == 400 and client.store.get("notifications.watch.email_to") is None
    good = client.put("/api/settings/alerts/watch/settings", json={"email_to": "owner@example.com"})
    assert good.status_code == 200 and good.json()["told"]["ok"]
    assert client.store.get("notifications.watch.email_to") == "owner@example.com"
    install = next(iter(watch.keys))
    assert watch.settings[install]["email_to"] == "owner@example.com"
    # Every request after pairing was signed, never the token.
    assert all(s["auth"] is None for s in watch.seen if s["path"] != "/v1/pair")


def test_unpairing_is_signed_by_the_install_and_needs_the_observer(client, watch):
    assert client.post("/api/settings/alerts/watch/pair", json={"token": TOKEN, "url": watch.url}).status_code == 200
    install = next(iter(watch.keys))
    # Control: an observer that does not agree keeps the pairing here.
    real_key = watch.keys[install]
    watch.keys[install] = "other-key-" + "x" * 40
    res = client.post("/api/settings/alerts/watch/unpair", json={"forget": True})
    assert res.status_code == 502 and outside_watch._pairing_path().exists()
    watch.keys[install] = real_key
    res = client.post("/api/settings/alerts/watch/unpair", json={"forget": True})
    assert res.status_code == 200 and res.json()["forgotten"] is True
    assert not outside_watch._pairing_path().exists() and install not in watch.keys
    assert client.get("/api/settings/alerts").json()["watch"]["paired"] is False


def test_an_install_the_observer_forgot_unpairs_here_too(client, watch):
    """Unpaired from the command line: Kazma Watch no longer knows the install
    (unknown_install), so the file here is all that is left of the pairing."""
    assert client.post("/api/settings/alerts/watch/pair", json={"token": TOKEN, "url": watch.url}).status_code == 200
    watch.keys.clear()
    res = client.post("/api/settings/alerts/watch/unpair")
    assert res.status_code == 200 and not outside_watch._pairing_path().exists()


# -- notices ----------------------------------------------------------------------------------


@pytest.fixture
def delivered(monkeypatch):
    """The real alert() with the chat delivery replaced by a recorder."""
    from kazma_core.config_store import get_config_store
    from kazma_core.observability.alerts import AlertDispatcher

    monkeypatch.delenv("KAZMA_OPS_ALERTS", raising=False)
    ops_alerts.reset_alert_state()
    AlertDispatcher.reset_state()
    sent: list[str] = []

    async def fake_deliver(text, outcome=None):
        sent.append(text)
        return True

    monkeypatch.setattr(ops_alerts, "_deliver", fake_deliver)
    monkeypatch.setattr(ops_alerts, "_has_any_sink", lambda: True)
    monkeypatch.setattr(ops_alerts, "_server_loop", None)
    store = get_config_store()
    yield sent, store
    store.delete("notifications.ops.notices_to_chats")
    ops_alerts.drain_alerts()
    ops_alerts.reset_alert_state()
    AlertDispatcher.reset_state()


def _recovery_and_notice_keys() -> tuple[str, str]:
    from kazma_core.observability.alert_catalog import CATALOG, recovery_keys

    recovery = next(e.key for e in CATALOG if e.key in recovery_keys() and e.sender == "ops"
                    and "{" not in e.key and e.severity in ("info", "success"))
    return recovery, "test.notice_only"


def test_notices_kept_off_the_chats_go_to_the_bell(delivered, caplog):
    from kazma_core.observability.alerts import AlertDispatcher

    sent, store = delivered
    store.set("notifications.ops.notices_to_chats", False, category="notifications")
    recovery, notice = _recovery_and_notice_keys()
    receipts: list = []
    with caplog.at_level(logging.INFO, logger="kazma_core.observability.ops_alerts"):
        ops_alerts.alert(notice, "Crawl of the docs finished", "12 pages", severity="success",
                         on_delivery=receipts.append)
        ops_alerts.alert("test.problem", "Backups are failing", severity="error")
        ops_alerts.alert(recovery, "It is back", severity="success")
        ops_alerts.drain_alerts()
    assert not any("Crawl of the docs" in t for t in sent), "a notice stayed off the chats"
    assert any("Backups are failing" in t for t in sent), "a problem still goes"
    assert any("It is back" in t for t in sent), "a recovery note still goes"
    bell = [a for a in AlertDispatcher.get_recent_alerts() if a.subsystem == f"notice:{notice}"]
    assert bell and bell[0].title == "Crawl of the docs finished" and bell[0].severity == "SUCCESS"
    assert receipts == [True]
    assert f"[ops_alerts] receipt {notice}: kept off the chats" in caplog.text
    assert "[ops_alerts] receipt test.problem: delivered" in caplog.text


def test_control_notices_go_to_the_chats_by_default(delivered):
    sent, _store = delivered
    _recovery, notice = _recovery_and_notice_keys()
    ops_alerts.alert(notice, "Crawl of the docs finished", severity="success")
    ops_alerts.drain_alerts()
    assert any("Crawl of the docs finished" in t for t in sent)


# -- recent alerts --------------------------------------------------------------------------------


def _write_app_log(path, records):
    ts = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    path.write_text("\n".join(json.dumps({"timestamp": ts, "level": level, "message": msg})
                              for level, msg in records), encoding="utf-8")


def test_recent_alerts_from_every_sender_with_their_receipts(tmp_path, monkeypatch, caplog):
    import importlib.util
    import sys

    monkeypatch.delenv("KAZMA_OPS_ALERTS", raising=False)
    ops_alerts.reset_alert_state()
    # Kazma's lines, as the real alert() writes them, receipts included.
    monkeypatch.setattr(ops_alerts, "_dispatch",
                        lambda text, receipt=None, **_k: receipt(True if "Backups" in text else False))
    with caplog.at_level(logging.INFO, logger="kazma_core.observability.ops_alerts"):
        ops_alerts.alert("backup.offsite_failed", "Backups are local only", severity="critical", cooldown_s=3600)
        ops_alerts.alert("backup.offsite_failed", "Backups are local only", severity="critical", cooldown_s=3600)
        ops_alerts.alert("mcp.server_down", "An MCP server is down", cooldown_s=0)
    lines = [(r.levelname, r.getMessage()) for r in caplog.records
             if r.getMessage().startswith(("[ops_alert] ", "[ops_alerts] receipt "))]
    assert len(lines) == 5, lines
    app_log = tmp_path / "kazma.log"
    _write_app_log(app_log, lines)
    monkeypatch.setenv("KAZMA_LOG_FILE", str(app_log))
    # The guard's lines, as its notifier writes them.
    spec = importlib.util.spec_from_file_location(
        "_recent_guard", Path(__file__).resolve().parents[1] / "scripts" / "service" / "kazma_guard.py")
    guard = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = guard
    spec.loader.exec_module(guard)
    guard_log = tmp_path / "guard.log"
    monkeypatch.setenv("KAZMA_GUARD_LOG", str(guard_log))
    monkeypatch.setenv("KAZMA_GUARD_TELEGRAM_TOKEN", "1:x")
    monkeypatch.setenv("KAZMA_GUARD_TELEGRAM_CHAT", "5")
    notifier = guard.Notifier(guard.GuardLog(guard_log, echo=False))

    def refuse(req, timeout=None):
        raise OSError("network down")

    monkeypatch.setattr(guard.urllib.request, "urlopen", refuse)
    notifier.send(guard.format_operator_card("Guard", "error", "Kazma's guard crashed"))

    summary = {"ok": True, "incidents_7d": [{"cause": "tunnel_down", "started_at": time.time() - 600,
                                             "ended_at": None, "paged_at": time.time() - 500}]}
    out = recent_alerts.recent_alerts(watch_summary=summary)
    assert out["errors"] == []
    by = {(i["source"], i["key"] or i["title"]): i for i in out["items"]}
    offsite = by[("kazma", "backup.offsite_failed")]
    assert offsite["delivered"] is True and offsite["held"] == 1 and offsite["severity"] == "critical"
    assert offsite["link"] == "/settings?tab=backup" and offsite["title_ar"]
    assert by[("kazma", "mcp.server_down")]["delivered"] is False
    crashed = by[("guard", "guard.crashed")]
    assert crashed["delivered"] is False and crashed["severity"] == "error"
    tunnel = by[("watch", "watch.tunnel_down")]
    assert tunnel["delivered"] is True and tunnel["title"]
    assert [i["at"] for i in out["items"]] == sorted((i["at"] for i in out["items"]), reverse=True)
    # Control: without the receipt lines nothing says whether a channel took it.
    _write_app_log(app_log, [ln for ln in lines if "receipt" not in ln[1]])
    out = recent_alerts.recent_alerts(watch_summary={})
    assert {i["delivered"] for i in out["items"] if i["source"] == "kazma"} == {None}


def test_a_watch_incident_another_sender_paged_is_not_a_failed_page():
    """One page per problem (Stage 2) meets Recent alerts (Stage 3): Kazma
    Watch held an incident Kazma, its guard or a Cloudflare notice paged, so
    its summary names ``held_by`` and no ``paged_at``. That is no page of
    Kazma Watch's, and no recovery of its was due."""
    now = time.time()
    held = {"cause": "dependency_down", "started_at": now - 900, "ended_at": now - 300,
            "paged_at": None, "recovery_paged_at": None, "held_by": "database.away"}
    failed = {"cause": "tunnel_down", "started_at": now - 600, "ended_at": None,
              "paged_at": None, "recovery_paged_at": None, "held_by": None}
    items = recent_alerts._watch_alerts({"incidents_7d": [held, failed]}, now - 86400)
    assert [(i["key"], i["delivered"]) for i in items] == [("watch.tunnel_down", False)]
    # Control: read without its held_by, the held incident is a page that
    # reached nobody and a recovery note that never went out.
    items = recent_alerts._watch_alerts({"incidents_7d": [{**held, "held_by": None}]}, now - 86400)
    assert [(i["key"], i["delivered"]) for i in items] == [
        ("watch.dependency_down", False), ("watch.recovered", False)]


def test_the_recent_route_names_a_source_it_could_not_read(client, monkeypatch):
    def broken(_since):
        raise OSError("log gone")

    monkeypatch.setattr(recent_alerts, "_kazma_alerts", broken)
    data = client.get("/api/settings/alerts/recent").json()
    assert "kazma" in data["errors"]
