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
        "health-gated restart": '{"event": "guard.restarting", "reason": "unhealthy (500)"}',
        "probe miss tolerated": '{"event": "health.recovered", "after_failures": 1}',
        "daily digest": "[digest] daily digest dispatched (812 chars)",
        "install restore": "[restore] RESTORED: 9/9 steps, generation 1787",
    }
    by_name = {s.mechanism: s for s in fl.FIRING_SIGNATURES}
    for mechanism, line in samples.items():
        sig = by_name[mechanism]
        assert re.search(sig.pattern, line, re.IGNORECASE), mechanism
    # A missed probe answered by the next one is not a restart: the ledger
    # reported 430 "health-gated restarts" in a week that had none.
    blip = '{"event": "health.failed", "detail": "probe error: [WinError 10054]"}'
    assert not re.search(by_name["health-gated restart"].pattern, blip, re.IGNORECASE)
    exited = '{"event": "guard.restarting", "reason": "process exited (code 1)"}'
    assert not re.search(by_name["health-gated restart"].pattern, exited, re.IGNORECASE)


# -- every signature is derived from a line the code really emits ---------

_REPO = __import__("pathlib").Path(__file__).resolve().parents[1]
_LOG_METHODS = {"debug", "info", "warning", "error", "critical", "exception"}


_SLOT = "\x00"


def _render(node, fill: str = "1") -> str | None:
    """A logger format string as it would print, placeholders filled with
    ``fill`` ("1" satisfies both \\d and \\w in a pattern)."""
    import ast

    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return re.sub(r"%[-#0 +]*\d*(?:\.\d+)?[sdrfix]", fill, node.value)
    if isinstance(node, ast.JoinedStr):
        return "".join(
            v.value if isinstance(v, ast.Constant) else fill for v in node.values
        )
    return None


def _real_summaries() -> list[str]:
    """What the result objects logged as ``"[tag] %s", res.summary()`` print."""
    from kazma_core.backup.restore import RestoreResult
    from kazma_core.backup.restore_drill import DrillResult

    out = []
    for status in (True, False, None):
        d = DrillResult(backup_dir="(deep)")
        d.add("check", status, "detail")
        out.append(d.summary())
    for ok in (True, False):
        r = RestoreResult(ok=ok, target="t", generation=1)
        r.add("step", ok)
        out.append(r.summary())
    return out


def _func_name(node) -> str:
    import ast

    f = node.func
    return f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else ""


def _emitted_app_lines(roots) -> list[str]:
    """Every line the code can log: logger format strings, ops-alert keys
    (alert() logs "[ops_alert] <key> | <title>"), and result summaries."""
    import ast

    summaries = _real_summaries()
    lines: list[str] = []
    for root in roots:
        for path in root.rglob("*.py"):
            if "_tests" in path.parts or "tests" in path.parts or "__pycache__" in path.parts:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not node.args:
                    continue
                name = _func_name(node)
                if name in _LOG_METHODS and isinstance(node.func, ast.Attribute):
                    text = _render(node.args[0])
                    if text:
                        lines.append(text)
                    if any(
                        isinstance(a, ast.Call) and _func_name(a) == "summary"
                        for a in node.args[1:]
                    ):
                        slotted = _render(node.args[0], fill=_SLOT) or ""
                        lines += [slotted.replace(_SLOT, s, 1).replace(_SLOT, "1") for s in summaries]
                elif name in ("alert", "_alert"):
                    key = node.args[0]
                    if isinstance(key, ast.Constant) and isinstance(key.value, str):
                        lines.append(f"[ops_alert] {key.value} | 1")
    return lines


def _guard_events() -> set[str]:
    import ast

    tree = ast.parse((_REPO / "scripts" / "service" / "kazma_guard.py").read_text(encoding="utf-8"))
    events: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
            and re.fullmatch(r"[a-z_]+\.[a-z_]+", node.args[1].value)
        ):
            events.add(node.args[1].value)
    return events


def _unmatched_signatures(signatures, app_lines, guard_events) -> list[str]:
    missing = []
    for sig in signatures:
        rx = re.compile(sig.pattern, re.IGNORECASE)
        if '"event":' in sig.pattern:
            head = sig.pattern.split(".*", 1)[0]  # the event clause
            if not any(re.search(head, f'"event": "{e}"', re.IGNORECASE) for e in guard_events):
                missing.append(sig.mechanism)
        elif not any(rx.search(line) for line in app_lines):
            missing.append(sig.mechanism)
    return missing


def test_every_ledger_signature_matches_a_line_the_code_emits():
    """Derived from the source, not from samples a person typed.

    Three signatures had drifted from their emitters by 2026-09-23 and each
    made the weekly report lie: "[ops-alert]" (the code says "[ops_alert]"),
    "[restore-drill] FAIL:" (the deep drill says "deep: FAIL:"), and restic
    maintenance, which logged only on failure. The hand-written samples in
    the test above all passed throughout.
    """
    app_lines = _emitted_app_lines(
        [p for p in _REPO.glob("kazma-*/kazma_*") if p.is_dir()] + [_REPO / "scripts"]
    )
    assert len(app_lines) > 1000, "the source walk found almost nothing"
    missing = _unmatched_signatures(fl.FIRING_SIGNATURES, app_lines, _guard_events())
    assert not missing, f"signatures no code emits (copy them from the emitting line): {missing}"


def test_the_signature_gate_catches_a_drifted_pattern():
    """Negative control (§28): the pre-fix operator-alerting pattern, and a
    guard event that does not exist."""
    app_lines = ["[ops_alert] 1 | 1", "[universal-backup] complete: 1"]
    stale = [
        fl.Signature("operator alerting", r"\[ops-alert\]|\[alert\]"),
        fl.Signature("renamed guard event", r'"event": "guard\.restarted"'),
        fl.Signature("universal backup", r"\[universal-backup\] complete:"),
    ]
    assert _unmatched_signatures(stale, app_lines, {"guard.restarting"}) == [
        "operator alerting", "renamed guard event",
    ]


def test_health_gated_signature_matches_the_supervisors_real_reason(tmp_path):
    """The reason clause is a runtime value, so check it end to end: the
    reason string _supervise returns, written by the guard's own writer."""
    import ast
    import importlib.util

    src = (_REPO / "scripts" / "service" / "kazma_guard.py").read_text(encoding="utf-8")
    sup = next(
        n for n in ast.walk(ast.parse(src))
        if isinstance(n, ast.FunctionDef) and n.name == "_supervise"
    )
    reasons = [
        _render(n) for n in ast.walk(sup)
        if isinstance(n, ast.Return) and isinstance(n.value, ast.JoinedStr)
        for n in [n.value]
    ]
    unhealthy = [r for r in reasons if r and r.startswith("unhealthy")]
    assert unhealthy, "_supervise no longer returns an 'unhealthy (...)' reason"

    spec = importlib.util.spec_from_file_location("kazma_guard_for_ledger", _REPO / "scripts" / "service" / "kazma_guard.py")
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    log = guard.GuardLog(tmp_path / "guard.log")
    log("warn", "guard.restarting", reason=unhealthy[0], in_s=5, restarts=1)
    line = (tmp_path / "guard.log").read_text(encoding="utf-8")
    sig = next(s for s in fl.FIRING_SIGNATURES if s.mechanism == "health-gated restart")
    assert re.search(sig.pattern, line, re.IGNORECASE), line


def test_clean_restic_maintenance_leaves_the_line_the_ledger_counts(monkeypatch, caplog):
    """It logged only on failure: 23 clean runs in a week, and the report
    said "restic maintenance: silent"."""
    import asyncio
    import logging

    import kazma_core.backup.restic_repo as repo_mod
    from kazma_core.memory import worker_bootstrap as wb

    ok = SimpleNamespace(ok=True, error="")
    monkeypatch.setattr(repo_mod, "restic_available", lambda: True)
    monkeypatch.setattr(repo_mod, "ensure_password", lambda **_: ("pw", False))
    monkeypatch.setattr(repo_mod, "repo_paths", lambda: {"local": "L", "remote": ""})
    for fn in ("unlock_stale", "forget_prune", "check"):
        monkeypatch.setattr(repo_mod, fn, lambda *a, **k: ok)
    with caplog.at_level(logging.INFO):
        assert asyncio.run(wb._handle_restic_maintenance({})) is True
    sig = next(s for s in fl.FIRING_SIGNATURES if s.mechanism == "restic maintenance")
    hits = [r.getMessage() for r in caplog.records if re.search(sig.pattern, r.getMessage())]
    assert hits == ["[restic] maintenance ok: local (forget --prune, check)"]


def test_ledger_reads_rotated_logs(tmp_path, monkeypatch):
    """The app log rotates at midnight; the report is weekly. Reading only
    kazma.log saw one day and called six days of backups silent."""
    import kazma_core.paths as paths

    home = tmp_path / ".kazma"
    home.mkdir()
    (home / "kazma.log").write_text("", encoding="utf-8")
    (home / "kazma.log.2026-09-20").write_text(
        '{"timestamp": "2999-01-01T00:00:00+00:00", '
        '"message": "[universal-backup] complete: 25 DBs"}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(paths, "user_home", lambda: home)
    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path / "kazma-data")
    assert home / "kazma.log.2026-09-20" in fl._log_paths()
    counts = {e.mechanism: e.count for e in fl.scan_log(hours=1e9).entries}
    assert counts["universal backup"] == 1


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
