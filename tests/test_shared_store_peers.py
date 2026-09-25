"""Every install that boots against one Postgres settings store is named.

The 2026-09-16 outage was two installs sharing one Postgres ConfigStore — the
dev clone and the live server — so a ``vault://`` pointer written by one
resolved to nothing on the other. ``ConfigStore`` warns when
``KAZMA_DATA_DIR`` is relocated on Postgres, but the dev clone relocated
nothing: no single-process check could have seen it. The store can, so every
server boot records itself and names the others (``kazma_core.db.
shared_store_peers``).

These run against a real SQLite ConfigStore — the record/read/prune logic is
backend-agnostic; only the boot hook gates on ``is_postgres()``, which is
tested by flipping the backend check.
"""

from __future__ import annotations

import logging
import time

import pytest
from kazma_core.config_store import ConfigStore
from kazma_core.db import shared_store_peers as ssp

DAY = 86400.0


@pytest.fixture
def store(tmp_path):
    cs = ConfigStore(db_path=str(tmp_path / "settings.db"), yaml_path=str(tmp_path / "none.yaml"))
    yield cs
    cs.close()


def _lines(caplog, level):
    return [
        r.getMessage() for r in caplog.records
        if r.name == ssp.__name__ and r.levelno == level
    ]


# ── install identity ──────────────────────────────────────────────────────


def test_the_install_id_is_stable_and_lives_in_the_data_dir(tmp_path):
    first = ssp.install_id(tmp_path)
    assert ssp._is_id(first)
    assert (tmp_path / "install_id").read_text(encoding="utf-8").strip() == first
    assert ssp.install_id(tmp_path) == first, "a restart must keep its identity"


def test_two_data_dirs_are_two_installs(tmp_path):
    assert ssp.install_id(tmp_path / "a") != ssp.install_id(tmp_path / "b")


def test_a_damaged_id_file_is_replaced_not_trusted(tmp_path):
    (tmp_path / "install_id").write_text("not-an-id\n", encoding="utf-8")
    fresh = ssp.install_id(tmp_path)
    assert ssp._is_id(fresh)
    assert ssp.install_id(tmp_path) == fresh


def test_the_read_only_form_never_mints_one(tmp_path):
    assert ssp.install_id(tmp_path, create=False) is None
    assert not (tmp_path / "install_id").exists(), "kazma doctor writes nothing"


# ── record, read, prune ───────────────────────────────────────────────────


def test_a_second_install_sees_the_first(store):
    now = time.time()
    assert ssp._announce_and_find_peers(store, me="a" * 32, data_dir="/srv/live", now=now) == []
    peers = ssp._announce_and_find_peers(store, me="b" * 32, data_dir="G:/dev/kazma-data", now=now + 60)
    assert [p.install_id for p in peers] == ["a" * 32]
    assert peers[0].data_dir == "/srv/live"


def test_an_install_is_never_its_own_peer(store):
    now = time.time()
    ssp._announce_and_find_peers(store, me="a" * 32, data_dir="/x", now=now)
    assert ssp._announce_and_find_peers(store, me="a" * 32, data_dir="/x", now=now + 5) == []


def test_old_boots_leave_the_window_and_then_the_store(store):
    now = time.time()
    ssp._announce_and_find_peers(store, me="a" * 32, data_dir="/old", now=now - 30 * DAY)
    ssp._announce_and_find_peers(store, me="c" * 32, data_dir="/ancient", now=now - 120 * DAY)

    peers = ssp._announce_and_find_peers(store, me="b" * 32, data_dir="/new", now=now)
    assert peers == [], "outside the peer window: not named"
    remaining = set(store.get_category(ssp.CATEGORY))
    assert f"{ssp.CATEGORY}.{'a' * 32}" in remaining, "inside the prune horizon: kept"
    assert f"{ssp.CATEGORY}.{'c' * 32}" not in remaining, "past it: deleted"


def test_recent_peers_is_read_only(store):
    now = time.time()
    ssp._announce_and_find_peers(store, me="a" * 32, data_dir="/x", now=now - 120 * DAY)
    before = dict(store.get_category(ssp.CATEGORY))
    assert ssp.recent_peers(store, "b" * 32, now=now) == []
    assert store.get_category(ssp.CATEGORY) == before, "reading must not prune"


# ── the boot hook ─────────────────────────────────────────────────────────


def test_the_boot_hook_names_an_unknown_peer(store, tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(ssp, "_backend_is_postgres", lambda: True)
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path / "dev-data"))
    ssp._announce_and_find_peers(store, me="a" * 32, data_dir="/srv/live", now=time.time() - 3600)

    caplog.set_level(logging.INFO, logger=ssp.__name__)
    peers = ssp.check_shared_store_peers(store)

    assert [p.install_id for p in peers] == ["a" * 32]
    (line,) = _lines(caplog, logging.WARNING)
    assert "aaaaaaaa" in line and "/srv/live" in line
    assert ssp.ACK_KEY in line, "the line must say how to acknowledge a replica"


def test_an_acknowledged_replica_is_info_not_a_warning(store, tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(ssp, "_backend_is_postgres", lambda: True)
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path / "replica-2"))
    ssp._announce_and_find_peers(store, me="a" * 32, data_dir="/srv/r1", now=time.time() - 60)
    store.set(ssp.ACK_KEY, ["aaaaaaaa"])  # the 8-char prefix the boot line prints

    caplog.set_level(logging.INFO, logger=ssp.__name__)
    ssp.check_shared_store_peers(store)
    assert _lines(caplog, logging.WARNING) == []
    assert any("acknowledged" in ln for ln in _lines(caplog, logging.INFO))


def test_a_new_sharer_still_warns_beside_an_acknowledged_one(store, tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(ssp, "_backend_is_postgres", lambda: True)
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path / "me"))
    now = time.time()
    ssp._announce_and_find_peers(store, me="a" * 32, data_dir="/srv/r1", now=now - 60)
    ssp._announce_and_find_peers(store, me="d" * 32, data_dir="C:/scratch", now=now - 30)
    store.set(ssp.ACK_KEY, ["aaaaaaaa"])

    caplog.set_level(logging.INFO, logger=ssp.__name__)
    ssp.check_shared_store_peers(store)
    (line,) = _lines(caplog, logging.WARNING)
    assert "dddddddd" in line and "aaaaaaaa" not in line


def test_a_short_acknowledgement_silences_nothing(store):
    store.set(ssp.ACK_KEY, ["a", "aaaa", "aaaaaaaa"])
    assert ssp._acknowledged(store) == ["aaaaaaaa"]


def test_alone_is_one_info_line(store, tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(ssp, "_backend_is_postgres", lambda: True)
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path / "solo"))
    caplog.set_level(logging.INFO, logger=ssp.__name__)
    assert ssp.check_shared_store_peers(store) == []
    assert _lines(caplog, logging.WARNING) == []
    assert len(_lines(caplog, logging.INFO)) == 1


def test_sqlite_installs_do_not_run_it(store, monkeypatch):
    monkeypatch.setattr(ssp, "_backend_is_postgres", lambda: False)
    assert ssp.check_shared_store_peers(store) is None
    assert store.get_category(ssp.CATEGORY) == {}, "nothing recorded on SQLite"


def test_a_store_failure_is_logged_not_raised(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(ssp, "_backend_is_postgres", lambda: True)
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path / "d"))

    class _Down:
        def set(self, *a, **k):
            raise RuntimeError("Postgres pool unavailable")

    caplog.set_level(logging.WARNING, logger=ssp.__name__)
    assert ssp.check_shared_store_peers(_Down()) is None
    assert any("could not check" in ln for ln in _lines(caplog, logging.WARNING))


def test_negative_control_without_the_record_nobody_is_named(store, tmp_path, monkeypatch, caplog):
    """The WARNING comes from the recorded boot, not from anything else.

    With the first install's record never written — the pre-2026-09-25 state —
    the second install's boot names nobody, which is exactly how the dev clone
    and the live server shared one store for days without a line in either log.
    """
    monkeypatch.setattr(ssp, "_backend_is_postgres", lambda: True)
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path / "dev"))
    caplog.set_level(logging.INFO, logger=ssp.__name__)
    assert ssp.check_shared_store_peers(store) == []
    assert _lines(caplog, logging.WARNING) == []


# ── kazma doctor reads the same records, writes nothing ───────────────────


def test_doctor_names_an_unknown_sharer_without_writing(store, tmp_path, monkeypatch):
    from kazma_cli import doctor

    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path / "doctor-box"))
    ssp._announce_and_find_peers(store, me="e" * 32, data_dir="/srv/live", now=time.time() - 600)
    before = dict(store.get_category(ssp.CATEGORY))

    (status, text), = doctor._shared_store_lines(store)
    assert status == doctor.WARN
    assert "eeeeeeee" in text and "/srv/live" in text
    assert store.get_category(ssp.CATEGORY) == before, "doctor records nothing"
    assert not (tmp_path / "doctor-box" / "install_id").exists(), "and mints no id"


def test_doctor_is_quiet_about_acknowledged_replicas(store, tmp_path, monkeypatch):
    from kazma_cli import doctor

    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path / "r2"))
    ssp._announce_and_find_peers(store, me="e" * 32, data_dir="/srv/r1", now=time.time() - 600)
    store.set(ssp.ACK_KEY, ["eeeeeeee"])
    (status, text), = doctor._shared_store_lines(store)
    assert status == doctor.OK and "acknowledged" in text


def test_doctor_alone_is_ok(store, tmp_path, monkeypatch):
    from kazma_cli import doctor

    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path / "solo"))
    (status, _text), = doctor._shared_store_lines(store)
    assert status == doctor.OK
