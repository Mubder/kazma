"""Notifications the owner has read (2026-11-08).

The owner: a notification opened its page, "but the notification still there
with a bell badge showing". Nothing recorded that one was seen, so the badge
counted every alert the server listed. Now opening one (or "Mark all as
read") records it on the server (``observability.notification_reads``), and
``/api/alerts/recent`` says ``read`` per item for the bell to count the rest.
The page's half is tests/js/test_bell_links.js; a browser walks it in
tests/e2e/test_bell_opens_the_page.py.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def store(tmp_path):
    from kazma_core.config_store import ConfigStore

    s = ConfigStore(db_path=str(tmp_path / "settings.db"))
    yield s
    s.close()


def test_read_ids_are_kept_and_bounded(store):
    from kazma_core.observability import notification_reads as nr

    assert nr.read_ids(store) == set()
    assert nr.mark_read(["a", "b"], store=store, now=1.0) == 2
    assert nr.read_ids(store) == {"a", "b"}
    many = [f"n{i}" for i in range(nr.KEEP + 5)]
    for i, notification in enumerate(many):
        nr.mark_read([notification], store=store, now=10.0 + i)
    kept = nr.read_ids(store)
    assert len(kept) == nr.KEEP and "a" not in kept and many[-1] in kept, "the oldest read go first"


@pytest.mark.parametrize("ids", [None, "a", [1], [""], ["x" * 201], ["a"] * 201])
def test_only_notification_ids_are_taken(ids):
    from kazma_core.observability.notification_reads import clean_ids

    with pytest.raises(ValueError):
        clean_ids(ids)


def test_ids_are_taken_once():
    from kazma_core.observability.notification_reads import clean_ids

    assert clean_ids(["a", "a", "b"]) == ["a", "b"]


def test_read_is_a_state_a_settings_backup_never_carries():
    from kazma_core.observability.notification_reads import READ_KEY
    from kazma_core.settings_restore import RUNTIME, classify

    assert classify(READ_KEY) == RUNTIME


@pytest.fixture
def client():
    from kazma_core.observability.alerts import AlertDispatcher
    from kazma_ui.app import create_app

    AlertDispatcher.reset_state()
    with TestClient(create_app()) as c:
        yield c
    AlertDispatcher.reset_state()


def test_the_bell_hears_what_was_read(client):
    from kazma_core.observability.alerts import AlertDispatcher

    AlertDispatcher.post_banner(subsystem="Memory", title="Memory recall degraded", reason="r")
    AlertDispatcher.post_banner(subsystem="System", title="RAM high", reason="r")

    def listed():
        return {a["title"]: a["read"] for a in client.get("/api/alerts/recent").json()}

    assert listed() == {"Memory recall degraded": False, "RAM high": False}
    memory = next(a for a in client.get("/api/alerts/recent").json() if a["subsystem"] == "Memory")
    answer = client.post("/api/alerts/read", json={"ids": [memory["id"]]})
    assert answer.status_code == 200 and answer.json() == {"ok": True, "read": 1}
    assert listed() == {"Memory recall degraded": True, "RAM high": False}
    # A refusal says so, and changes nothing.
    bad = client.post("/api/alerts/read", json={"ids": "everything"})
    assert bad.status_code == 400 and bad.json()["code"] == "invalid_ids"
    assert listed()["RAM high"] is False
