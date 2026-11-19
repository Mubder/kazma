"""``kazma migrate import`` holds Kazma through its guard (2026-11-19).

Restoring the old Kazma after the PC reset, the import refused a running
Kazma and left the owner to pause the guard and resume it by hand, then
refused again for two minutes over the heartbeat the stopped server had
stamped. Now the command checks the whole import first (a dry run: a bundle
that would be refused never stops Kazma), stops Kazma once no chat turn runs
(``setup._MoveHold``, the database and engine moves' hold), imports with
``server_stopped``, and starts Kazma again whatever the outcome (a failed
import left the data as it was). ``--force`` imports without the hold, as
before. And a bundle it cannot read is one sentence, not a traceback.
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from kazma_cli import migrate


class _Hold:
    def __init__(self, *, running=True, refusal=None, stops=True, log=None):
        self.held = False
        self._refusal = refusal
        self._stops = stops
        self.running = running
        self.log = log if log is not None else []

    def adopt_leftover(self):
        self.log.append("adopt")
        return False

    def refusal(self):
        return self._refusal

    def stop(self):
        self.log.append("stop")
        if self._stops and self.running:
            self.held = True
        return self._stops

    def resume(self):
        self.log.append("resume")
        self.held = False
        return "up"


def _report(*, errors=(), ok=True):
    return SimpleNamespace(ok=ok, errors=list(errors), warnings=[], vault_status="", vault_message="",
                           rows_rewritten={}, files_restored=[], staging_path="", backup_path="",
                           dry_run=False)


@pytest.fixture
def bundle(tmp_path):
    path = tmp_path / "b.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("manifest.json", "{}")
    return path


@pytest.fixture
def calls(monkeypatch):
    log: list = []

    def fake_import(bundle_path, **kw):
        log.append(("import", kw.get("dry_run", False), kw.get("server_stopped", False)))
        return fake_import.reports.pop(0)

    fake_import.reports = []
    monkeypatch.setattr("kazma_core.migration.import_bundle", fake_import)
    return SimpleNamespace(log=log, importer=fake_import)


def _run(args, hold, monkeypatch):
    monkeypatch.setattr(migrate, "_import_hold", lambda: hold)
    try:
        migrate.run(["import", *args])
    except SystemExit as exc:
        return exc.code
    return 0


def test_the_import_holds_kazma_and_starts_it_again(bundle, calls, monkeypatch, capsys):
    calls.importer.reports = [_report(), _report()]
    hold = _Hold(log=calls.log)
    assert _run([str(bundle), "--workspace", "x"], hold, monkeypatch) == 0
    # The whole check first, Kazma untouched; then the hold, the import told
    # the server is held, and Kazma started again.
    assert calls.log == [("import", True, False), "adopt", "stop", ("import", False, True), "resume"]
    assert "Kazma is running again." in capsys.readouterr().out


def test_a_bundle_the_check_refuses_never_stops_kazma(bundle, calls, monkeypatch, capsys):
    calls.importer.reports = [_report(errors=["Re-run with --reset-vault-key"], ok=False)]
    hold = _Hold(log=calls.log)
    assert _run([str(bundle), "--workspace", "x"], hold, monkeypatch) == 1
    assert calls.log == [("import", True, False)]
    assert "Kazma was not stopped" in capsys.readouterr().out


def test_a_failed_import_still_starts_kazma(bundle, calls, monkeypatch):
    calls.importer.reports = [_report(), _report(errors=["document store restore failed"], ok=False)]
    hold = _Hold(log=calls.log)
    assert _run([str(bundle), "--workspace", "x"], hold, monkeypatch) == 1
    assert calls.log[-1] == "resume"


def test_an_import_that_raises_still_starts_kazma(bundle, calls, monkeypatch):
    def boom(bundle_path, **kw):
        calls.log.append(("import", kw.get("dry_run", False), kw.get("server_stopped", False)))
        if not kw.get("dry_run"):
            raise RuntimeError("disk full")
        return _report()

    monkeypatch.setattr("kazma_core.migration.import_bundle", boom)
    hold = _Hold(log=calls.log)
    assert _run([str(bundle), "--workspace", "x"], hold, monkeypatch) == 1
    assert calls.log[-1] == "resume"


def test_a_kazma_nothing_can_stop_is_refused_before_anything(bundle, calls, monkeypatch, capsys):
    calls.importer.reports = [_report()]
    hold = _Hold(refusal="Kazma is running and no guard supervises it", log=calls.log)
    assert _run([str(bundle), "--workspace", "x"], hold, monkeypatch) == 1
    assert calls.log == [("import", True, False), "adopt"]
    assert "no guard supervises it" in capsys.readouterr().out


def test_a_stop_the_guard_could_not_make_changes_nothing(bundle, calls, monkeypatch):
    calls.importer.reports = [_report()]
    hold = _Hold(stops=False, log=calls.log)
    assert _run([str(bundle), "--workspace", "x"], hold, monkeypatch) == 1
    assert ("import", False, True) not in calls.log and ("import", False, False) not in calls.log


def test_force_imports_without_the_hold(bundle, calls, monkeypatch):
    calls.importer.reports = [_report(), _report()]
    hold = _Hold(log=calls.log)
    assert _run([str(bundle), "--workspace", "x", "--force"], hold, monkeypatch) == 0
    assert calls.log == [("import", True, False), ("import", False, False)]


def test_a_kazma_not_running_is_imported_without_a_hold(bundle, calls, monkeypatch):
    calls.importer.reports = [_report(), _report()]
    hold = _Hold(running=False, log=calls.log)
    assert _run([str(bundle), "--workspace", "x"], hold, monkeypatch) == 0
    # Nothing to hold: the import's own heartbeat check stays on.
    assert ("import", False, False) in calls.log and "resume" not in calls.log


def test_the_hold_is_the_moves_hold_under_its_own_reason():
    hold = migrate._import_hold()
    assert type(hold).__name__ == "_MoveHold" and hold.reason == migrate._IMPORT_PAUSE_REASON


# ── a bundle it cannot read ─────────────────────────────────────────────────


def test_an_unreadable_bundle_is_one_sentence(bundle, monkeypatch, capsys):
    real_open = Path.open

    def denied(self, *args, **kwargs):
        if self == bundle:
            raise PermissionError(13, "Permission denied")
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", denied)
    for sub in ("verify", "import"):
        with pytest.raises(SystemExit) as stop:
            migrate.run([sub, str(bundle)])
        assert stop.value.code == 1
        out = capsys.readouterr().out
        assert "permission denied" in out and "robocopy" in out and "Traceback" not in out


def test_control_zipfile_raises_on_a_bundle_it_cannot_read(bundle, monkeypatch):
    """What ``verify`` met on 2026-11-19: zipfile's own PermissionError, which
    reached the owner as a traceback."""
    import builtins
    import io

    real = io.open

    def denied(file, *args, **kwargs):
        if str(file) == str(bundle):
            raise PermissionError(13, "Permission denied")
        return real(file, *args, **kwargs)

    monkeypatch.setattr(io, "open", denied)
    monkeypatch.setattr(builtins, "open", denied)
    with pytest.raises(PermissionError):
        zipfile.ZipFile(bundle, "r")
