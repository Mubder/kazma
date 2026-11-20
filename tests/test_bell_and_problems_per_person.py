"""Spaces S1-32: the bell, the open problems and the Running work card are
per person; a research topic or chat title is shown only to members of its
space.

Two people, each with their own space, and the admin. In the first person's
space a research session runs with a planted word in its topic, another
fails (the acquisition monitor notices it), and a chat's run incident is
opened with a planted word in its title. Through the real routes --
``/api/alerts/recent`` (the bell), ``/api/problems`` (the Dashboard's open
problems) and ``/api/runs`` (Running work) -- the first person sees them;
the other person and the admin see none of the words, nor the session ids.
The install's own notices (a service alert, an unmet requirement) are the
admin's, never a member's. And another space's research news never goes
to the install's channels.

Control: the lists of before -- the monitor's notices for every space, the
install's alerts for everyone -- show the first person's word to the other.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from kazma_core.config_store import get_config_store
from kazma_core.runs.contract import Run, RunState
from kazma_core.spaces import identity, people, scope
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID, OWNER_SPACE_ID

TOPIC_WORD = "plantedtopic" + uuid.uuid4().hex[:6]
FAILED_WORD = "plantedfailed" + uuid.uuid4().hex[:6]
CHAT_WORD = "plantedchat" + uuid.uuid4().hex[:6]
INSTALL_WORD = "plantedinstall" + uuid.uuid4().hex[:6]


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    from kazma_ui.app import create_app

    with TestClient(create_app(), client=("127.0.0.1", 40000), base_url="http://127.0.0.1:9090") as client:
        yield client


def _as(monkeypatch, principal: dict[str, Any]) -> None:
    from kazma_ui import auth

    monkeypatch.setattr(auth, "get_request_principal", lambda request: principal)
    monkeypatch.setattr(auth, "admin_decision",
                        lambda request: "ok" if principal.get("role") == "admin" else "forbidden")


def _admin() -> dict[str, Any]:
    return {"source": "secret", "role": "admin", "user_id": "", "actor": "secret"}


def _person(person: dict[str, Any]) -> dict[str, Any]:
    return {"source": "session", "role": "operator", "user_id": person["id"], "actor": "login"}


@pytest.fixture
def world(tmp_path, monkeypatch):
    from kazma_core.observability.alerts import AlertDispatcher
    from kazma_core.runs.incidents import IncidentStore, get_incident_store, reset_incident_store
    from kazma_core.tools import research_session
    from kazma_core.web_acquire import monitor

    monkeypatch.setattr(scope, "_enforced", True)
    monkeypatch.setattr(research_session, "_db_path", lambda: tmp_path / "research_sessions.db")
    reset_incident_store(IncidentStore(tmp_path / "incidents.db"))
    one = people.create_person("bell-one", "a-long-enough-password", by=OWNER_ACCOUNT_ID)
    two = people.create_person("bell-two", "a-long-enough-password", by=OWNER_ACCOUNT_ID)
    space = one["personal_space"]
    # The monitor's first pass sets its start; a run that fails after it is news.
    monitor.tick()
    with scope.bind_scope(space, one["id"]):
        running = research_session.create_session(f"about {TOPIC_WORD} today")
        research_session.update_session(running.id, status="running", stage="searching")
        failed = research_session.create_session(f"about {FAILED_WORD} today")
    research_session.update_session(failed.id, status="error", stage="failed", message="it failed")
    monitor.tick()
    store = get_incident_store()
    store.open_incident(Run(run_id=f"turn:{uuid.uuid4().hex[:6]}", kind="chat_turn", native_id="t1",
                            title=f"chat {CHAT_WORD}", state=RunState.STALLED, native_state="running",
                            space=space, asker=one["id"]),
                        "stalled", origin={})
    AlertDispatcher.clear_alerts()
    AlertDispatcher.post_banner(subsystem="TestService", title=f"service {INSTALL_WORD}", reason="it broke")
    try:
        yield {"one": one, "two": two, "space": space, "running": running.id, "failed": failed.id}
    finally:
        get_config_store().delete(identity.REGISTRY_KEY)
        AlertDispatcher.clear_alerts()
        reset_incident_store(None)


def _seen(client, space: str) -> str:
    headers = {"X-Kazma-Space": space}
    parts = []
    for path in ("/api/alerts/recent", "/api/problems", "/api/runs"):
        answer = client.get(path, headers=headers)
        assert answer.status_code == 200, (path, answer.status_code, answer.text[:300])
        parts.append(answer.text)
    return "\n".join(parts)


def test_the_first_person_sees_their_own_spaces_notices(client, world, monkeypatch) -> None:
    _as(monkeypatch, _person(world["one"]))
    text = _seen(client, world["space"])
    assert TOPIC_WORD in text and CHAT_WORD in text
    assert world["failed"] in text  # the monitor's notice of the failed session
    assert INSTALL_WORD not in text  # the install's own alert is the admin's


def test_no_other_account_sees_them(client, world, monkeypatch) -> None:
    for principal, space in ((_person(world["two"]), world["two"]["personal_space"]),
                             (_admin(), OWNER_SPACE_ID)):
        _as(monkeypatch, principal)
        text = _seen(client, space)
        for planted in (TOPIC_WORD, FAILED_WORD, CHAT_WORD, world["running"], world["failed"]):
            assert planted not in text, (principal["source"], planted)


def test_the_installs_own_notices_are_the_admins(client, world, monkeypatch) -> None:
    _as(monkeypatch, _admin())
    assert INSTALL_WORD in _seen(client, OWNER_SPACE_ID)
    _as(monkeypatch, _person(world["two"]))
    assert INSTALL_WORD not in _seen(client, world["two"]["personal_space"])


def test_another_spaces_research_news_stays_off_the_installs_channels(world) -> None:
    from kazma_core.web_acquire import monitor

    rows = [row for row in monitor._read().get("events", []) if row["key"] == "session:" + world["failed"]]
    assert rows and rows[-1]["space"] == world["space"]
    assert rows[-1]["dispatch"] == "kept_in_its_space"


def test_control_the_lists_of_before_show_the_word_to_the_other(client, world, monkeypatch) -> None:
    from kazma_core.runs import spaces as run_spaces
    from kazma_core.web_acquire import monitor

    monkeypatch.setattr(monitor, "_shown_here", lambda space, install_ok: True)
    monkeypatch.setattr(run_spaces, "install_notices_here", lambda: True)
    _as(monkeypatch, _person(world["two"]))
    text = _seen(client, world["two"]["personal_space"])
    assert world["failed"] in text and INSTALL_WORD in text


def test_without_spaces_everyone_sees_the_installs_bell(client, world, monkeypatch) -> None:
    monkeypatch.setattr(scope, "_enforced", False)
    _as(monkeypatch, _admin())
    text = "\n".join(client.get(p).text for p in ("/api/alerts/recent", "/api/problems"))
    assert INSTALL_WORD in text and world["failed"] in text


def test_a_crawl_whose_space_cannot_be_read_is_shown_to_nobody(monkeypatch) -> None:
    import sqlite3

    from kazma_core.runs.spaces import kept_in_its_space
    from kazma_core.web_acquire import monitor

    class _Unreadable:
        def of(self, row):
            raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(scope, "_enforced", True)
    space = monitor._crawl_space(_Unreadable(), {"library_id": "lib"})
    assert space == monitor.UNKNOWN_SPACE
    with scope.bind_scope(OWNER_SPACE_ID, OWNER_ACCOUNT_ID):
        assert not monitor._shown_here(space, True)
    assert kept_in_its_space(space)
    # Without Spaces nothing is read: every notice is everyone's, as before.
    monkeypatch.setattr(scope, "_enforced", False)
    assert monitor._crawl_space(_Unreadable(), {"library_id": "lib"}) == OWNER_SPACE_ID
