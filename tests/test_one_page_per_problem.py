"""One page per problem (unified alerts plan, Stage 2), Kazma's and the guard's halves.

A database outage, a full disk and a restart longer than three minutes were
each paged two or three times: by Kazma, by its guard and by Kazma Watch.
The rules held here:

* **Inside owners agree.** ``alert_catalog.inside_hold`` -- and the guard's
  stdlib copy ``_inside_hold``, both held to
  ``tests/fixtures/alert_receipts/holds.json`` -- decides who pages a
  condition both can see: a sender marked ``owner=False`` records it while an
  owner can speak, and of two inside owners (``SHARED_OWNERS``) the first to
  page it pages. ``ops_alerts.alert`` and the guard's ``_page`` ask it for
  every entry with a condition, so a condition added to the catalog follows
  the rule the day it is added (a planted entry shows it).
* **Page receipts.** Every page Kazma or the guard sends that opens an
  incident is a receipt -- the catalog key, when, whether a channel took it --
  until its recovery: Kazma's in the file the guard hands over
  (``KAZMA_GUARD_PAGES_FILE``), the guard's in its state file; the heartbeat
  carries both (``tests/test_observer_heartbeat.py`` holds the allowlist).
* **End to end.** Three live-shaped scenarios run Kazma's real ``alert``, the
  real guard and its real signed heartbeats, then replay those heartbeats
  through Kazma Watch's own Worker (``deploy/observer/test/replay_scenario.mjs``):
  exactly one page each, and one recovery from the sender that paged. The
  same scenario without the rules (the senders as they were) is the negative
  control.

The catalog relations Kazma Watch reads (``ANSWERED_BY``, ``SAME_CAUSE``) and
the Cloudflare entries (Stage 4) are gated here too. Each gate runs on a
planted violation first.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from kazma_core.observability import alert_catalog, ops_alerts, page_receipts
from kazma_core.observability.alert_catalog import ANSWERED_BY, CATALOG, SAME_CAUSE, _Entry

ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / "scripts" / "service" / "kazma_guard.py"
OBSERVER = ROOT / "deploy" / "observer"
HOLDS = json.loads((ROOT / "tests" / "fixtures" / "alert_receipts" / "holds.json").read_text(encoding="utf-8"))


def _load_guard():
    spec = importlib.util.spec_from_file_location("_one_page_guard", GUARD)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


guard = _load_guard()


@pytest.fixture
def inside(monkeypatch, tmp_path):
    """Kazma as a guard started it: its receipts file and the guard's state
    file in this test's folder; ops alerts on, delivery recorded (a channel
    takes each one unless ``outcome`` says otherwise)."""
    monkeypatch.setenv("KAZMA_GUARD_PAGES_FILE", str(tmp_path / "kazma_pages.json"))
    monkeypatch.setenv("KAZMA_GUARD_STATE_FILE", str(guard._state_path()))
    monkeypatch.delenv("KAZMA_OPS_ALERTS", raising=False)
    ops_alerts.reset_alert_state()
    page_receipts._reset_for_tests()
    sent: list[str] = []
    box = SimpleNamespace(sent=sent, outcome=True, pages=tmp_path / "kazma_pages.json")

    def _dispatch(text, on_delivery=None, **_kw):  # notice=: Settings -> Alerts' Notices
        sent.append(text)
        if on_delivery is not None:
            on_delivery(box.outcome)

    monkeypatch.setattr(ops_alerts, "_dispatch", _dispatch)
    yield box
    page_receipts._reset_for_tests()
    ops_alerts.reset_alert_state()


def _guard_state(**fields) -> None:
    path = guard._state_path()
    path.write_text(json.dumps(fields), encoding="utf-8")


def _file_pages(box) -> list[dict]:
    assert page_receipts.flush()
    return json.loads(box.pages.read_text(encoding="utf-8"))["pages"]


# -- the hold rule, in both copies ---------------------------------------------------


def _fixture_failures(hold) -> list[str]:
    return [c["name"] for c in HOLDS["cases"] if hold(c["key"], c["receipts"], c["speaks"]) != c["want"]]


def test_both_copies_answer_the_hold_fixture():
    assert len(HOLDS["cases"]) >= 10
    assert _fixture_failures(alert_catalog.inside_hold) == []
    assert _fixture_failures(guard._inside_hold) == []
    # Controls: a copy that never holds, and one that ignores "delivered".
    assert len(_fixture_failures(lambda *a: "")) == sum(1 for c in HOLDS["cases"] if c["want"])

    def blind(key, receipts, speaks):
        return alert_catalog.inside_hold(key, [{**r, "delivered": True} for r in receipts], speaks)

    assert _fixture_failures(blind) == ["a page that reached nobody holds nothing back"]


# -- the relations Kazma Watch reads ---------------------------------------------------


def _relation_problems(catalog, answered_by, same_cause) -> list[str]:
    watch = {e.key.removeprefix("watch."): e for e in catalog if e.sender == "watch"}
    inside = {e.condition for e in catalog if e.sender in alert_catalog.INSIDE_SENDERS and e.condition}
    out = []
    for condition, answers in sorted(answered_by.items()):
        entry = watch.get(condition)
        if entry is None:
            out.append(f"ANSWERED_BY names {condition}, which Kazma Watch does not diagnose")
        elif entry.owner:
            out.append(f"ANSWERED_BY names {condition}, which Kazma Watch owns: nothing inside answers for it")
        out += [f"ANSWERED_BY: no inside page has the condition {a}" for a in answers if a not in inside]
    for condition, also in sorted(same_cause.items()):
        out += [f"SAME_CAUSE names {c}, which Kazma Watch does not diagnose"
                for c in (condition, *also) if c not in watch]
    for e in catalog:
        if e.sender == "cloudflare" and e.condition:
            if e.owner:
                out.append(f"{e.key}: a Cloudflare notice witnesses a condition, it never owns one")
            if e.condition not in watch:
                out.append(f"{e.key}: {e.condition} is no condition of Kazma Watch")
    return out


def test_the_relations_name_real_conditions():
    planted_cf = (*CATALOG, _Entry("cloudflare.planted", "cloudflare", "warn", "x", "س", condition="disk_full"))
    assert _relation_problems(planted_cf, {"tunnel_down": ("database_away",), "dependency_down": ("nothing",)},
                              {"tunnel_down": ("nowhere",)}) == [
        "ANSWERED_BY: no inside page has the condition nothing",
        "ANSWERED_BY names tunnel_down, which Kazma Watch owns: nothing inside answers for it",
        "SAME_CAUSE names nowhere, which Kazma Watch does not diagnose",
        "cloudflare.planted: a Cloudflare notice witnesses a condition, it never owns one",
        "cloudflare.planted: disk_full is no condition of Kazma Watch",
    ]
    assert _relation_problems(CATALOG, ANSWERED_BY, SAME_CAUSE) == []


# -- Cloudflare's entries and the observer's mapping ------------------------------------


def _cloudflare_problems(cloudflare_js: str, catalog) -> list[str]:
    mapped = set(re.findall(r'"(cloudflare\.[a-z_]+)"', cloudflare_js))
    entries = {e.key for e in catalog if e.sender == "cloudflare"}
    return ([f"{k} is mapped by cloudflare.js but not in the catalog" for k in sorted(mapped - entries)]
            + [f"{k} is in the catalog but cloudflare.js never maps it" for k in sorted(entries - mapped)])


def test_every_cloudflare_entry_is_mapped_and_every_mapping_is_catalogued():
    text = (OBSERVER / "src" / "cloudflare.js").read_text(encoding="utf-8")
    planted = text + '\nconst X = "cloudflare.planted_notice";\n'
    lacking = (*CATALOG, _Entry("cloudflare.never_mapped", "cloudflare", "warn", "x", "س"))
    assert _cloudflare_problems(planted, CATALOG) == [
        "cloudflare.planted_notice is mapped by cloudflare.js but not in the catalog"]
    assert _cloudflare_problems(text, lacking) == ["cloudflare.never_mapped is in the catalog but cloudflare.js never maps it"]
    assert _cloudflare_problems(text, CATALOG) == []
    assert "cloudflare" in alert_catalog.SENDERS


# -- Kazma's side: holds ------------------------------------------------------------------


def test_a_non_owner_records_while_kazma_watch_watches(inside, caplog):
    caplog.set_level(logging.INFO, logger="kazma_core.observability.ops_alerts")
    beat = time.time() - 600  # the guard died ten minutes ago
    _guard_state(heartbeat=beat, outside_watch={"state": "reached", "last_ack": beat - 30})
    assert ops_alerts.alert("guard.gone", "Kazma is running without its guard", severity="critical") is False
    assert inside.sent == []
    lines = [r.getMessage() for r in caplog.records]
    assert any(m.startswith("[ops_alert_held] guard.gone | ") and "(paged by watch)" in m for m in lines)
    assert not any(m.startswith("[ops_alert] guard.gone") for m in lines)
    # Control: Kazma Watch had not heard the guard for ten minutes before it
    # died (it will not see the silence as new): Kazma pages it.
    _guard_state(heartbeat=beat, outside_watch={"state": "reached", "last_ack": beat - 600})
    assert ops_alerts.alert("guard.gone", "Kazma is running without its guard", severity="critical") is True
    assert len(inside.sent) == 1


def test_every_inside_non_owner_follows_the_rule(inside, monkeypatch):
    """Not the database alone: a condition added to the catalog with an owner
    is recorded by its non-owners on both sides, the day it is added."""
    planted = _Entry("planted.disk_full", "ops", "critical", "x", "س", condition="disk_critical", owner=False)
    monkeypatch.setattr(alert_catalog, "CATALOG", (*CATALOG, planted))
    _guard_state(heartbeat=time.time())          # the guard (disk_critical's owner) speaks
    assert ops_alerts.alert("planted.disk_full", "Disk full") is False
    _guard_state(heartbeat=time.time() - 3600)   # control: the guard is gone
    assert ops_alerts.alert("planted.disk_full", "Disk full") is True
    assert len(inside.sent) == 1

    # The guard's side: a guard page that Kazma owns is recorded while Kazma answers.
    table = dict(guard._alert_table())
    table["planted.ops"] = {"key": "planted.ops", "sender": "ops", "condition": "planted_cond", "owner": True}
    table["planted.guard"] = {"key": "planted.guard", "sender": "guard", "condition": "planted_cond",
                              "owner": False}
    monkeypatch.setattr(guard, "_alert_table_cache", table)
    monkeypatch.setenv("KAZMA_GUARD_DOCUMENT_BROKER", "off")
    g = guard.Guard()
    sent: list[str] = []
    said: list[str] = []
    g.notify.send = lambda text: (sent.append(text), True)[1]
    g.log = lambda level, event, **kw: said.append(event)
    g.proc = SimpleNamespace(poll=lambda: None, pid=1)
    g.spawned_at = time.time() - 60
    g._probe_note = {"ready": True, "answered": True, "at": time.time()}
    assert g._page("warn", "Planted", kind="planted.guard", force=True) is False
    assert sent == [] and said.count("guard.page_held") == 1
    g.proc = SimpleNamespace(poll=lambda: 1, pid=1)   # control: Kazma is down
    assert g._page("warn", "Planted", kind="planted.guard", force=True) is True
    assert len(sent) == 1


def test_of_two_inside_owners_the_first_to_page_it_pages(inside):
    _guard_state(heartbeat=time.time(),
                 pages=[{"key": "dbsvc.down", "paged_at": int(time.time()), "delivered": True}])
    assert ops_alerts.alert("database.away", "Kazma's database is not answering", severity="error") is False
    assert inside.sent == []
    # Control: the guard's page did not reach anyone -- Kazma pages.
    _guard_state(heartbeat=time.time(),
                 pages=[{"key": "dbsvc.down", "paged_at": int(time.time()), "delivered": False}])
    assert ops_alerts.alert("database.away", "Kazma's database is not answering", severity="error") is True
    assert len(inside.sent) == 1


# -- Kazma's side: receipts ----------------------------------------------------------------


def test_kazma_keeps_a_receipt_until_the_recovery(inside):
    ops_alerts.alert("database.away", "Kazma's database is not answering", severity="error")
    assert _file_pages(inside) == [{"key": "database.away", "paged_at": pytest.approx(time.time(), abs=5),
                                    "delivered": True}]
    ops_alerts.alert("database.back", "Kazma's database answers again", severity="success")
    assert _file_pages(inside) == []
    # A run-time name never leaves: the receipt is the catalog entry's key.
    ops_alerts.alert("llm.provider_unreachable.deepseek", "A model provider cannot be reached")
    assert [p["key"] for p in _file_pages(inside)] == ["llm.provider_unreachable.{provider}"]
    assert "deepseek" not in inside.pages.read_text(encoding="utf-8")
    # A page no channel took is a receipt that says so.
    inside.outcome = False
    ops_alerts.alert("database.away", "Kazma's database is not answering", severity="error", cooldown_s=0)
    assert {p["key"]: p["delivered"] for p in _file_pages(inside)}["database.away"] is False
    # Control: an alert that opens no incident leaves no receipt.
    page_receipts._reset_for_tests()
    ops_alerts.alert("backup.offsite_failed", "Backups are local only")
    assert _file_pages(inside) == []


def test_one_delivery_receipt_feeds_the_heartbeat_and_recent_alerts(inside, caplog):
    """Stages 2 and 3 meet in ``ops_alerts._Receipt``: told an alert's outcome
    once, it writes the receipt line Settings -> Alerts' recent alerts read
    and the open page receipt the heartbeat carries, and they agree."""
    caplog.set_level(logging.INFO, logger="kazma_core.observability.ops_alerts")
    told: list = []
    inside.outcome = False
    ops_alerts.alert("database.away", "Kazma's database is not answering", severity="error",
                     on_delivery=told.append)
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith(ops_alerts.RECEIPT_MARKER)]
    assert lines == [f"{ops_alerts.RECEIPT_MARKER}database.away: not delivered"]
    assert {p["key"]: p["delivered"] for p in _file_pages(inside)} == {"database.away": False}
    assert told == [False]
    # An alert that opens no incident still leaves its line, and no receipt.
    inside.outcome = True
    page_receipts._reset_for_tests()
    ops_alerts.alert("backup.offsite_failed", "Backups are local only")
    assert caplog.records[-1].getMessage() == f"{ops_alerts.RECEIPT_MARKER}backup.offsite_failed: delivered"
    assert _file_pages(inside) == []


def test_one_outcome_feeds_the_pager_record_and_the_held_pages_receipt(monkeypatch):
    """The guard hears a page's outcome once (``Notifier._heard``): the state
    file's ``pager`` (Settings -> Alerts) and, for a held page that went out
    after all, its receipt (the heartbeat) both say delivered."""
    g = _bare_guard(monkeypatch)
    g.notify.on_outcome = g._note_page_outcome
    monkeypatch.setattr(g.notify, "_post", lambda text, delayed=False: True)
    monkeypatch.setattr(g.notify, "_resolve", lambda: None)
    assert g._page("critical", "Disk almost full on C:", kind="disk.critical", force=True)
    state = guard._read_state()
    assert state["pages"][0]["delivered"] is False and state["pager"]["ok"] is False
    assert state["pager"]["held"] == 1 and state["pager"]["error"] == "not configured"
    g.notify.token, g.notify.chat = "1:x", "42"
    g.notify.flush_held()
    state = guard._read_state()
    assert state["pages"][0]["delivered"] is True
    assert state["pager"]["ok"] is True and state["pager"]["delayed"] is True
    # The record names the page, not the "Delayed" line wrapped around it.
    assert state["pager"]["head"] == "\U0001f6a8 [Guard] Disk almost full on C:"


def test_a_server_no_guard_started_writes_no_receipts(monkeypatch, tmp_path):
    monkeypatch.delenv("KAZMA_GUARD_PAGES_FILE", raising=False)
    monkeypatch.delenv("KAZMA_GUARD_STATE_FILE", raising=False)
    assert page_receipts._pages_path() is None
    assert page_receipts.record_page("database.away") is None
    assert page_receipts.held_by("guard.gone") == ""  # and holds nothing back
    # Control: the state file alone names the receipts file beside it.
    monkeypatch.setenv("KAZMA_GUARD_STATE_FILE", str(tmp_path / "guard.state.json"))
    assert page_receipts._pages_path() == tmp_path / "kazma_pages.json"


def _availability(monkeypatch):
    from kazma_core.db import availability

    clock = SimpleNamespace(t=1000.0, spawned=[])
    monkeypatch.setattr(availability, "_clock", lambda: clock.t)
    monkeypatch.setattr(availability, "_wall", lambda: 1_700_000_000.0 + clock.t)
    monkeypatch.setattr(availability, "_spawn_prober", lambda oid, gen: clock.spawned.append(oid))
    probe = {"answer": (False, "connection refused")}
    monkeypatch.setattr(availability, "_probe", lambda: probe["answer"])
    availability.reset()
    return availability, clock, probe


def test_the_database_page_waits_while_the_guard_has_it_and_is_asked_again(inside, monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="kazma_core.db.availability")
    availability, clock, probe = _availability(monkeypatch)
    try:
        _guard_state(heartbeat=time.time(),
                     pages=[{"key": "dbsvc.down", "paged_at": int(time.time()), "delivered": True}])
        availability.note_failure("x", reason="refused")
        oid = clock.spawned[0]
        availability._probe_once(oid)
        clock.t += availability.PAGE_AFTER_S + 1
        availability._probe_once(oid)
        availability._probe_once(oid)
        assert inside.sent == []
        assert not availability.snapshot()["page_requested"]  # asked again next probe
        said = [r.getMessage() for r in caplog.records if "Kazma records it" in r.getMessage()]
        assert len(said) == 1  # said once an outage
        # The guard's page closed (its service answers); the database is still away.
        _guard_state(heartbeat=time.time(), pages=[])
        availability._probe_once(oid)
        assert len(inside.sent) == 1 and "database is not answering" in inside.sent[0]
    finally:
        availability.reset()


@pytest.mark.parametrize("delivered", [True, False])
def test_the_outage_ending_settles_kazmas_receipt(inside, monkeypatch, delivered):
    availability, clock, probe = _availability(monkeypatch)
    try:
        inside.outcome = delivered
        availability.note_failure("x", reason="refused")
        oid = clock.spawned[0]
        availability._probe_once(oid)
        clock.t += availability.PAGE_AFTER_S + 1
        availability._probe_once(oid)
        assert [p["key"] for p in _file_pages(inside)] == ["database.away"]
        probe["answer"] = (True, None)
        availability._probe_once(oid)
        # Delivered: database.back went out and settled it; not delivered: no
        # recovery, and the receipt goes with the outage all the same.
        assert _file_pages(inside) == []
        assert any("answers again" in t for t in inside.sent) is delivered
    finally:
        availability.reset()


# -- the guard's side --------------------------------------------------------------------------


def _bare_guard(monkeypatch):
    monkeypatch.setenv("KAZMA_GUARD_DOCUMENT_BROKER", "off")
    monkeypatch.setenv("KAZMA_GUARD_TELEGRAM_TOKEN", "")
    monkeypatch.setenv("KAZMA_GUARD_TELEGRAM_CHAT", "")
    g = guard.Guard()
    g.broker.wanted = False
    return g


def test_the_guards_receipts_open_and_settle(monkeypatch):
    g = _bare_guard(monkeypatch)
    sent: list[str] = []
    g.notify.send = lambda text: (sent.append(text), True)[1]
    assert g._page("critical", "Disk almost full on C:", "2% free", kind="disk.critical", force=True)
    assert [(p["key"], p["delivered"]) for p in guard._read_state()["pages"]] == [("disk.critical", True)]
    assert g._page("success", "Disk space is back on C:", kind="disk.back", force=True)
    assert guard._read_state()["pages"] == []
    # Control: a page that opens no incident is no receipt.
    g._page("info", "Kazma is restarting for an operator reload", kind="guard.operator_reload", force=True)
    assert guard._read_state()["pages"] == []


def test_a_page_held_by_the_notifier_says_delivered_once_it_goes_out(monkeypatch):
    g = _bare_guard(monkeypatch)
    posted: list[str] = []
    monkeypatch.setattr(g.notify, "_post", lambda text, delayed=False: (posted.append(text), True)[1])
    monkeypatch.setattr(g.notify, "_resolve", lambda: None)
    assert g._page("critical", "Disk almost full on C:", kind="disk.critical", force=True)
    assert guard._read_state()["pages"][0]["delivered"] is False   # held: no credentials yet
    g.notify.token, g.notify.chat = "1:x", "42"
    g.notify.flush_held()
    assert len(posted) == 1 and posted[0].startswith("⏳ Delayed")
    assert guard._read_state()["pages"][0]["delivered"] is True


def test_the_notifier_says_whether_telegram_took_it(monkeypatch):
    g = _bare_guard(monkeypatch)
    g.notify.token, g.notify.chat = "1:x", "42"
    monkeypatch.setattr(g.notify, "_post", lambda text, delayed=False: True)
    assert g.notify.send("x") is True
    monkeypatch.setattr(g.notify, "_post", lambda text, delayed=False: False)
    assert g.notify.send("x") is False
    g.notify.token = ""
    monkeypatch.setattr(g.notify, "_resolve", lambda: None)
    assert g.notify.send("held") is False   # held until the credentials resolve


def test_the_guard_reads_only_its_current_servers_receipts(inside, monkeypatch):
    ops_alerts.alert("database.away", "Kazma's database is not answering", severity="error")
    assert page_receipts.flush()
    started = json.loads(inside.pages.read_text(encoding="utf-8"))["process_started"]
    assert [p["key"] for p in guard.PageReceipts.kazma(started - 0.5)] == ["database.away"]
    # Control: a guard that spawned its server after that file was written
    # reads an earlier server's receipts, which say nothing about this one.
    assert guard.PageReceipts.kazma(started + 5) == []


# -- end to end: three live-shaped scenarios ----------------------------------------------------

BASE = 1_800_000_000 - (1_800_000_000 % 3600)
INSTALL = "a1b2c3d4e5f6a7b8"
KEY = "scenario-pairing-key-0123456789abcdefghij"


def _node_with_sqlite() -> str | None:
    node = shutil.which("node")
    if not node:
        return None
    probe = subprocess.run([node, "-e", "require('node:sqlite')"], capture_output=True, timeout=60,
                           check=False)
    return node if probe.returncode == 0 else None


class _Proc:
    def __init__(self) -> None:
        self.alive = True
        self.pid = 4242

    def poll(self):
        return None if self.alive else 1


class _Machine:
    """Kazma and its guard on one clock, a signed heartbeat a minute.

    ``one_page=False`` is the senders as they were before Stage 2: no hold
    on either side, and a heartbeat with no receipts.
    """

    def __init__(self, monkeypatch, tmp_path, box, *, one_page=True, service=None, disk=False) -> None:
        self.t = 0.0
        self.box = box
        self.one_page = one_page

        def wall() -> float:
            return BASE + self.t

        monkeypatch.setattr(guard, "time", SimpleNamespace(
            monotonic=lambda: 10_000.0 + self.t, time=wall, sleep=lambda s: None))
        monkeypatch.setattr(page_receipts, "time", SimpleNamespace(time=wall, sleep=time.sleep))
        monkeypatch.setattr(page_receipts, "_STARTED", BASE - 30.0)
        monkeypatch.setenv("KAZMA_GUARD_DOCUMENT_BROKER", "off")
        monkeypatch.setenv("KAZMA_GUARD_TELEGRAM_TOKEN", "")
        monkeypatch.setenv("KAZMA_GUARD_TELEGRAM_CHAT", "")
        if disk:
            monkeypatch.setenv("KAZMA_GUARD_DISK_WATCH", "auto")
        if not one_page:
            monkeypatch.setattr(guard, "_inside_hold", lambda *a: "")
            monkeypatch.setattr(page_receipts, "held_by", lambda *a, **k: "")
        self.g = g = guard.Guard()
        g.broker.wanted = False
        g._started_mono = 10_000.0 - 600
        g.started_at = BASE - 600
        self.guard_pages: list[str] = []
        g.notify.send = lambda text: (self.guard_pages.append(text), True)[1]
        g.proc = _Proc()
        g.spawned_at = BASE - 40.0
        self.db_up = True
        if service:
            g.dbsvc = guard.DatabaseServiceWatch(
                g, record=lambda: {"os": "windows", "service": service, "port": 5432},
                run=lambda argv, timeout: (0, "STATE : 4  RUNNING" if self.db_up else "STATE : 1  STOPPED", ""),
                answers=lambda port: self.db_up)
        self.free_gb = 50.0
        if disk:
            g.disk = guard.DiskWatch(g, reader=lambda p: SimpleNamespace(
                total=100 * 1024 ** 3, free=int(self.free_gb * 1024 ** 3)), paths=[("C:", "data", tmp_path)])
        self.ready(True)
        self.minutes: list[dict] = []
        self.seq = 0

    def ready(self, ok: bool, *, why=(), database=None, answered=True) -> None:
        self.g._probe_note = {"ready": ok, "answered": answered, "checks": list(why),
                              "database": database, "at": BASE + self.t}

    def minute(self, *, live: int = 200, ready: int = 200) -> None:
        for _ in range(6):
            self.g._broker_tick()
            self.t += 10.0
        self.g._beat(force=True)
        page_receipts.flush()
        self.seq += 1
        snap = self.g._heartbeat_snapshot(self.seq)
        snap.update(install=INSTALL, cloudflared="running", power=None)
        body = guard.heartbeat_body(snap)
        if not self.one_page:
            data = json.loads(body)
            data.pop("pages")
            body = json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ts = int(BASE + self.t)
        self.minutes.append({"now": ts, "live": live, "ready": ready, "heartbeat": {
            "body": body.decode("utf-8"), "ts": ts, "signature": guard.sign_request(KEY.encode(), ts, "POST", "/v1/heartbeat", body)}})

    def watch(self, node: str, tmp_path: Path) -> dict:
        path = tmp_path / "scenario.json"
        path.write_text(json.dumps({"install": INSTALL, "key": KEY, "minutes": self.minutes}), encoding="utf-8")
        result = subprocess.run([node, "test/replay_scenario.mjs", str(path)], cwd=str(OBSERVER),
                                capture_output=True, text=True, encoding="utf-8", timeout=120, check=False)
        assert result.returncode == 0, result.stderr[-2000:]
        out = json.loads(result.stdout)
        assert out["beats"] and set(out["beats"]) == {200}, out["beats"]
        return out

    def count(self, watched: dict) -> tuple[list[str], list[str]]:
        """(problem pages, recovery pages) every sender sent."""
        texts = self.box.sent + self.guard_pages + [m["text"] for m in watched["messages"]]
        return ([t for t in texts if not t.startswith("\U0001f7e2")],
                [t for t in texts if t.startswith("\U0001f7e2")])


@pytest.fixture
def node():
    found = _node_with_sqlite()
    if found is None:
        pytest.skip("needs Node 22.5+ (node:sqlite) to replay the heartbeats through Kazma Watch")
    return found


def _database_outage(m: _Machine) -> None:
    for _ in range(5):
        m.minute()
    m.db_up = False                     # the database service stops
    m.ready(False, why=("database",), database={"state": "away", "confirmed": True})
    for _ in range(2):
        m.minute(ready=503)
    # Kazma's prober pages it after two minutes (db/availability, PAGE_AFTER_S).
    ops_alerts.alert("database.away", "Kazma's database is not answering",
                     "Not answering since 12:05 UTC: connection refused.", severity="error")
    for _ in range(18):                 # the guard's service page is due at 5 min
        m.minute(ready=503)
    m.db_up = True
    m.ready(True)
    ops_alerts.alert("database.back", "Kazma's database answers again", "Back after 20 min.",
                     severity="success")
    for _ in range(5):
        m.minute()


def _critical_disk(m: _Machine) -> None:
    for _ in range(3):
        m.minute()
    m.free_gb = 1.0
    for _ in range(15):
        m.minute()
    m.free_gb = 50.0
    for _ in range(4):
        m.minute()


def _long_restart(m: _Machine) -> None:
    for _ in range(3):
        m.minute()
    m.g.proc.alive = False              # the server crashed
    m.g.notify_restart("process exited (code 1)", 5.0)
    for _ in range(3):
        m.minute(live=502, ready=502)
    m.g.proc = _Proc()                  # respawned; booting for three more minutes
    m.g.spawned_at = BASE + m.t
    m.g._probe_note = None
    for _ in range(3):
        m.minute(live=502, ready=502)
    m.ready(True)
    m.g.notify_recovered()
    for _ in range(4):
        m.minute()


SCENARIOS = {
    "a database outage": (_database_outage, {"service": "postgresql-x64-16"}, 3),
    "a critical disk": (_critical_disk, {"disk": True}, 2),
    "a restart longer than three minutes": (_long_restart, {}, 2),
}


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_one_page_each_end_to_end(name, inside, node, monkeypatch, tmp_path):
    play, options, _old = SCENARIOS[name]
    m = _Machine(monkeypatch, tmp_path, inside, **options)
    play(m)
    pages, recoveries = m.count(m.watch(node, tmp_path))
    assert len(pages) == 1, pages
    assert len(recoveries) == 1, recoveries
    # The recovery comes from the sender that paged.
    assert pages[0].split("]")[0].split("[")[1] == recoveries[0].split("]")[0].split("[")[1]


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_control_the_senders_as_they_were_page_it_more_than_once(name, inside, node, monkeypatch, tmp_path):
    play, options, old = SCENARIOS[name]
    m = _Machine(monkeypatch, tmp_path, inside, one_page=False, **options)
    play(m)
    pages, _recoveries = m.count(m.watch(node, tmp_path))
    assert len(pages) == old, pages
    assert any("[Watch]" in p for p in pages)
