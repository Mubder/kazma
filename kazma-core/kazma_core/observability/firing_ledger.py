"""Count which resilience mechanisms actually fired, on a schedule.

The audit's finding was that existence is not execution: three shipped
mechanisms had never run, and nobody knew because nothing counted. The
resilience manifest fixed half of that -- it names every recovery claim and
fails the build when the code or its test disappears. It cannot tell you
whether any of them has ever *run*.

So ``unproven: 8`` has been a number computed by hand whenever someone
asked. That makes the audit a document. This makes it a dial.

Each mechanism declares the log line it emits when it fires. A weekly sweep
counts those lines and reports what fired, what did not, and what changed
since last week. A mechanism that goes from firing to silent is worth
knowing about; so is one that has never fired at all, which is exactly the
state the audit found three of.

What the report leads with (2026-09-30)
---------------------------------------
The first versions listed the first eight mechanisms that fired and the
first eight that did not, in declaration order, and counted a placeholder
row as a mechanism. The week that ended 2026-09-29 reported "15 of 29
fired" and showed eight names: the six health-gated restarts and thirty
event-loop stalls behind them were among the seven it left out, and 141 of
its "149 alerts" were one alert repeated inside its cooldown. The report
now says every mechanism, puts what needs a look first -- a symptom that
fired, or a scheduled job that ran fewer times than its schedule -- names
what blocked the event loop (from the stall dumps), and counts alerts sent,
not alerts suppressed.

What this deliberately does NOT do
----------------------------------
It does not mark a mechanism "proven" on its own. A log line proves the
code path executed; whether it did the right thing is a question for the
test suite and the rehearsal. The ledger reports counts and leaves the
judgement where it belongs -- overstating here would recreate the original
problem in a new place. The one exception is arithmetic: a job scheduled
every six hours that ran eleven times in a week did not run on schedule.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["FIRING_SIGNATURES", "scan_log", "build_report",
           "run_weekly_sweep", "ledger_scheduler"]

#: A symptom: it fires because something went wrong. Always worth a look.
SYMPTOM = "symptom"
#: A recovery: it fires when it saves the day. Silence means the fault
#: never came, not that the code is broken.
RECOVERY = "recovery"
#: Scheduled work: it should fire on its cadence. Too few runs is a problem.
ROUTINE = "routine"
#: The pager itself: every alert that was sent.
ALERTING = "alerting"


@dataclass(frozen=True)
class Signature:
    """One mechanism and the trace it leaves when it fires."""

    mechanism: str
    pattern: str
    note: str = ""
    kind: str = RECOVERY
    #: Runs a week its schedule makes (ROUTINE only; 0 = no fixed schedule).
    per_week: float = 0.0
    #: A regex with a group named ``k``: each firing is tallied under that
    #: value -- the alert key, the restart reason.
    group: str = ""


# Patterns are matched against the rendered log message. They are written
# against lines the code actually emits -- each was verified present in a
# real log or in the emitting source, not guessed from the mechanism name.
FIRING_SIGNATURES: tuple[Signature, ...] = (
    Signature("MCP reconnect", r"\[MCP-reconnect\] '.+' (re)?connected",
              "a server came back without a restart"),
    Signature("MCP reconnect attempt", r"\[MCP-reconnect\] '.+' still down",
              kind=SYMPTOM),
    # ops_alerts.alert() logs "[ops_alert] <key> | <title>" -- underscore. This
    # watched "[ops-alert]" and reported alerting silent in a week that sent
    # five alerts, one of them a failed restore drill (2026-09-23). An alert
    # held back by its cooldown logs the same line ending "(throttled)": it
    # was not sent, and the week to 2026-09-29 counted 140 of them as alerts.
    Signature("operator alerting", r"\[ops_alert\] \S+ \|(?!.*\(throttled\))",
              kind=ALERTING, group=r"\[ops_alert\] (?P<k>\S+) \|"),
    # Every 6 h (worker_bootstrap._BACKUP_EXPORT_INTERVAL_HOURS).
    Signature("universal backup", r"\[universal-backup\] complete:",
              kind=ROUTINE, per_week=28),
    Signature("graph memory backup", r"\[neo4j-backup\] exported \d+ nodes",
              kind=ROUTINE, per_week=28),
    Signature("restic snapshot",
              r"pg dump snapshotted to|restic \w+ snapshot ok", kind=ROUTINE),
    # The success line. Maintenance used to log only on failure, so this
    # matched failures and could not tell "ran fine" from "never ran".
    Signature("restic maintenance", r"\[restic\] maintenance ok", kind=ROUTINE),
    # Daily, plus the weekly deep drill.
    Signature("restore drill", r"\[restore-drill\] (deep: )?(PASS|FAIL|UNVERIFIED):",
              "a backup was verified readable, not merely written",
              kind=ROUTINE, per_week=7),
    # Owner-started Knowledge crawls, tallied by verdict and, page by page,
    # by why a page was not stored (the crawl target, 2026-10-31).
    Signature("Knowledge crawl", r"\[kb_ingest\] crawl outcome:",
              "a Knowledge crawl ended, by its verdict", kind=ROUTINE,
              group=r"\[kb_ingest\] crawl outcome: (?P<k>\w+)"),
    Signature("Knowledge page not stored", r"\[kb_ingest\] page \S+ \(",
              "a page a crawl did not store, by outcome and reason", kind=SYMPTOM,
              group=r"\[kb_ingest\] page (?P<k>\S+ \([^)]+\))"),
    # A page the site no longer has, taken out of its library (2026-11-13):
    # a 410 at once, a 404 once a later crawl found it missing again.
    Signature("Knowledge page removed", r"\[kb_ingest\] page removed from the library \(",
              "a page the site removed, taken out of its library", kind=ROUTINE,
              group=r"\[kb_ingest\] page removed from the library \((?P<k>[^)]+)\)"),
    # Monthly (observability/failover_drill.py): the primary forced down,
    # the backup of another vendor answering, the notice, the switch back.
    Signature("backup-model drill", r"\[failover-drill\] (PASS|FAIL):",
              "the failover chain was taken, not merely configured",
              kind=ROUTINE, per_week=0.25),
    # Daily (web_acquire/canary.py): a real crawl of one known page.
    Signature("crawl canary", r"\[acquisition\] canary (passed|failed)",
              "a crawl was run end to end, not merely configured",
              kind=ROUTINE, per_week=7),
    Signature("repetition loop breaker", r"\[Supervisor\] Tool LOOP detected",
              "never observed in production as of 2026-08-29"),
    Signature("iteration budget divert", r"\[Supervisor\] Iteration \d+ == max_iterations"),
    Signature("detached-pump watchdog", r"Reaping stalled detached pump"),
    Signature("stale turn reap", r"Reaping stale detached turn"),
    Signature("guard restart", r'"event": "guard.restarting"', kind=SYMPTOM,
              group=r'"event": "guard\.restarting".*?"reason": "(?P<k>[^"]+)"'),
    # A health-gated restart is a restart whose reason is "unhealthy (...)"
    # -- FAILURES_TO_KILL consecutive failed probes. This used to count every
    # health.failed/recovered event, and reported 430 restarts in a week that
    # had none: each was one missed probe answered by the next.
    Signature("health-gated restart",
              r'"event": "guard\.restarting".*"reason": "unhealthy',
              "the guard only restarts on consecutive failed health probes",
              kind=SYMPTOM),
    Signature("probe miss tolerated", r'"event": "health\.recovered"',
              "a failed health probe the guard rode out without restarting"),
    # Kazma answered "not ready" (its database away) and the guard paged
    # instead of restarting -- restarts on 2026-09-28 were this case.
    Signature("dependency outage ridden out", r'"event": "health\.dependency_down"',
              "Kazma answered but a dependency (the database) was down; no restart",
              kind=SYMPTOM),
    Signature("probe unrunnable (machine out of ports)", r'"event": "health\.probe_unrunnable"',
              "Windows had no free local port; the guard did not count it as Kazma failing",
              kind=SYMPTOM),
    Signature("crash-loop refusal", r'"event": "guard\.(crash_loop|refused_to_start)"',
              "restarting forever is worse than stopping and saying so", kind=SYMPTOM),
    Signature("orphan reap", r'"event": "(orphan|port)\.(reaping|reaped|reaping_holder|holder_reaped)"'),
    Signature("maintenance pause", r'"event": "maintenance\.(active|resumed)"'),
    # "dispatched" is the line before 2026-10-03, when the digest was handed
    # off and never confirmed; it keeps the week of the change counted.
    Signature("daily digest", r"\[digest\] daily digest (?:delivered|dispatched)",
              kind=ROUTINE, per_week=7),
    # time_travel.maintain_snapshots: daily, and 120 s after every boot (the
    # live install restarts several times a day, so a week shows more).
    Signature("time travel maintenance", r"\[TimeTravel\] maintenance: deleted=",
              "the Time Travel steps' retention prune (TT-11)", kind=ROUTINE, per_week=7),
    Signature("install restore", r"\[restore\] (RESTORED|FAILED):"),
    Signature("foreign server detection", r'"event": "child.foreign_server_holds_port"'),
    Signature("pre-spawn port clearance", r'"event": "port.stale_before_spawn"'),
    Signature("orphaned temp sweep", r"swept orphaned (temp dump|archive)"),
    Signature("offsite fallback", r"offsite sync failed.*trying rclone", kind=SYMPTOM),
    Signature("connector health warning", r"connector\.google_(expired|expiring)",
              kind=SYMPTOM),
    Signature("chaos injection", r"\[Chaos\] Injecting"),
    # Not a recovery -- a symptom, and the report is where a chronic one gets
    # seen. 70 of these piled up in .kazma/stall-*.txt with nobody looking,
    # and one week they ended in a health-gated restart (2026-09-23).
    Signature("event loop stall (stacks dumped)", r"\[loop-stall\] event loop unresponsive",
              "every stack is in .kazma/stall-*.txt; the loop's frame names the blocker",
              kind=SYMPTOM),
)

#: A routine job below this share of its schedule is reported (a restart
#: can cost one run; a job that stopped costs them all).
_SCHEDULE_FLOOR = 0.75

_THROTTLED = re.compile(r"\[ops_alert\] \S+ \|.*\(throttled\)")


@dataclass
class LedgerEntry:
    mechanism: str
    count: int = 0
    last_seen: str = ""
    note: str = ""
    kind: str = RECOVERY
    #: The runs its schedule makes in this report's window (ROUTINE only).
    expected: int = 0
    #: Firings tallied by the signature's group (alert key, restart reason).
    tally: dict[str, int] = field(default_factory=dict)

    @property
    def below_schedule(self) -> bool:
        return bool(self.expected) and self.count < self.expected * _SCHEDULE_FLOOR

    def as_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"mechanism": self.mechanism, "count": self.count, "kind": self.kind}
        if self.last_seen:
            d["last_seen"] = self.last_seen
        if self.note:
            d["note"] = self.note
        if self.expected:
            d["expected"] = self.expected
        if self.tally:
            d["tally"] = dict(self.tally)
        return d


@dataclass
class LedgerReport:
    window_hours: float = 168.0
    scanned_lines: int = 0
    entries: list[LedgerEntry] = field(default_factory=list)
    error: str = ""
    #: Alerts logged but held back by their cooldown -- not sent.
    suppressed_alerts: int = 0
    #: What the event loop was blocked in, from the stall dumps in the window.
    stall_blockers: dict[str, int] = field(default_factory=dict)
    #: Manifest mechanisms that leave no trace the ledger can count.
    blind: list[str] = field(default_factory=list)
    #: The week against its targets (resilience plan G3), each a
    #: ``weekly_targets.Target``; empty when they could not be measured.
    targets: list[Any] = field(default_factory=list)

    @property
    def fired(self) -> list[LedgerEntry]:
        return [e for e in self.entries if e.count]

    @property
    def silent(self) -> list[LedgerEntry]:
        return [e for e in self.entries if not e.count]

    @property
    def needs_a_look(self) -> list[LedgerEntry]:
        """Symptoms that fired, and scheduled jobs that ran below schedule."""
        return [e for e in self.entries
                if (e.kind == SYMPTOM and e.count) or e.below_schedule]

    def as_dict(self) -> dict[str, Any]:
        return {
            "window_hours": self.window_hours,
            "scanned_lines": self.scanned_lines,
            "fired": [e.as_dict() for e in self.fired],
            "silent": [e.mechanism for e in self.silent],
            "needs_a_look": [e.mechanism for e in self.needs_a_look],
            "suppressed_alerts": self.suppressed_alerts,
            "stall_blockers": dict(self.stall_blockers),
            "blind": list(self.blind),
            "targets": [asdict(t) for t in self.targets],
            "error": self.error,
        }

    @property
    def targets_missed(self) -> int:
        return sum(1 for t in self.targets if t.met is False)

    def summary(self) -> str:
        return (f"{len(self.fired)} of {len(self.entries)} mechanisms fired in "
                f"the last {self.window_hours:.0f}h "
                f"({self.scanned_lines} log lines scanned)")


def _log_paths() -> list[Path]:
    """Every log a mechanism might report into.

    The first version read only the application log and therefore counted
    ZERO guard restarts, foreign-server detections and port clearances --
    all of which had fired that same evening. The guard deliberately logs
    to its own file so the app's logging config cannot silence it, which is
    exactly why a ledger that reads one file is worse than none: it reports
    "never fired" for mechanisms that did.
    """
    found: list[Path] = []
    try:
        from kazma_core.paths import data_dir, log_file, user_home

        root = Path(data_dir()).parent
        candidates = [
            log_file(),                               # the file the server writes
            Path(user_home()) / "guard.log",
            root / ".kazma" / "guard.log",
            Path.home() / ".kazma" / "guard.log",     # legacy guard home
        ]
    except Exception:  # noqa: BLE001
        candidates = [Path.home() / ".kazma" / "guard.log"]
    # Rotated siblings too (``kazma.log.2026-09-16``, ``guard.log.1``). The
    # app log rotates at midnight and the sweep runs weekly, so reading only
    # the live file saw one day of a 168h window -- and reported the backups,
    # restic snapshots and restore drills of the other six days "silent"
    # (2026-09-23). The per-line timestamp filter still bounds the window.
    for c in list(candidates):
        try:
            candidates.extend(sorted(c.parent.glob(c.name + ".*")))
        except OSError:
            continue
    for c in candidates:
        try:
            if c.is_file() and c.resolve() not in {f.resolve() for f in found}:
                found.append(c)
        except Exception:  # noqa: BLE001
            continue
    return found


def _scan_one(log: Path, compiled, counts: dict, last: dict,
              cutoff: float, report: LedgerReport,
              tallies: dict[str, Counter] | None = None) -> None:
    """Count matches in one file, in place."""
    with log.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            report.scanned_lines += 1
            ts = ""
            text = line
            # Structured lines carry the message in a field; plain ones are
            # matched whole, so a format change degrades to fewer matches
            # rather than to a crash.
            if line.lstrip().startswith("{"):
                try:
                    obj = json.loads(line)
                    text = str(obj.get("message") or "") + " " + line
                    ts = str(obj.get("timestamp") or obj.get("ts") or "")
                except Exception:  # noqa: BLE001
                    pass
            else:
                # Plain log line: copy the file formatter's datefmt
                # (``%Y-%m-%d %H:%M:%S``). Unparseable lines are skipped
                # from the count so months-old undated traces cannot inflate
                # "fired recently" (audit M-13).
                m = _PLAIN_TS.match(line)
                if m:
                    ts = m.group(1)
            if not ts or not _within(ts, cutoff):
                continue
            if _THROTTLED.search(text):
                report.suppressed_alerts += 1
            for sig, rx, grp in compiled:
                if rx.search(text):
                    counts[sig.mechanism] += 1
                    if grp is not None and tallies is not None:
                        g = grp.search(text)
                        tallies[sig.mechanism][g.group("k") if g else "(unnamed)"] += 1
                    # Newest wins: rotated files are read after the live one,
                    # and a plain overwrite reported yesterday as "last seen".
                    stamp = ts[:19].replace(" ", "T")
                    if stamp > last.get(sig.mechanism, ""):
                        last[sig.mechanism] = stamp


def scan_log(hours: float = 168.0, path: str | Path | None = None) -> LedgerReport:
    """Count firings within the window. Never raises.

    Reads line by line rather than slurping: the log is rotated but can
    still reach tens of megabytes, and a reporting job must not be the
    thing that spikes memory on the box it reports about.
    """
    report = LedgerReport(window_hours=hours)
    logs = [Path(path)] if path else _log_paths()
    logs = [p for p in logs if p.is_file()]

    def expected(sig: Signature) -> int:
        return int(sig.per_week * hours / 168.0) if sig.kind == ROUTINE else 0

    if not logs:
        report.error = "no log file found"
        report.entries = [LedgerEntry(s.mechanism, 0, note=s.note, kind=s.kind,
                                      expected=expected(s))
                          for s in FIRING_SIGNATURES]
        return report

    compiled = [
        (s, re.compile(s.pattern, re.IGNORECASE),
         re.compile(s.group, re.IGNORECASE) if s.group else None)
        for s in FIRING_SIGNATURES
    ]
    counts: dict[str, int] = {s.mechanism: 0 for s in FIRING_SIGNATURES}
    tallies: dict[str, Counter] = {s.mechanism: Counter() for s in FIRING_SIGNATURES}
    last: dict[str, str] = {}
    cutoff = time.time() - hours * 3600

    for log in logs:
        try:
            if log.stat().st_mtime < cutoff:
                continue  # a rotated file last written before the window
            _scan_one(log, compiled, counts, last, cutoff, report, tallies)
        except Exception as exc:  # noqa: BLE001
            report.error = f"{log.name}: {str(exc)[:150]}"

    report.entries = [
        LedgerEntry(s.mechanism, counts[s.mechanism],
                    last.get(s.mechanism, ""), s.note, kind=s.kind,
                    expected=expected(s),
                    tally=dict(tallies[s.mechanism].most_common()))
        for s in FIRING_SIGNATURES
    ]
    if not path:
        report.stall_blockers = stall_blockers(cutoff)
    return report


_PLAIN_TS = re.compile(r"^(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})")
_LAST_RUN_KEY = "observability.firing_ledger.last_run"

# Frames below the caller that put the work on the loop: the dump's news is
# the first Kazma frame ABOVE these (AGENTS.md §35), not postgres_pool.
_STORAGE_FRAMES = ("config_store.py", "postgres_pool.py", "sqlite_session.py",
                   "backend.py", "pg_helpers.py")
_FRAME = re.compile(r'File "(?P<path>[^"]+)", line \d+ in (?P<func>\S+)')
_KAZMA_PKG = re.compile(r"[\\/](kazma_(?:core|ui|gateway|cli|skills|tui))[\\/](?P<rel>.+)\.py$")


_THREAD_HEAD = re.compile(r"^(?:Event-loop thread|Current thread|Thread) 0x", re.MULTILINE)


def _loop_stack(dump: str) -> list[str]:
    """The event-loop thread's frame lines, most recent call first.

    Dumps since 2026-09-26 lead with it ("Event-loop thread 0x..."); older
    ones hold it among faulthandler's threads, as the thread running
    asyncio's ``_run_once``.
    """
    starts = [m.start() for m in _THREAD_HEAD.finditer(dump)] + [len(dump)]
    blocks = [dump[a:b] for a, b in zip(starts, starts[1:])]
    for block in blocks:
        if block.startswith("Event-loop thread"):
            return block.splitlines()[1:]
    for block in blocks:
        if " in _run_once" in block:
            return block.splitlines()[1:]
    return []


def _blocker(dump: str) -> str:
    """``package.module.func (from package.module.func)`` for a dump's event loop.

    The first Kazma frame above the storage layer, and its caller. A loop
    parked outside Kazma code (an SSL read, a selector) is named as such: the
    time went to another thread holding the interpreter.
    """
    stack = _loop_stack(dump)
    if not stack:
        return "(the dump has no event-loop stack)"
    frames: list[str] = []
    for line in stack:
        m = _FRAME.search(line)
        if not m:
            break
        path, func = m.group("path"), m.group("func")
        if "site-packages" in path or path.endswith(_STORAGE_FRAMES):
            continue
        pkg = _KAZMA_PKG.search(path)
        if pkg is None:
            continue
        module = pkg.group("rel").replace("\\", ".").replace("/", ".")
        frames.append(f"{pkg.group(1)}.{module}.{func}")
        if len(frames) == 2:
            break
    if not frames:
        return "(outside Kazma code -- another thread held the interpreter)"
    return frames[0] if len(frames) == 1 else f"{frames[0]} (from {frames[1]})"


def stall_blockers(cutoff: float) -> dict[str, int]:
    """What the event loop was blocked in, tallied over the dumps since *cutoff*."""
    from kazma_core.observability.loop_stall import stall_dump_dir

    tally: Counter = Counter()
    try:
        dumps = list(stall_dump_dir().glob("stall-*.txt"))
    except OSError:  # a report must never fail on its evidence
        logger.debug("[firing-ledger] stall dumps unreadable", exc_info=True)
        return {}
    for dump in dumps:
        try:
            if dump.stat().st_mtime < cutoff:
                continue
            tally[_blocker(dump.read_text(encoding="utf-8", errors="replace"))] += 1
        except OSError:
            continue
    return dict(tally.most_common())


def _within(ts: str, cutoff: float) -> bool:
    try:
        import datetime

        stamp = ts.replace(" ", "T", 1) if " " in ts[:19] and "T" not in ts[:19] else ts
        return datetime.datetime.fromisoformat(stamp).timestamp() >= cutoff
    except Exception:  # noqa: BLE001
        return False  # unparseable → skip from counts (audit M-13)


def _last_run_epoch() -> float | None:
    try:
        from kazma_core.config_store import get_config_store

        raw = get_config_store().get(_LAST_RUN_KEY)
        if raw is None or raw == "":
            return None
        return float(raw)
    except Exception:  # noqa: BLE001
        return None


def _stamp_last_run() -> None:
    try:
        from kazma_core.config_store import get_config_store

        get_config_store().set(_LAST_RUN_KEY, time.time(), category="observability")
    except Exception as exc:  # noqa: BLE001
        logger.debug("[firing-ledger] last_run stamp failed: %s", exc)


#: The lines the weekly targets read (weekly_targets), so the rest is skipped.
_TARGET_APP_MARKS = ("[turn] Turn finished", "[universal-backup] complete:",
                     "[shutdown] the stop ran over", "[ops_alert]")
_TARGET_GUARD_MARKS = ("guard.restarting", "guard.supervising")


def _week_targets(now: float | None = None) -> list[Any]:
    """The week against its targets, from the logs of the last two weeks and
    the observer's uptime (plan G3)."""
    from kazma_core.observability.weekly_targets import measure_targets

    now = time.time() if now is None else now
    cutoff = now - 2 * 7 * 86400
    app: list[str] = []
    guard: list[str] = []
    for log in _log_paths():
        try:
            if log.stat().st_mtime < cutoff:
                continue
            is_guard = log.name.startswith("guard.log")
            marks = _TARGET_GUARD_MARKS if is_guard else _TARGET_APP_MARKS
            with log.open("r", encoding="utf-8", errors="replace") as fh:
                # Only the lines a target reads: two weeks of log are not held.
                (guard if is_guard else app).extend(line for line in fh if any(m in line for m in marks))
        except OSError:
            continue
    from kazma_core.observability.outside_watch import outside_status

    # Not paired, or the observer cannot say: uptime is unknown, never met.
    observer = outside_status().get("observer") or {}
    value = observer.get("up_7d_pct") if observer.get("ok") else None
    uptime = float(value) if isinstance(value, (int, float)) else None
    return measure_targets(app, guard, now=now, uptime_now=uptime)


def build_report(hours: float = 168.0) -> LedgerReport:
    """Scan, and cross-check against the manifest's own claims."""
    report = scan_log(hours)
    try:
        report.targets = _week_targets()
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        # The targets were measured outside any guard: a failure there (the
        # observer's status, an unreadable log, a malformed line) lost the
        # whole weekly report. The week's counts still go out.
        logger.warning("[firing-ledger] the weekly targets could not be measured", exc_info=True)
        report.error = (report.error + "; " if report.error else "") + \
            f"targets not measured ({type(exc).__name__})"
    try:
        from kazma_core.observability.resilience_manifest import MECHANISMS

        # Names are compared on letters alone. The manifest writes
        # "foreign-server detection" and the ledger writes "foreign server
        # detection"; a raw substring test called that mechanism blind while
        # it was being counted two lines above -- a false alarm in the one
        # report whose job is to tell true silence from unwatched.
        def key(n: str) -> str:
            return re.sub(r"[^a-z0-9]+", "", n.lower())

        named = {m.name for m in MECHANISMS}
        tracked = {key(s.mechanism) for s in FIRING_SIGNATURES}
        # Mechanisms the manifest claims but the ledger cannot observe are
        # worth naming: an unobservable mechanism is one whose firing
        # nobody could ever confirm, which is the audit's finding again.
        # Listed apart: they are not mechanisms that stayed silent.
        report.blind = sorted(n for n in named
                              if not any(t in key(n) or key(n) in t for t in tracked))
    except Exception:  # noqa: BLE001
        logger.debug("[firing-ledger] manifest cross-check failed", exc_info=True)
    return report


def run_weekly_sweep(hours: float = 168.0, *, notify: bool = True) -> LedgerReport:
    """Build the report and send it. Never raises."""
    try:
        report = build_report(hours)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[firing-ledger] sweep failed", exc_info=True)
        r = LedgerReport(window_hours=hours)
        r.error = str(exc)[:200]
        return r

    logger.info("[firing-ledger] %s", report.summary())
    if notify:
        _send(report)
    _stamp_last_run()
    return report


SWEEP_INTERVAL_HOURS = 168.0


async def ledger_scheduler() -> None:
    """Fire the sweep once a week. Crash-isolated.

    Persists ``last_run`` in ConfigStore so a server that restarts more
    often than weekly still emits when overdue (audit M-13). First sleep
    is short (120s) when there is no last_run, mirroring the backup loop.
    """
    from kazma_core.db import availability
    from kazma_core.shutdown import sleep_or_shutdown

    interval = SWEEP_INTERVAL_HOURS * 3600
    first = True
    while True:
        try:
            # An unreadable stamp reads as "never ran": a read during an
            # outage would send the week's report again.
            if await availability.wait_while_away():
                return
            # Both off the loop: the ConfigStore read is a Postgres round
            # trip, and the scan reads a week of rotated logs (~4M lines on
            # the live install). A scan on the loop was already one of the
            # 70 loop-stall dumps before the rotated files were added.
            last = await asyncio.to_thread(_last_run_epoch)
            now = time.time()
            if last is None:
                delay = 120.0 if first else interval
            else:
                remaining = interval - (now - last)
                delay = 0.0 if remaining <= 0 else max(60.0, remaining)
            first = False
            if delay > 0 and await sleep_or_shutdown(delay):
                return
            if await availability.wait_while_away():
                return
            await asyncio.to_thread(run_weekly_sweep, SWEEP_INTERVAL_HOURS)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 -- a failed sweep must not
            # kill the cadence; the next one still fires.
            logger.warning("[firing-ledger] scheduler iteration failed: %s", exc)
            if await sleep_or_shutdown(300):
                return


def _named(entry: LedgerEntry) -> str:
    """``name (count)``, with the tally or the schedule when there is one
    (the command line's one-line form)."""
    if entry.tally and set(entry.tally) != {"(unnamed)"}:
        parts = [f"{k} {n}" for k, n in list(entry.tally.items())[:4]]
        more = len(entry.tally) - 4
        if more > 0:
            parts.append(f"+{more} more")
        return f"{entry.mechanism} ({entry.count}: {', '.join(parts)})"
    if entry.expected:
        return f"{entry.mechanism} ({entry.count} of {entry.expected} scheduled)"
    return f"{entry.mechanism} ({entry.count})"


#: The longest body the weekly message sends: under Telegram's 4,096 with the
#: card's first line around it (``ops_alerts.alert``'s ``max_detail`` ceiling).
BODY_BUDGET = 3500
#: How many names of a tally are listed under its mechanism.
_TALLY_SHOWN = 6


def _alert_name(key: str) -> str:
    """An alert key as the catalog titles it ("Kazma could not start"), else the key."""
    from kazma_core.observability.alert_catalog import entry_for

    entry = entry_for(key)
    return entry.title if entry is not None else key


def _entry_lines(entry: LedgerEntry, *, name=None) -> list[str]:
    """``  mechanism: count`` (or ``N of M scheduled``), and its tally under it.

    The tally names each reason or key with its count, one a line: a guard
    restart's reasons hold parentheses and semicolons of their own, and in
    one sentence they ran into each other ("unhealthy (database: timed out
    (3s); code_execution: ...) 1)").
    """
    named = entry.tally and set(entry.tally) != {"(unnamed)"}
    if entry.expected and not named:
        lines = [f"  {entry.mechanism}: {entry.count} of {entry.expected} scheduled"]
    else:
        lines = [f"  {entry.mechanism}: {entry.count}"]
    if named:
        items = list(entry.tally.items())
        lines += [f"    {(name or str)(k)}: {n}" for k, n in items[:_TALLY_SHOWN]]
        if len(items) > _TALLY_SHOWN:
            lines.append(f"    and {len(items) - _TALLY_SHOWN} more")
    return lines


def _title(report: LedgerReport) -> str:
    """Short, for a phone's notification and the daily digest's alert line.

    It carried the whole summary (111 characters on 2026-11-11), which the
    digest then printed as the alert's name with ": 1" after it.
    """
    from kazma_core.english_count import count_noun

    look, missed = len(report.needs_a_look), report.targets_missed
    parts = []
    if look:
        parts.append(f"{look} {'needs' if look == 1 else 'need'} a look")
    if missed:
        parts.append(f"{count_noun(missed, 'target')} missed")
    return "Weekly resilience report — " + (", ".join(parts) if parts else "nothing needs a look")


def _sections(report: LedgerReport) -> list[list[str]]:
    """The body's sections, most important first; each a heading and its lines."""
    from kazma_core.observability.weekly_targets import render_targets

    look = report.needs_a_look
    by_kind = {k: [e for e in report.fired if e.kind == k and e not in look]
               for k in (ALERTING, RECOVERY, ROUTINE, SYMPTOM)}
    window = "Last 7 days" if round(report.window_hours) == 168 else f"Last {report.window_hours:.0f}h"
    sections = [[f"{window}: {len(report.fired)} of {len(report.entries)} mechanisms fired "
                 f"({report.scanned_lines:,} log lines read)."]]
    if look:
        sections.append(["Needs a look:"] + [line for e in look for line in _entry_lines(e)])
    if report.targets:
        sections.append(render_targets(report.targets))
    if report.stall_blockers:
        blocked = ["The event loop was blocked in:"]
        blocked += [f"  {k}: {n}" for k, n in list(report.stall_blockers.items())[:8]]
        if len(report.stall_blockers) > 8:
            blocked.append(f"  and {len(report.stall_blockers) - 8} more (.kazma/stall-*.txt)")
        sections.append(blocked)
    sent = next((e for e in report.entries if e.kind == ALERTING), None)
    if sent is not None and (sent.count or report.suppressed_alerts):
        held = (f" ({report.suppressed_alerts} repeats held back by their cooldown)"
                if report.suppressed_alerts else "")
        alerts = [f"Alerts sent: {sent.count}{held}"]
        # The tally's lines, one level up: the heading is the count's line.
        alerts += [line[2:] for line in _entry_lines(sent, name=_alert_name)[1:]]
        sections.append(alerts)
    if by_kind[ROUTINE]:
        sections.append(["Routine:"] + [line for e in by_kind[ROUTINE] for line in _entry_lines(e)])
    if by_kind[RECOVERY]:
        sections.append(["Recoveries that ran:"] + [line for e in by_kind[RECOVERY] for line in _entry_lines(e)])
    silent = [e.mechanism for e in report.silent if e not in look]
    if silent:
        sections.append(["Silent (not needed this week, not necessarily broken): " + ", ".join(silent) + "."])
    if report.blind:
        sections.append(["Not watched (no log line to count): " + ", ".join(report.blind) + "."])
    if report.error:
        sections.append([f"Scan error: {report.error}"])
    return sections


def render(report: LedgerReport, budget: int = BODY_BUDGET) -> tuple[str, str]:
    """``(title, body)`` of the weekly message. Every mechanism is in it.

    The body is laid out in sections, a blank line between them, each a
    heading and one line an item -- the daily digest's layout. It was one
    paragraph, and the alert layer cut it at 600 characters: the report of
    2026-11-11 ended at "Stops over budget 0", with the alerts, the routine
    jobs and the silent mechanisms gone. Past *budget* the last sections are
    left out whole, and named.
    """
    sections = _sections(report)
    kept: list[list[str]] = []
    left_out: list[str] = []
    used = 0
    for i, section in enumerate(sections):
        text = "\n".join(section)
        size = len(text) + (2 if kept else 0)
        if not left_out and (i < 2 or used + size <= budget - 120):
            kept.append(section)
            used += size
        else:
            left_out.append(section[0].split(":", 1)[0])
    body = "\n\n".join("\n".join(s) for s in kept)
    if left_out:
        body += ("\n\nLeft out for length: " + ", ".join(left_out)
                 + ". The whole report: python -m kazma_core.observability.firing_ledger --quiet")
    return _title(report), body


def _send(report: LedgerReport) -> None:
    try:
        from kazma_core.observability.ops_alerts import alert

        title, body = render(report)
        problem = bool(report.needs_a_look or report.targets_missed)
        alert(
            "resilience.firing_ledger",
            title,
            body,
            severity="warn" if problem else "info",
            cooldown_s=6 * 24 * 3600,
            max_detail=BODY_BUDGET,
        )
    except Exception:  # noqa: BLE001
        logger.debug("[firing-ledger] could not send report", exc_info=True)


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Which resilience mechanisms fired?")
    ap.add_argument("--hours", type=float, default=168.0)
    ap.add_argument("--log", default=None)
    ap.add_argument("--quiet", action="store_true", help="do not send an alert")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    report = (scan_log(args.hours, args.log) if args.log
              else run_weekly_sweep(args.hours, notify=not args.quiet))
    title, body = render(report)
    print(f"\n{title}\n")
    for e in report.entries:
        mark = f"{e.count:>4}" if e.count else "   ·"
        line = f"  {mark}  {_named(e) if e.count or e.expected else e.mechanism}"
        if e.last_seen:
            line += f"   (last {e.last_seen})"
        print(line)
        if e.note and not e.count:
            print(f"          {e.note}")
    print(f"\n{body}\n")
    if report.error:
        print("\nerror:", report.error)
    return 0


if __name__ == "__main__":
    from kazma_core.env_files import load_env_files

    load_env_files()
    raise SystemExit(main())
