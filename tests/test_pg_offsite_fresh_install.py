"""A fresh install has no restic repository to copy the Postgres dump into.

On the drill VM (2026-11-06), a fresh install with Postgres and no restic:
every Postgres dump's offsite copy (``native_pg_offsite``) failed three
times and was left on the Dashboard as a failed task ("handler returned
False"), with nothing the owner could fix by retrying. The copy counted the
default local repository path, which every install has, as a destination.

``restic_repo.configured_repos`` is the one answer: the offsite repository
when it is set, the local one once restic is set up here (its repository
exists, or a passphrase does). With none, there is nothing to copy. Where
restic is set up, a copy that cannot be made still fails and alerts.
"""

from __future__ import annotations

import asyncio

import pytest

from kazma_core.backup import restic_repo
from kazma_core.memory import worker_bootstrap as wb


@pytest.fixture
def machine(tmp_path, monkeypatch):
    """A machine with no restic, no passphrase, no offsite repository, and
    one Postgres dump on disk."""
    local = tmp_path / "backups" / "restic"
    monkeypatch.setattr(restic_repo, "repo_paths", lambda: {"local": str(local), "remote": ""})
    monkeypatch.delenv("KAZMA_RESTIC_PASSWORD", raising=False)
    monkeypatch.setattr(restic_repo, "password_file", lambda: tmp_path / ".kazma" / "restic.pass")
    monkeypatch.setattr(restic_repo, "restic_available", lambda: False)
    alerts: list[list[str]] = []
    monkeypatch.setattr(wb, "_alert_pg_offsite", lambda failed: alerts.append(list(failed)))
    dump = tmp_path / "backups" / "pg" / "pg_shared_1.dump"
    dump.parent.mkdir(parents=True)
    dump.write_bytes(b"PGDMP fake")
    return {"local": local, "dump": dump, "alerts": alerts, "tmp": tmp_path}


def _copy(dump) -> bool:
    return asyncio.run(wb._handle_native_pg_offsite({"path": str(dump)}))


def test_a_fresh_install_has_nothing_to_copy_into(machine):
    assert restic_repo.configured_repos() == {}
    assert wb._snapshot_pg_to_restic(machine["dump"]) == []
    assert _copy(machine["dump"]) is True, "nothing to copy is done, not failed"
    assert machine["alerts"] == []


def test_the_old_rule_failed_the_copy(machine, monkeypatch):
    """Negative control: the default local path counted as a destination."""
    monkeypatch.setattr(restic_repo, "configured_repos",
                        lambda: {n: r for n, r in restic_repo.repo_paths().items() if r})
    assert _copy(machine["dump"]) is False
    assert machine["alerts"] == [["local"]]


def test_a_set_up_repository_without_restic_still_fails_and_alerts(machine):
    """restic set up here (its repository exists) and gone: a real failure."""
    machine["local"].mkdir(parents=True)
    (machine["local"] / "config").write_text("x", encoding="utf-8")
    assert restic_repo.configured_repos() == {"local": str(machine["local"])}
    assert _copy(machine["dump"]) is False
    assert machine["alerts"] == [["local"]]


def test_a_passphrase_alone_means_restic_is_set_up(machine):
    """A repository that vanished while its passphrase stayed still counts."""
    pass_file = machine["tmp"] / ".kazma" / "restic.pass"
    pass_file.parent.mkdir(parents=True)
    pass_file.write_text("demo-passphrase", encoding="utf-8")
    assert restic_repo.configured_repos() == {"local": str(machine["local"])}
    assert _copy(machine["dump"]) is False


def test_an_offsite_repository_alone_is_a_destination(machine, monkeypatch):
    monkeypatch.setattr(restic_repo, "repo_paths",
                        lambda: {"local": str(machine["local"]), "remote": "rclone:r2:kazma/restic"})
    assert restic_repo.configured_repos() == {"remote": "rclone:r2:kazma/restic"}
    assert _copy(machine["dump"]) is False, "restic missing with an offsite repository set"
