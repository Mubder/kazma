"""The failures that report success.

Every bug in here shares one shape: the system kept working, kept saying
"complete", and stopped protecting the data. That is worse than a crash,
because a crash gets fixed the same day.

Three real incidents, one test each:

* A Google *service account* can list a shared Drive folder in
  milliseconds and cannot write a single byte to it -- it has no storage
  quota of its own. restic takes a lock before it will even LIST, so on
  such a remote every command retried for fifteen minutes and looked like
  a hang. Two 600-second probes were burned before the cause was visible.
* A missing passphrase logged one INFO line and skipped every snapshot.
  It did that for four hours while backups reported "complete", because
  the local dump really had been written.
* The firing ledger read only the application log, so it reported ZERO
  guard restarts -- for mechanisms that log, by design, to their own file
  so the app's logging config cannot silence them.
"""

from __future__ import annotations

import re
import subprocess
from types import SimpleNamespace

import pytest
from kazma_core.backup import restic_repo as rr
from kazma_core.observability import firing_ledger as fl

_QUOTA_ERR = (
    "googleapi: Error 403: Service Accounts do not have storage quota. "
    "Leverage shared drives, storageQuotaExceeded"
)


@pytest.fixture(autouse=True)
def _clear_probe_cache():
    rr._write_probe_cache.clear()
    yield
    rr._write_probe_cache.clear()


# -- a remote that reads but will not write ------------------------------

def test_local_repo_is_not_probed(tmp_path):
    """Only rclone remotes cost a probe; a local path must stay free."""
    ok, why = rr.remote_writable(str(tmp_path / "repo"))
    assert ok and why == ""


def test_read_only_remote_is_named_not_retried(monkeypatch):
    calls: list[list[str]] = []

    def fake_run(args, **kw):
        calls.append(args)
        return SimpleNamespace(returncode=1, stdout="", stderr=_QUOTA_ERR)

    monkeypatch.setattr(subprocess, "run", fake_run)
    ok, why = rr.remote_writable("rclone:drive:restic")

    assert not ok
    # The operator has to be able to act on this without reading Google's
    # API docs, so the message must say what to do, not just what failed.
    assert "READ-ONLY" in why
    assert "service account" in why.lower()
    assert "NOT being written" in why
    # The probe deletes nothing it did not create: one failed rcat, and no
    # deletefile, because there is nothing to delete.
    assert len(calls) == 1


def test_probe_result_is_cached(monkeypatch):
    n = {"count": 0}

    def fake_run(args, **kw):
        n["count"] += 1
        return SimpleNamespace(returncode=1, stdout="", stderr=_QUOTA_ERR)

    monkeypatch.setattr(subprocess, "run", fake_run)
    rr.remote_writable("rclone:drive:restic")
    rr.remote_writable("rclone:drive:restic")
    assert n["count"] == 1, "a 90s probe per restic call is its own outage"


def test_restic_refuses_to_run_against_a_read_only_remote(monkeypatch):
    """The whole point: fail in seconds, loudly, instead of hanging."""
    raised: list[tuple] = []
    monkeypatch.setattr(rr, "remote_writable",
                        lambda repo, **kw: (False, "READ-ONLY: no quota"))
    monkeypatch.setattr(rr, "alert_read_only_remote",
                        lambda repo, why: bool(raised.append((repo, why))))
    monkeypatch.setattr(rr, "restic_available", lambda: True)

    def explode(*a, **k):  # pragma: no cover -- must never be reached
        raise AssertionError("restic was invoked against a read-only remote")

    monkeypatch.setattr(subprocess, "run", explode)

    res = rr._run(["snapshots"], "rclone:drive:restic", "pw", action="snapshots")
    assert not res.ok
    assert "READ-ONLY" in res.error
    assert raised, "a silently unwritten offsite copy must alert"


# -- a missing passphrase with a repository already on disk --------------

def test_no_passphrase_and_no_repository_is_not_an_incident(monkeypatch, tmp_path):
    """A fresh install has nothing at stake yet. Alerting here would
    train the operator to ignore the alert that does matter."""
    monkeypatch.setattr(rr, "repo_paths",
                        lambda: {"local": str(tmp_path / "nope"), "remote": ""})
    assert rr.alert_missing_password("test") is False


def test_no_passphrase_with_a_repository_is_critical(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "config").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(rr, "repo_paths",
                        lambda: {"local": str(repo), "remote": ""})

    sent: list[dict] = []
    import kazma_core.observability.ops_alerts as ops
    monkeypatch.setattr(ops, "alert",
                        lambda key, title, body, **kw: sent.append(
                            {"key": key, "title": title, "body": body, **kw}))

    assert rr.alert_missing_password("universal backup") is True
    assert len(sent) == 1
    a = sent[0]
    assert a["severity"] == "critical"
    # It has to say both halves of the damage: new data unprotected, and
    # the history already stored no longer readable.
    assert "SKIPPED" in a["title"]
    assert "unprotected" in a["body"]
    assert "decrypt" in a["body"]


# -- a ledger that reads one file and reports on many --------------------

def test_ledger_counts_events_from_the_guard_log(tmp_path, monkeypatch):
    """The regression that made the ledger worse than nothing.

    The guard writes to its own file on purpose. A ledger that reads only
    the application log reports "never fired" for mechanisms that fired
    that same evening -- and a false silence in the one report whose job
    is to find silence is the audit's finding wearing a new hat.
    """
    app = tmp_path / "kazma.log"
    app.write_text(
        '{"timestamp": "2999-01-01T00:00:00+00:00", '
        '"message": "[universal-backup] complete: 25 DBs"}\n',
        encoding="utf-8")
    guard = tmp_path / "guard.log"
    guard.write_text(
        '{"ts": "2999-01-01T00:00:00+00:00", "event": "guard.restarting"}\n'
        '{"ts": "2999-01-01T00:00:01+00:00", "event": "port.stale_before_spawn"}\n',
        encoding="utf-8")

    monkeypatch.setattr(fl, "_log_paths", lambda: [app, guard])
    counts = {e.mechanism: e.count for e in fl.scan_log(hours=1e9).entries}

    assert counts["universal backup"] == 1
    assert counts["guard restart"] == 1, "guard.log was not scanned"
    assert counts["pre-spawn port clearance"] == 1


def test_ledger_signatures_match_lines_the_code_emits():
    """A pattern that cannot match is a dial welded to zero.

    The first version watched for "[universal-backup] wrote", a string
    that appears nowhere in the codebase; the real line says "complete:".
    """
    samples = {
        "universal backup": "[universal-backup] complete: 25 DBs, 952.9 MB",
        "guard restart": '{"event": "guard.restarting", "restarts": 2}',
        "foreign server detection": '{"event": "child.foreign_server_holds_port"}',
        "health-gated restart": '{"event": "health.failed", "detail": "500"}',
        "daily digest": "[digest] daily digest dispatched (812 chars)",
        "install restore": "[restore] RESTORED: 9/9 steps, generation 1787",
    }
    by_name = {s.mechanism: s for s in fl.FIRING_SIGNATURES}
    for mechanism, line in samples.items():
        sig = by_name[mechanism]
        assert re.search(sig.pattern, line, re.IGNORECASE), mechanism


def test_ledger_does_not_call_a_watched_mechanism_blind():
    """The manifest writes "foreign-server detection"; the ledger writes
    it with a space. A raw substring test called that mechanism
    unobservable while it was being counted two lines above."""
    report = fl.build_report(hours=0.001)
    blind = [e for e in report.entries if e.mechanism == "(no firing signature)"]
    if blind:
        assert "foreign-server detection" not in blind[0].note


# -- a report nobody runs ------------------------------------------------

def test_ledger_sweep_is_registered_at_boot_and_held():
    """The ledger shipped unscheduled for its first day.

    That is this module's own finding happening to this module: a
    mechanism that exists, imports, passes its tests, and never runs. The
    task must also be held, because an unreferenced asyncio task can be
    garbage-collected mid-loop.
    """
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1]
           / "kazma-core" / "kazma_core" / "memory"
           / "worker_bootstrap.py").read_text(encoding="utf-8")
    assert "_start_firing_ledger_scheduler()" in src
    fn = src.split("def _start_firing_ledger_scheduler()", 1)[1][:1500]
    assert "_scheduler_tasks.add(task)" in fn


def test_ledger_sweep_sleeps_before_its_first_report():
    """A report on every boot fires hardest during an incident, when the
    operator needs another message least."""
    import inspect

    src = inspect.getsource(fl.ledger_scheduler)
    assert src.index("asyncio.sleep") < src.index("run_weekly_sweep")
