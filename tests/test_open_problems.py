"""One list of open problems (resilience plan G2).

Open alerts lived in Telegram's history, the bell and the digest. The list
is built each time from where each problem lives, so a resolved problem
leaves by itself; a problem can be acknowledged (hidden until it changes)
or snoozed for a day; a source that cannot be read is named, never "none";
the daily digest reads the same list.
"""

from __future__ import annotations

import pytest

from kazma_core import config_store
from kazma_core.observability import open_problems as op


@pytest.fixture
def store(tmp_path, monkeypatch):
    settings = config_store.ConfigStore(str(tmp_path / "settings.db"))
    monkeypatch.setattr(config_store, "get_config_store", lambda: settings)
    yield settings
    settings.close()


def _source(name, rows, start=0):
    def read(language):
        return [op.Problem(op._pid(name, r), name, r, "detail", 1000.0 + start + i) for i, r in enumerate(rows)]
    read.__name__ = f"_{name}"
    return read


def test_every_source_is_listed_newest_first(store):
    got = op.open_problems(sources=(_source("alert", ["a"]), _source("advisory", ["b", "c"], 10)))
    assert [p["title"] for p in got["problems"]] == ["c", "b", "a"]
    assert got["unreadable"] == [] and got["hidden"] == 0


def test_a_resolved_problem_leaves_by_itself(store):
    rows = ["disk low"]
    sources = (_source("alert", rows),)
    assert len(op.open_problems(sources=sources)["problems"]) == 1
    rows.clear()  # the source no longer holds it
    assert op.open_problems(sources=sources)["problems"] == []


def test_acknowledged_until_it_changes_and_snoozed_for_a_day(store):
    sources = (_source("alert", ["disk low", "model failover"]),)
    first, second = op.open_problems(sources=sources)["problems"][::-1]
    op.acknowledge(first["id"], "owner")
    op.snooze(second["id"], "owner", now=5000.0)
    got = op.open_problems(sources=sources, now=5000.0 + 3600)
    assert got["problems"] == [] and got["hidden"] == 2
    # A day later the snoozed one is back; the acknowledged one stays hidden.
    later = op.open_problems(sources=sources, now=5000.0 + op.SNOOZE_S + 1)
    assert [p["title"] for p in later["problems"]] == ["model failover"]
    # A changed problem is a new one: shown again.
    changed = op.open_problems(sources=(_source("alert", ["disk almost full"]),), now=5000.0)
    assert [p["title"] for p in changed["problems"]] == ["disk almost full"]
    shown = op.open_problems(sources=sources, now=5000.0 + 3600, include_hidden=True)["problems"]
    assert {p["title"]: (p["acknowledged"], bool(p["snoozed_until"])) for p in shown} == {
        "disk low": (True, False), "model failover": (False, True)}


def test_a_source_that_cannot_be_read_is_named(store):
    def broken(language):
        raise OSError("store away")
    broken.__name__ = "_incidents"
    got = op.open_problems(sources=(broken, _source("alert", ["a"])))
    assert got["unreadable"] == ["incidents"] and len(got["problems"]) == 1


def test_each_real_source_turns_its_own_record_into_a_problem(store, monkeypatch):
    from kazma_core.observability.alerts import AlertDispatcher

    AlertDispatcher.post_banner(subsystem="llm-failover:x", title="Model failover: x failed",
                                reason="x kept failing", since=1000.0)
    try:
        alerts = op._alerts("en")
    finally:
        AlertDispatcher.resolve_alerts_for_subsystem("llm-failover:x")
    assert [p.title for p in alerts] == ["Model failover: x failed"]
    assert op._alerts("en") == [], "resolved: gone"
    import kazma_core.install_requirements as ir

    monkeypatch.setattr(ir, "last_requirements_check", lambda: {"unmet": [{"requirement": "pypdf>=6.1"}], "at": 5.0})
    (req,) = op._requirements("en")
    assert req.title == "pypdf>=6.1" and req.fix == "kazma update --reinstall -y"
    monkeypatch.setattr(ir, "last_requirements_check", lambda: {"unmet": []})
    assert op._requirements("en") == []


def test_the_api_lists_and_the_owner_acknowledges(store, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from kazma_ui.problems_api import create_problems_router
    import kazma_ui.auth as auth

    monkeypatch.setattr(op, "_SOURCES", (_source("alert", ["disk low"]),))
    monkeypatch.setattr(auth, "admin_decision", lambda request: "ok")
    app = FastAPI()
    app.include_router(create_problems_router())
    client = TestClient(app, headers={"X-Requested-With": "XMLHttpRequest"})
    (problem,) = client.get("/api/problems").json()["problems"]
    assert client.post(f"/api/problems/{problem['id']}/ack").json()["ok"] is True
    assert client.get("/api/problems").json()["problems"] == []
    assert client.post("/api/problems/not-an-id/ack").status_code == 422


def test_the_daily_digest_reads_the_same_list(store, monkeypatch):
    from kazma_core.observability import daily_digest

    monkeypatch.setattr(op, "_SOURCES", (_source("advisory", ["urllib3: GHSA-x"]),))
    assert daily_digest._open_problems_line() == "Open problems: 1 (urllib3: GHSA-x)"
    monkeypatch.setattr(op, "_SOURCES", ())
    assert daily_digest._open_problems_line() is None
