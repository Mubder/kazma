"""The weekly resilience report says everything, problems first (2026-09-30).

The report for the week to 2026-09-29 read "15 of 29 mechanisms fired" and
named eight of them: the first eight in declaration order. Among the seven it
left out were six health-gated restarts and thirty event-loop stalls; the
"29" counted a placeholder row; and 141 of its "149 alerts" were one alert
held back 140 times by its cooldown. These tests hold the report to: every
mechanism named, what needs a look first, alerts counted as sent, restart
reasons and alert keys tallied, scheduled jobs measured against their
schedule, and the stall dumps read for what blocked the loop.
"""

from __future__ import annotations

import json
import os
import time

import pytest

from kazma_core.observability import firing_ledger as fl


def _stamp(offset_s: float = -60.0) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(time.time() + offset_s))


def _app_line(message: str) -> str:
    return json.dumps({"timestamp": _stamp(), "level": "INFO", "message": message}) + "\n"


def _guard_line(event: str, **fields) -> str:
    return json.dumps({"ts": _stamp(), "level": "warn", "event": event, **fields}) + "\n"


@pytest.fixture
def week(tmp_path, monkeypatch):
    """A week of logs and stall dumps in a temporary install."""
    app = tmp_path / "kazma.log"
    guard = tmp_path / "guard.log"
    dumps = tmp_path / "dumps"
    dumps.mkdir()
    monkeypatch.setattr(fl, "_log_paths", lambda: [app, guard])
    from kazma_core.observability import loop_stall

    monkeypatch.setattr(loop_stall, "stall_dump_dir", lambda: dumps)
    return app, guard, dumps


def _write(app, guard, app_lines, guard_lines=()):
    app.write_text("".join(app_lines), encoding="utf-8")
    guard.write_text("".join(guard_lines), encoding="utf-8")


def test_every_mechanism_is_named_in_the_message(week):
    app, guard, _ = week
    # More than eight of each kind fired and stayed silent: the old message
    # cut both lists at eight without saying so.
    _write(app, guard, [
        _app_line("[universal-backup] complete: 26 DBs"),
        _app_line("[neo4j-backup] exported 12 nodes, 3 relationships"),
        _app_line("[restic] maintenance ok: local (forget --prune, check)"),
        _app_line("[digest] daily digest dispatched (400 chars)"),
        _app_line("[loop-stall] event loop unresponsive for 15s"),
        _app_line("[Supervisor] Tool LOOP detected"),
        _app_line("swept orphaned temp dump x"),
        _app_line("restic local snapshot ok: ab12"),
        _app_line("[restore-drill] PASS: 36/36 checks passed"),
    ], [
        _guard_line("guard.restarting", reason="unhealthy (probe error: timed out)"),
        _guard_line("health.recovered"),
        _guard_line("port.stale_before_spawn"),
    ])
    report = fl.build_report(hours=24)
    title, body = fl.render(report)
    message = title + " " + body
    for sig in fl.FIRING_SIGNATURES:
        assert sig.mechanism in message, sig.mechanism
    assert len(report.entries) == len(fl.FIRING_SIGNATURES)
    # Re-pointed 2026-11-11: the count is the body's first line; the title
    # stays short (it was the digest's whole alert line).
    assert body.startswith(f"Last 24h: {len(report.fired)} of {len(fl.FIRING_SIGNATURES)} mechanisms fired")


def test_what_needs_a_look_comes_first(week):
    app, guard, _ = week
    _write(app, guard, [
        _app_line("[universal-backup] complete: 26 DBs"),
        _app_line("[loop-stall] event loop unresponsive for 15s"),
    ], [
        _guard_line("guard.restarting", reason="unhealthy (probe error: timed out)"),
        _guard_line("guard.restarting", reason="unhealthy (probe error: timed out)"),
        _guard_line("guard.restarting", reason="process exited (code 1)"),
    ])
    report = fl.build_report(hours=24)
    title, body = fl.render(report)
    assert "need a look" in title
    # Re-pointed 2026-11-11 (sections, one line an item): the first section
    # after the count is what needs a look, each restart reason on its line.
    sections = body.split("\n\n")
    look = sections[1]
    assert look.startswith("Needs a look:\n")
    assert ("  guard restart: 3\n"
            "    unhealthy (probe error: timed out): 2\n"
            "    process exited (code 1): 1") in look
    assert "  health-gated restart: 2" in look
    assert "event loop stall" in look
    # A week with nothing wrong -- every scheduled job on schedule -- says so
    # by saying nothing is wrong.
    _write(app, guard, [_app_line("[universal-backup] complete: 26 DBs")] * 28
           + [_app_line("[neo4j-backup] exported 12 nodes, 3 relationships")] * 28
           + [_app_line("[restore-drill] PASS: 36/36 checks passed")] * 7
           + [_app_line("[digest] daily digest dispatched (400 chars)")] * 7
           # Re-pointed 2026-10-14 (Time Travel TT-11): the daily snapshot
           # prune is a scheduled job the report now watches, so a week on
           # schedule includes its seven runs.
           + [_app_line("[TimeTravel] maintenance: deleted=0 remaining_old=0 before=10 after=10 "
                        "vacuumed=False")] * 7
           # Re-pointed 2026-10-31: the daily crawl check is a scheduled job.
           + [_app_line("[acquisition] canary passed in 4.2 s")] * 7)
    quiet_title, quiet_body = fl.render(fl.build_report(hours=168))
    assert "need a look" not in quiet_title
    assert "Needs a look:" not in quiet_body


def test_alerts_are_counted_as_sent_not_as_logged(week):
    app, guard, _ = week
    _write(app, guard, [
        _app_line("[ops_alert] reply.persist_failed | A reply was produced but NOT saved to the transcript."),
        *[_app_line("[ops_alert] reply.persist_failed | A reply was produced but NOT saved "
                    "to the transcript. (throttled)")] * 140,
        _app_line("[ops_alert] backup.pg_dump | The Postgres dump failed."),
    ])
    report = fl.build_report(hours=24)
    alerting = next(e for e in report.entries if e.mechanism == "operator alerting")
    assert alerting.count == 2
    assert alerting.tally == {"reply.persist_failed": 1, "backup.pg_dump": 1}
    assert report.suppressed_alerts == 140
    _, body = fl.render(report)
    assert "140 repeats held back by their cooldown" in body


def test_a_scheduled_job_below_its_schedule_needs_a_look(week):
    app, guard, _ = week
    # Two digests in a week that should have had seven: the daily digest did
    # not run from boot to boot until 2026-09-28.
    _write(app, guard, [_app_line("[digest] daily digest dispatched (400 chars)")] * 2
           + [_app_line("[universal-backup] complete: 26 DBs")] * 27)
    report = fl.build_report(hours=168)
    look = {e.mechanism for e in report.needs_a_look}
    assert "daily digest" in look
    assert "universal backup" not in look  # 27 of 28: a restart costs a run
    _, body = fl.render(report)
    assert "  daily digest: 2 of 7 scheduled" in body
    # Nothing at all is below schedule too.
    assert "graph memory backup" in look


# ── the message as Telegram shows it (2026-11-11) ─────────────────────
# The report of 2026-11-11 arrived as one paragraph under a 111-character
# title, and stopped at "Stops over budget 0": the body was every section
# joined by spaces, and the alert layer cut it at 600 characters. The digest
# then printed the whole title as the alert's name, ": 1" at its end.

#: The live week's restart reasons, as the guard wrote them.
_LIVE_REASONS = (["never became healthy"] * 33
                 + ["unhealthy (database: timed out (3s); code_execution: No available isolated "
                    "execution backend; not ready for 628s)"])


def _live_week(app, guard):
    app_lines = ([_app_line("[universal-backup] complete: 26 DBs")] * 28
                 + [_app_line("[digest] daily digest dispatched (400 chars)")] * 7
                 + [_app_line(f"[ops_alert] key.{i} | an alert") for i in range(9)]
                 + [_app_line("[ops_alert] backup.pg_dump | The Postgres dump failed.")] * 2
                 + [_app_line("restic local snapshot ok: ab12")] * 112)
    guard_lines = [_guard_line("guard.restarting", reason=r) for r in _LIVE_REASONS]
    _write(app, guard, app_lines, guard_lines)


def _sent(monkeypatch, report):
    """What ``_send`` hands the alert layer, and the card it would build."""
    from kazma_core.observability import ops_alerts

    calls = []
    monkeypatch.setattr(ops_alerts, "alert", lambda key, title, detail="", **kw: calls.append(
        (title, detail, kw)) or True)
    fl._send(report)
    (title, detail, kw), = calls
    card = ops_alerts._format(kw["severity"], title, detail, 0, 0,
                              kw.get("max_detail", ops_alerts.MAX_DETAIL_CHARS))
    return title, detail, kw, card


def test_the_whole_report_reaches_the_card_in_lines(week, monkeypatch):
    app, guard, _ = week
    _live_week(app, guard)
    report = fl.build_report(hours=168)
    title, detail, kw, card = _sent(monkeypatch, report)
    assert kw["severity"] == "warn"
    # Every section is in the card, each on lines of its own.
    for heading in ("Needs a look:", "Alerts sent:", "Routine:", "Silent (", "Not watched ("):
        assert f"\n\n{heading}" in card, heading
    assert "(cut)" not in card
    assert "  guard restart: 34\n    never became healthy: 33\n" in card
    assert len(card) <= 4096
    # The alert layer's default cut is what lost the rest (negative control).
    from kazma_core.observability import ops_alerts

    cut = ops_alerts._format("warn", title, detail, 0, 0)
    assert "Routine:" not in cut and cut.endswith(ops_alerts.CUT_MARK)


def test_the_title_is_short_and_says_what_is_wrong(week):
    app, guard, _ = week
    _live_week(app, guard)
    title, _ = fl.render(fl.build_report(hours=168))
    assert title.startswith("Weekly resilience report — ")
    assert len(title) <= 80 and "log lines" not in title and "mechanisms" not in title
    # One is "needs", more are "need".
    _write(app, guard, [_app_line("[universal-backup] complete: 26 DBs")] * 28,
           [_guard_line("guard.restarting", reason="process exited (code 1)")])
    report = fl.build_report(hours=168)
    assert len(report.needs_a_look) >= 1
    one = fl._title(fl.LedgerReport(entries=[e for e in report.entries if e.mechanism == "guard restart"]))
    assert one == "Weekly resilience report — 1 needs a look"


def test_a_long_report_leaves_out_its_last_sections_whole_and_says_which(week):
    app, guard, _ = week
    _live_week(app, guard)
    report = fl.build_report(hours=168)
    _, body = fl.render(report, budget=900)
    assert "Needs a look:" in body
    assert "\n\nNot watched (" not in body, "the section itself is left out"
    last = body.rsplit("\n\n", 1)[1]
    assert last.startswith("Left out for length: ") and "Not watched (no log line to count)" in last


def test_targets_that_cannot_be_measured_do_not_lose_the_report(week, monkeypatch):
    app, guard, _ = week
    _live_week(app, guard)

    def broken(now=None):
        raise RuntimeError("the observer could not be asked")

    monkeypatch.setattr(fl, "_week_targets", broken)
    report = fl.build_report(hours=168)
    assert report.targets == [] and "targets not measured (RuntimeError)" in report.error
    _, body = fl.render(report)
    assert "Needs a look:" in body and "Scan error: targets not measured" in body


# ── what blocked the loop ──────────────────────────────────────────────


# The live dump of 2026-09-28 22:16 UTC (Windows paths, trimmed).
_NEW_DUMP = r"""event loop unresponsive for 15.3s
written 2026-09-29T01:16:56
threads alive: 25
======================================================================
Event-loop thread 0x000028f8 (most recent call first):
  File "C:\kazma\.venv\Lib\site-packages\psycopg_pool\pool.py", line 880 in wait
  File "C:\kazma\kazma-core\kazma_core\db\postgres_pool.py", line 88 in execute
  File "C:\kazma\kazma-core\kazma_core\config_store.py", line 1582 in get
  File "C:\kazma\kazma-core\kazma_core\backup\restic_repo.py", line 171 in repo_paths
  File "C:\kazma\kazma-core\kazma_core\memory\worker_bootstrap.py", line 1359 in _handle_restic_maintenance
  File "C:\kazma\kazma-core\kazma_core\memory\task_queue.py", line 343 in _process
  File "C:\py\Lib\asyncio\base_events.py", line 1936 in _run_once

Thread 0x00001b20 (most recent call first):
  File "C:\py\Lib\concurrent\futures\thread.py", line 81 in _worker
"""

# faulthandler only (before 2026-09-26): the loop is the thread in _run_once.
_OLD_DUMP = """event loop unresponsive for 15.0s
======================================================================
Thread 0x00001111 (most recent call first):
  File "/opt/py/lib/python3.11/concurrent/futures/thread.py", line 81 in _worker

Thread 0x00002222 (most recent call first):
  File "/opt/kazma/kazma-ui/kazma_ui/settings.py", line 40 in get_appearance
  File "/opt/kazma/kazma-ui/kazma_ui/app.py", line 900 in _render
  File "/opt/py/lib/python3.11/asyncio/base_events.py", line 1936 in _run_once
"""

_ELSEWHERE_DUMP = """event loop unresponsive for 18.5s
======================================================================
Event-loop thread 0x0000d1c8 (most recent call first):
  File "/opt/py/lib/python3.11/ssl.py", line 922 in read
  File "/opt/py/lib/python3.11/asyncio/sslproto.py", line 800 in _do_read__copied
  File "/opt/kazma/serve.py", line 121 in <module>
"""


def test_the_blocker_is_the_first_kazma_frame_above_the_storage_layer():
    assert fl._blocker(_NEW_DUMP) == (
        "kazma_core.backup.restic_repo.repo_paths "
        "(from kazma_core.memory.worker_bootstrap._handle_restic_maintenance)"
    )
    assert fl._blocker(_OLD_DUMP) == "kazma_ui.settings.get_appearance (from kazma_ui.app._render)"
    assert fl._blocker(_ELSEWHERE_DUMP).startswith("(outside Kazma code")
    assert fl._blocker("event loop unresponsive\n") == "(the dump has no event-loop stack)"


def test_the_report_names_the_blockers_of_the_week(week):
    app, guard, dumps = week
    _write(app, guard, [_app_line("[loop-stall] event loop unresponsive for 15s")] * 2)
    (dumps / "stall-a.txt").write_text(_OLD_DUMP, encoding="utf-8")
    (dumps / "stall-b.txt").write_text(_OLD_DUMP, encoding="utf-8")
    old = dumps / "stall-last-month.txt"
    old.write_text(_ELSEWHERE_DUMP, encoding="utf-8")
    month_ago = time.time() - 30 * 86400
    os.utime(old, (month_ago, month_ago))
    report = fl.build_report(hours=168)
    assert report.stall_blockers == {"kazma_ui.settings.get_appearance (from kazma_ui.app._render)": 2}
    _, body = fl.render(report)
    assert ("The event loop was blocked in:\n"
            "  kazma_ui.settings.get_appearance (from kazma_ui.app._render): 2") in body
