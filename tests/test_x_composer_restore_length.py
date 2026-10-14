"""Composer CAS, restore pause, daylight-saving validation and weighted text."""

from __future__ import annotations

from dataclasses import replace
from zoneinfo import ZoneInfo

import pytest
from kazma_core.tenant_context import tenant_scope


def test_composer_is_scoped_to_tenant_and_actor_and_rejects_stale_save():
    from kazma_core.x_api.composer import load, save
    from kazma_core.x_api.publication_store import PublicationConflictError

    with tenant_scope("one"):
        assert load(actor="author")["revision"] == 0
        assert save(actor="author", expected_revision=0, content={"text": "Private"})["revision"] == 1
        with pytest.raises(PublicationConflictError):
            save(actor="author", expected_revision=0, content={"text": "Lost update"})
        assert load(actor="reviewer")["content"] == {}
        assert load(actor="author")["content"]["text"] == "Private"
    with tenant_scope("two"):
        assert load(actor="author")["content"] == {}


@pytest.mark.parametrize(("text", "weighted"), [
    ("hello", 5), ("مرحبا", 5), ("日本語", 6), ("😷", 2),
    ("👨‍👩‍👧‍👦", 2), ("👍🏽", 2), ("🇰🇼", 2), ("e\u0301", 1),
    ("https://example.com/" + "x" * 300, 23), ("example.com", 23),
])
def test_shared_weighted_character_corpus(text, weighted):
    from kazma_core.x_api.text_length import validate_text

    assert validate_text(text).weighted == weighted


def test_weighted_limit_and_invalid_unicode():
    from kazma_core.x_api.text_length import validate_text

    assert validate_text("日" * 140).valid
    assert not validate_text("日" * 141).valid
    assert not validate_text("a\uffff").valid
    assert not validate_text("\ud800").valid


def _us_dst_days() -> tuple[str, str]:
    """Next year's US spring-forward and fall-back days (2nd Sunday of March,
    1st Sunday of November).

    Re-pointed 2026-10-14 (Scheduled audit SCH-E03): a booking is refused past
    ``cron.timing.MAX_HORIZON`` (two years), so the fixed 2030 dates this test
    used now fail on the horizon before the DST check is reached.
    """
    from datetime import date, timedelta

    year = date.today().year + 1
    march = date(year, 3, 1)
    second_sunday = march + timedelta(days=(6 - march.weekday()) % 7 + 7)
    nov = date(year, 11, 1)
    first_sunday = nov + timedelta(days=(6 - nov.weekday()) % 7)
    return second_sunday.isoformat(), first_sunday.isoformat()


@pytest.mark.parametrize("kind", ["gap", "fold"])
def test_dst_gap_and_fold_require_unambiguous_time(monkeypatch, kind):
    from kazma_core.cron import scheduler
    from kazma_core.x_api.booking import _parse_when

    spring, fall = _us_dst_days()
    when = f"{spring}T02:30:00" if kind == "gap" else f"{fall}T01:30:00"
    # The booking reads the zone and its name in one settings read
    # (``resolve_cron_zone``, SCH-E10), so that is what the test pins.
    monkeypatch.setattr(scheduler, "resolve_cron_zone",
                        lambda: (ZoneInfo("America/New_York"), "America/New_York"))
    with pytest.raises(ValueError, match="does not exist|occurs twice"):
        _parse_when(when)
    assert _parse_when(f"{fall}T01:30:00-04:00") < _parse_when(f"{fall}T01:30:00-05:00")


def test_restore_pause_never_releases_old_queue(monkeypatch):
    import time

    from kazma_core.config_store import get_config_store
    from kazma_core.x_api import config
    from kazma_core.x_api.publication_store import get_publication_store
    from kazma_core.x_api.restore import pause_restored_publishing, restore_paused, resume_verified_account
    from kazma_core.x_api.schedule import get_x_scheduled_store

    store = get_publication_store()
    row = store.reserve(tenant_id="default", account_id="123", credential_revision="keys",
                        idempotency_key="restore", text="Old scheduled campaign", reply_to_id="", due_at=time.time() + 3600,
                        max_day=8, max_month=80, duplicate_days=30, origin="schedule")
    legacy = get_x_scheduled_store()
    old_id = legacy.add(text="Legacy campaign", fire_at=time.time() + 3600)
    pause_restored_publishing()
    assert restore_paused()
    assert store.get(row["id"], tenant_id="default")["state"] == "awaiting_approval"
    assert legacy.get(old_id).status == "held"
    with pytest.raises(ValueError):
        resume_verified_account()
    cfg = replace(config.get_x_config(), account_id="123", credentials=config.XCredentials("k", "s", "t", "ts"))
    monkeypatch.setattr(config, "get_x_config", lambda: cfg)
    from kazma_core.x_api.account_binding import record_account
    with pytest.raises(ValueError, match="after this restore"):
        resume_verified_account()
    record_account(cfg.credentials, {"id": "123", "username": "owner"})
    resume_verified_account()
    assert not get_config_store().get("system.x.restore_paused")
    assert store.get(row["id"], tenant_id="default")["state"] == "awaiting_approval"
    assert legacy.get(old_id).status == "held"


def test_failed_restore_guard_rolls_back_imported_publishing_database(tmp_path, monkeypatch):
    import hashlib
    import sqlite3
    import zipfile

    from kazma_core.migration import importer
    from kazma_core.migration.bundle import BUNDLE_VERSION, Manifest

    data = tmp_path / "data"
    data.mkdir()
    live = data / "x_scheduled.db"
    incoming = tmp_path / "incoming.db"
    for path, value in ((live, "original"), (incoming, "restored-unsafe")):
        conn = sqlite3.connect(path)
        try:
            conn.execute("CREATE TABLE marker (value TEXT)")
            conn.execute("INSERT INTO marker VALUES (?)", (value,))
            conn.commit()
        finally:
            conn.close()
    files = {"data/x_scheduled.db": incoming.read_bytes(), "meta.env": b"", "pathmap.json": b"{}"}
    manifest = Manifest(bundle_version=BUNDLE_VERSION,
                        file_hashes={key: hashlib.sha256(value).hexdigest() for key, value in files.items()})
    bundle = tmp_path / "restore.zip"
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr("manifest.json", manifest.to_json())
        for name, content in files.items():
            archive.writestr(name, content)

    def refuse_guard(swapped, report):
        assert "x_scheduled.db" in swapped
        report.error("X publishing restore guard failed: settings are read-only")
        return False

    monkeypatch.setattr("kazma_core.paths.data_dir", lambda: data)
    monkeypatch.setattr(importer, "_live_server_detected", lambda: (False, ""))
    monkeypatch.setattr(importer, "_guard_restored_x", refuse_guard)
    report = importer.import_bundle(bundle, target_workspace_root=str(tmp_path))
    assert not report.ok
    assert not report.files_restored
    conn = sqlite3.connect(live)
    try:
        assert conn.execute("SELECT value FROM marker").fetchone()[0] == "original"
    finally:
        conn.close()
