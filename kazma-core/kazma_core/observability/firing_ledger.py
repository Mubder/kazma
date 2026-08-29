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

What this deliberately does NOT do
----------------------------------
It does not mark a mechanism "proven" on its own. A log line proves the
code path executed; whether it did the right thing is a question for the
test suite and the rehearsal. The ledger reports counts and leaves the
judgement where it belongs -- overstating here would recreate the original
problem in a new place.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["FIRING_SIGNATURES", "scan_log", "build_report",
           "run_weekly_sweep", "ledger_scheduler"]


@dataclass(frozen=True)
class Signature:
    """One mechanism and the trace it leaves when it fires."""

    mechanism: str
    pattern: str
    note: str = ""


# Patterns are matched against the rendered log message. They are written
# against lines the code actually emits -- each was verified present in a
# real log or in the emitting source, not guessed from the mechanism name.
FIRING_SIGNATURES: tuple[Signature, ...] = (
    Signature("MCP reconnect", r"\[MCP-reconnect\] '.+' (re)?connected",
              "a server came back without a restart"),
    Signature("MCP reconnect attempt", r"\[MCP-reconnect\] '.+' still down"),
    Signature("operator alerting", r"\[ops-alert\]|\[alert\]"),
    Signature("universal backup", r"\[universal-backup\] complete:"),
    Signature("graph memory backup", r"\[neo4j-backup\] exported \d+ nodes"),
    Signature("restic snapshot",
              r"pg dump snapshotted to|restic \w+ snapshot ok"),
    Signature("restic maintenance", r"restic (forget|check|unlock)"),
    Signature("repetition loop breaker", r"\[Supervisor\] Tool LOOP detected",
              "never observed in production as of 2026-08-29"),
    Signature("iteration budget divert", r"\[Supervisor\] Iteration \d+ == max_iterations"),
    Signature("detached-pump watchdog", r"Reaping stalled detached pump"),
    Signature("stale turn reap", r"Reaping stale detached turn"),
    Signature("guard restart", r'"event": "guard.restarting"'),
    Signature("health-gated restart", r'"event": "health\.(failed|recovered)"',
              "the guard only restarts on a failed health probe"),
    Signature("crash-loop refusal", r'"event": "guard\.(crash_loop|refused_to_start)"',
              "restarting forever is worse than stopping and saying so"),
    Signature("orphan reap", r'"event": "(orphan|port)\.(reaping|reaped|reaping_holder|holder_reaped)"'),
    Signature("maintenance pause", r'"event": "maintenance\.(active|resumed)"'),
    Signature("daily digest", r"\[digest\] daily digest dispatched"),
    Signature("install restore", r"\[restore\] (RESTORED|FAILED):"),
    Signature("foreign server detection", r'"event": "child.foreign_server_holds_port"'),
    Signature("pre-spawn port clearance", r'"event": "port.stale_before_spawn"'),
    Signature("orphaned temp sweep", r"swept orphaned (temp dump|archive)"),
    Signature("offsite fallback", r"offsite sync failed.*trying rclone"),
    Signature("connector health warning", r"connector\.google_(expired|expiring)"),
    Signature("chaos injection", r"\[Chaos\] Injecting"),
)


@dataclass
class LedgerEntry:
    mechanism: str
    count: int = 0
    last_seen: str = ""
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"mechanism": self.mechanism, "count": self.count}
        if self.last_seen:
            d["last_seen"] = self.last_seen
        if self.note:
            d["note"] = self.note
        return d


@dataclass
class LedgerReport:
    window_hours: float = 168.0
    scanned_lines: int = 0
    entries: list[LedgerEntry] = field(default_factory=list)
    error: str = ""

    @property
    def fired(self) -> list[LedgerEntry]:
        return [e for e in self.entries if e.count]

    @property
    def silent(self) -> list[LedgerEntry]:
        return [e for e in self.entries if not e.count]

    def as_dict(self) -> dict[str, Any]:
        return {
            "window_hours": self.window_hours,
            "scanned_lines": self.scanned_lines,
            "fired": [e.as_dict() for e in self.fired],
            "silent": [e.mechanism for e in self.silent],
            "error": self.error,
        }

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
        from kazma_core.paths import data_dir, user_home

        root = Path(data_dir()).parent
        candidates = [
            Path(user_home()) / "kazma.log",
            root / ".kazma" / "kazma.log",
            root / "kazma.log",
            Path(user_home()) / "guard.log",
            root / ".kazma" / "guard.log",
            Path.home() / ".kazma" / "guard.log",     # legacy guard home
        ]
    except Exception:  # noqa: BLE001
        candidates = [Path.home() / ".kazma" / "guard.log"]
    for c in candidates:
        try:
            if c.is_file() and c.resolve() not in {f.resolve() for f in found}:
                found.append(c)
        except Exception:  # noqa: BLE001
            continue
    return found


def _scan_one(log: Path, compiled, counts: dict, last: dict,
              cutoff: float, report: LedgerReport) -> None:
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
            if ts and not _within(ts, cutoff):
                continue
            for sig, rx in compiled:
                if rx.search(text):
                    counts[sig.mechanism] += 1
                    if ts:
                        last[sig.mechanism] = ts[:19]


def scan_log(hours: float = 168.0, path: str | Path | None = None) -> LedgerReport:
    """Count firings within the window. Never raises.

    Reads line by line rather than slurping: the log is rotated but can
    still reach tens of megabytes, and a reporting job must not be the
    thing that spikes memory on the box it reports about.
    """
    report = LedgerReport(window_hours=hours)
    logs = [Path(path)] if path else _log_paths()
    logs = [p for p in logs if p.is_file()]
    if not logs:
        report.error = "no log file found"
        report.entries = [LedgerEntry(s.mechanism, 0, note=s.note)
                          for s in FIRING_SIGNATURES]
        return report

    compiled = [(s, re.compile(s.pattern, re.IGNORECASE)) for s in FIRING_SIGNATURES]
    counts: dict[str, int] = {s.mechanism: 0 for s in FIRING_SIGNATURES}
    last: dict[str, str] = {}
    cutoff = time.time() - hours * 3600

    for log in logs:
        try:
            _scan_one(log, compiled, counts, last, cutoff, report)
        except Exception as exc:  # noqa: BLE001
            report.error = f"{log.name}: {str(exc)[:150]}"

    report.entries = [
        LedgerEntry(s.mechanism, counts[s.mechanism],
                    last.get(s.mechanism, ""), s.note)
        for s in FIRING_SIGNATURES
    ]
    return report


def _within(ts: str, cutoff: float) -> bool:
    try:
        import datetime

        return datetime.datetime.fromisoformat(ts).timestamp() >= cutoff
    except Exception:  # noqa: BLE001
        return True  # unparseable stamp -> count it; undercounting hides firings


def build_report(hours: float = 168.0) -> LedgerReport:
    """Scan, and cross-check against the manifest's own claims."""
    report = scan_log(hours)
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
        blind = sorted(n for n in named
                       if not any(t in key(n) or key(n) in t for t in tracked))
        if blind:
            report.entries.append(
                LedgerEntry("(no firing signature)", 0,
                            note="manifest entries with no observable trace: "
                                 + ", ".join(blind[:6]))
            )
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
    return report


SWEEP_INTERVAL_HOURS = 168.0


async def ledger_scheduler() -> None:
    """Fire the sweep once a week. Crash-isolated; sleeps first.

    A report nobody schedules is the exact shape this module was written
    to find. It sat unscheduled for its first day of life, which is worth
    recording rather than quietly fixing: the pattern is that easy to
    repeat.

    Sleeps first for the same reason the digest does -- a report on every
    boot fires hardest during an incident, when the operator needs another
    message least.
    """
    while True:
        try:
            await asyncio.sleep(SWEEP_INTERVAL_HOURS * 3600)
            run_weekly_sweep(SWEEP_INTERVAL_HOURS)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 -- a failed sweep must not
            # kill the cadence; the next one still fires.
            logger.warning("[firing-ledger] scheduler iteration failed: %s", exc)
            await asyncio.sleep(300)


def _send(report: LedgerReport) -> None:
    try:
        from kazma_core.observability.ops_alerts import alert

        fired = ", ".join(f"{e.mechanism} ({e.count})" for e in report.fired[:8])
        silent = ", ".join(e.mechanism for e in report.silent[:8])
        alert(
            "resilience.firing_ledger",
            f"Weekly resilience report — {report.summary()}",
            (f"Fired: {fired or 'nothing'}. "
             f"Silent: {silent or 'none'}. "
             "Silent is not necessarily broken -- a recovery whose fault "
             "never occurred is simply unexercised. It is worth knowing which."),
            severity="info",
            cooldown_s=6 * 24 * 3600,
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
    print(f"\n{report.summary()}\n")
    for e in report.entries:
        mark = f"{e.count:>4}" if e.count else "   ·"
        line = f"  {mark}  {e.mechanism}"
        if e.last_seen:
            line += f"   (last {e.last_seen})"
        print(line)
        if e.note and not e.count:
            print(f"          {e.note}")
    if report.error:
        print("\nerror:", report.error)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
