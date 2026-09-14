"""The drill must run, and must check the things that lose data.

Two findings from a live install, 2026-09-14:

**It never ran.** Across three days of logs: 34 "restore drill scheduler
started", zero results. `DRILL_INTERVAL_HOURS` was 168 and the loop slept a
full interval BEFORE its first run, so on a host that restarts every few hours
the clock reset every time. The module's own docstring had already named this
sin for its predecessor — "a mechanism nobody runs asserts a property nobody
has measured" — and the scheduler added to fix it reproduced it.

**It checked readability, not recoverability.** Every file opened and passed
`PRAGMA integrity_check`. Nothing asked whether the backup's own vault key
still decrypts its own vault, whether a database was missing entirely, or
whether a "valid" database had any tables in it.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from kazma_core.backup import restore_drill


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path, monkeypatch):
    from kazma_core.config_store import ConfigStore

    store = ConfigStore(
        db_path=str(tmp_path / "cfg.db"), yaml_path=str(tmp_path / "none.yaml")
    )
    monkeypatch.setattr(
        "kazma_core.config_store.get_config_store", lambda: store, raising=False
    )
    return store


class TestTheCadenceSurvivesARestart:
    def test_it_is_daily(self):
        assert restore_drill.DRILL_INTERVAL_HOURS == 24.0

    def test_a_drill_is_due_when_none_has_ever_run(self):
        assert restore_drill.drill_is_due() is True

    def test_it_is_not_due_again_straight_away(self):
        now = 1_800_000_000.0
        restore_drill._record_drill_run(now)
        assert restore_drill.drill_is_due(now + 60) is False

    def test_it_is_due_once_the_interval_has_passed(self):
        now = 1_800_000_000.0
        restore_drill._record_drill_run(now)
        assert restore_drill.drill_is_due(now + 24 * 3600 + 1) is True

    def test_a_restart_does_not_reset_the_clock(self):
        """The whole bug. Under the old design a process restarting every few
        hours never reached its first run; the schedule now counts from the
        last completed drill, which is stored."""
        now = 1_800_000_000.0
        restore_drill._record_drill_run(now)
        # Simulate any number of restarts: the state is external to the loop.
        for _ in range(20):
            assert restore_drill.drill_is_due(now + 3600) is False
        assert restore_drill.drill_is_due(now + 25 * 3600) is True

    def test_the_scheduler_waits_minutes_not_a_week_before_its_first_run(self):
        assert restore_drill._DRILL_FIRST_DELAY_SECONDS <= 900


def _backup(tmp_path, *, vault_key: str | None, stored_key: str = "K" * 32,
            with_tables: bool = True) -> Path:
    """A backup directory shaped like a real one."""
    import os

    d = tmp_path / "backup"
    (d / "dbs").mkdir(parents=True)
    (d / "manifest.json").write_text(
        '{"version": 1, "databases": {"ok": 1, "failed": 0}}', encoding="utf-8"
    )
    if vault_key is not None:
        (d / ".env").write_text(f"KAZMA_VAULT_KEY={vault_key}\n", encoding="utf-8")

    con = sqlite3.connect(str(d / "dbs" / "kazma.db"))
    try:
        if with_tables:
            con.execute("CREATE TABLE t (id INTEGER)")
        con.commit()
    finally:
        con.close()

    previous = os.environ.get("KAZMA_VAULT_KEY")
    os.environ["KAZMA_VAULT_KEY"] = stored_key
    try:
        from kazma_core.security.vault import SecretVault

        vault = SecretVault(db_path=str(d / "dbs" / "vault.db"))
        vault.store("cfg:providers.list.groq.api_key", "gsk_SECRET", category="config")
    finally:
        if previous is None:
            os.environ.pop("KAZMA_VAULT_KEY", None)
        else:
            os.environ["KAZMA_VAULT_KEY"] = previous
    return d


def _check(res, name):
    return next((c for c in res.checks if c["check"] == name), None)


class TestItProvesTheSecretsAreRecoverable:
    def test_a_matching_key_decrypts_the_backed_up_vault(self, tmp_path):
        d = _backup(tmp_path, vault_key="K" * 32, stored_key="K" * 32)
        res = restore_drill.verify_backup(d)
        chk = _check(res, "vault:decrypt")
        assert chk and chk["ok"], chk

    def test_a_stale_key_is_caught(self, tmp_path):
        """The failure that matters: every file is perfect and nothing in the
        backup can be read back."""
        d = _backup(tmp_path, vault_key="W" * 32, stored_key="K" * 32)
        res = restore_drill.verify_backup(d)
        chk = _check(res, "vault:decrypt")
        assert chk and not chk["ok"], chk
        assert res.ok is False

    def test_a_missing_key_is_caught(self, tmp_path):
        d = _backup(tmp_path, vault_key=None)
        d_env = d / ".env"
        d_env.write_text("SOMETHING_ELSE=1\n", encoding="utf-8")
        res = restore_drill.verify_backup(d)
        chk = _check(res, "vault:key")
        assert chk and not chk["ok"], chk


class TestItNoticesWhatIsMissingOrEmpty:
    def test_a_database_with_no_tables_fails(self, tmp_path):
        """`integrity_check` passes on an empty file — structurally perfect
        and completely worthless."""
        d = _backup(tmp_path, vault_key="K" * 32, with_tables=False)
        res = restore_drill.verify_backup(d)
        chk = _check(res, "sqlite:kazma.db")
        assert chk and not chk["ok"], chk
        assert "no tables" in chk["detail"]

    def test_a_database_the_manifest_claims_but_did_not_save_is_reported(self, tmp_path):
        """The manifest is held to its own word.

        Checked against the manifest rather than the live data directory: the
        drill can be pointed at any backup, including one from another install
        or another day, so "what is on this machine right now" is not a
        question a backup can be asked. A first version did compare the two and
        flagged 238 databases — the 212 per-repo code-index stores among them.
        """
        d = _backup(tmp_path, vault_key="K" * 32)
        (d / "manifest.json").write_text(
            '{"version": 1, "databases": {"ok": 2, "failed": 0, "items": '
            '[{"path": "kazma.db"}, {"path": "snapshots.db"}]}}',
            encoding="utf-8",
        )
        res = restore_drill.verify_backup(d)
        chk = _check(res, "complete:databases")
        assert chk and not chk["ok"], chk
        assert "snapshots.db" in chk["detail"]

    def test_a_manifest_that_matches_disk_passes(self, tmp_path):
        d = _backup(tmp_path, vault_key="K" * 32)
        (d / "manifest.json").write_text(
            '{"version": 1, "databases": {"ok": 1, "failed": 0, "items": '
            '[{"path": "kazma.db"}]}}',
            encoding="utf-8",
        )
        res = restore_drill.verify_backup(d)
        chk = _check(res, "complete:databases")
        assert chk and chk["ok"], chk
