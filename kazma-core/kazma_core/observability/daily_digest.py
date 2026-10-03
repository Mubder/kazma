"""A once-a-day summary, so silence means healthy rather than dead.

Phase 2 of the resilience plan. Incident alerts tell you when something
breaks. They cannot tell you the difference between "a quiet day" and "the
alerting itself is broken" — and after this project, that distinction is the
whole point. If the only signal is failure, an agent that has silently
stopped looks exactly like an agent with nothing to report.

The digest is deliberately small: what ran, what failed, what recovered,
what was suppressed. It is not a dashboard. Anything longer gets skimmed and
then ignored, at which point it is worse than nothing because it manufactures
a feeling of oversight.

Sources are all cheap and local, and cover the whole window whatever
restarted in it:

* the supervisor's own log — restarts, refusals, orphan reaps, and the
  operator's planned work (reloads, maintenance pauses)
* the application log — finished turns, alerts (sent and held back) and
  recovery events, rotated files included
* the approval registry (``hitl_gates.db``) — the questions asked

Nothing here queries the LLM, a remote database or the network.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
from collections import Counter
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

from kazma_core.english_count import count_noun

logger = logging.getLogger(__name__)

__all__ = ["build_digest", "digest_enabled"]

DIGEST_INTERVAL_HOURS = float(os.environ.get("KAZMA_DIGEST_INTERVAL_HOURS", "24"))

# Guard events worth counting in a daily summary. Anything not listed is
# noise for this purpose -- the log remains the full record.
_GUARD_EVENTS = {
    "guard.restarting": "server restarts",
    "child.never_ready": "failed starts",
    "orphan.reaped": "orphans cleaned up",
    "guard.crash_loop": "crash loops",
    "guard.refused_to_start": "refused starts",
    "child.foreign_server_holds_port": "port conflicts",
    # The guard's pager is the one that must work when the app cannot. From
    # 2026-09-24 22:30 it could not resolve its credentials and skipped every
    # page, a restart among them, for a day -- logged only in guard.log, at
    # INFO. The app's own digest is the other channel that can say so.
    "notify.skipped": "guard alerts not delivered (no credentials)",
    "notify.failed": "guard alerts that failed to send",
}

# The operator's planned work: shown, never a problem. A maintenance pause
# was listed under "Needs attention" -- two `kazma update` pauses of 41 s and
# 11 s, both ended, read as an incident on 2026-10-03. The server is stopped
# while a pause lasts, so the digest (which the server writes) only ever
# sees pauses that have ended.
_PAUSE_START = "maintenance.active"
_PAUSE_END = "maintenance.resumed"
_RELOAD = "guard.operator_reload"

# Application log markers. Substring match on the message, because these
# lines are formatted with %-args and the values vary per turn.
_APP_MARKERS = {
    "Backfilled unanswered turn": "answers recovered from checkpoint",
    "LLM call attempt": "LLM retries",
    "failed to connect": "MCP connection failures",
}

# One line per finished turn, from every transport
# (kazma_ui.turn_runtime._note_finished_turn). Turns were counted from "SSE
# turn complete", which only the web stream writes.
_TURN_MARKER = "[turn] Turn finished:"
_TURN_LINE = re.compile(r"\[turn\] Turn finished: platform=(\S+) key=(\S+) failed=(\S+)")

# Every ops alert leaves this line, sent or held back by its cooldown
# (ops_alerts.alert): "[ops_alert] <key> | <title>[ (throttled)]". The digest
# read the alerts raised since the process started, and the live install
# reloads several times a day: an alert from before the last reload was
# never in a digest.
_ALERT_MARKER = "[ops_alert] "
_THROTTLED = " (throttled)"

# How a question in the approval registry ended, in the digest's words.
_OUTCOME_WORDS = {
    "approve": "approved",
    "deny": "denied",
    "yolo": "approved by YOLO mode",
    "timeout": "timed out",
    "orphaned": "left unanswered",
    "waiting": "still waiting",
    "error": "could not resume",
}


def _outcome_word(outcome: str) -> str:
    # A clarify/confirm card records the option picked ("option:<id>").
    if outcome.startswith("option:"):
        return "answered"
    return _OUTCOME_WORDS.get(outcome, outcome)


def digest_enabled() -> bool:
    raw = os.environ.get("KAZMA_DAILY_DIGEST", "").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _guard_log_path() -> Path:
    env = os.environ.get("KAZMA_GUARD_LOG")
    if env:
        return Path(env)
    from kazma_core.paths import user_home

    return Path(user_home()) / "guard.log"


def _app_log_path() -> Path:
    from kazma_core.paths import log_file

    return log_file()


def _window_files(path: Path, since: float) -> list[Path]:
    """*path* and its rotated siblings that can hold a line from the window.

    The app log rotates at local midnight (``kazma.log.2026-10-02``). Reading
    the live file alone gave the digest sent at 05:17 UTC -- 08:17 in Kuwait
    -- eight hours of its 24, and "Turns completed: 0" over a day with turns
    (2026-10-03). A sibling last written before the window holds no line in
    it. Same rule as the weekly report (``firing_ledger._log_paths``).
    """
    files: list[Path] = []
    try:
        candidates = [path, *sorted(path.parent.glob(path.name + ".*"))]
    except OSError:
        candidates = [path]
    for p in candidates:
        try:
            if p.is_file() and p.stat().st_mtime >= since:
                files.append(p)
        except OSError:
            continue
    return files


def _records(locate, since: float, ts_field: str,
             needles: tuple[str, ...]) -> Iterator[tuple[float, dict]]:
    """``(time, record)`` for each JSON line in the window holding a needle.

    *locate* returns the live log's path. A log that cannot be found or read
    is skipped and said; the digest still goes out.
    """
    try:
        path = locate()
    except OSError as exc:
        logger.warning("[digest] log location not resolved: %s", exc)
        return
    for log in _window_files(path, since):
        try:
            with log.open(encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if not any(n in line for n in needles):
                        continue
                    try:
                        rec = json.loads(line)
                        ts = datetime.fromisoformat(rec[ts_field]).timestamp()
                    except (ValueError, KeyError, TypeError):
                        continue
                    if ts >= since:
                        yield ts, rec
        except OSError as exc:
            logger.warning("[digest] could not read %s: %s", log.name, exc)


def _scan_guard_log(since: float) -> tuple[Counter, list[float | None], int]:
    """Counted guard events, maintenance pause durations, reloads."""
    counts: Counter = Counter()
    planned: list[tuple[float, str]] = []
    needles = (*_GUARD_EVENTS, _PAUSE_START, _PAUSE_END, _RELOAD)
    for ts, rec in _records(_guard_log_path, since, "ts", needles):
        event = str(rec.get("event", ""))
        label = _GUARD_EVENTS.get(event)
        if label:
            counts[label] += 1
        elif event in (_PAUSE_START, _PAUSE_END, _RELOAD):
            planned.append((ts, event))
    planned.sort()
    reloads = sum(1 for _ts, event in planned if event == _RELOAD)
    return counts, _pause_durations(planned), reloads


def _pause_durations(events: list[tuple[float, str]]) -> list[float | None]:
    """How long each maintenance pause that began in the window lasted.

    A second ``maintenance.active`` before the ``resumed`` is the same pause
    acknowledged again by a guard that restarted. None: the log holds no end.
    """
    durations: list[float | None] = []
    start: float | None = None
    for ts, event in events:
        if event == _PAUSE_START:
            if start is None:
                start = ts
        elif event == _PAUSE_END and start is not None:
            durations.append(ts - start)
            start = None
    if start is not None:
        durations.append(None)
    return durations


def _scan_app_log(
    since: float,
) -> tuple[Counter, dict[str, tuple[str, bool]], dict[str, list[int]]]:
    """Counted markers; each finished turn, key -> (platform, failed); and
    each alert key -> [sent, held back].

    A turn is counted once by its key: close_turn can run again for a turn
    it closed before a restart.
    """
    counts: Counter = Counter()
    turns: dict[str, tuple[str, bool]] = {}
    alerts: dict[str, list[int]] = {}
    needles = (*_APP_MARKERS, _TURN_MARKER, _ALERT_MARKER)
    for _ts, rec in _records(_app_log_path, since, "timestamp", needles):
        msg = str(rec.get("message", ""))
        turn = _TURN_LINE.search(msg)
        if turn:
            turns[turn.group(2)] = (turn.group(1), turn.group(3) == "yes")
            continue
        if msg.startswith(_ALERT_MARKER):
            key = msg[len(_ALERT_MARKER):].split(" | ", 1)[0].strip()
            tally = alerts.setdefault(key, [0, 0])
            tally[1 if msg.endswith(_THROTTLED) else 0] += 1
            continue
        for marker, label in _APP_MARKERS.items():
            if marker in msg:
                counts[label] += 1
    return counts, turns, alerts


def _approvals(since: float) -> tuple[dict[str, int], str]:
    """Approval questions asked in the window by outcome, or why not read."""
    try:
        from kazma_core.safety.hitl_gates import gate_outcomes_since, gate_registry_enabled

        if not gate_registry_enabled():
            return {}, "approvals: not counted (the approval registry is switched off)"
        return gate_outcomes_since(since), ""
    except (sqlite3.Error, OSError) as exc:
        logger.warning("[digest] approval registry not read: %s", exc)
        return {}, "approvals: not counted (the approval registry could not be read)"


def _human_duration(seconds: float) -> str:
    seconds = max(0, round(seconds))
    if seconds < 120:
        return f"{seconds} s"
    minutes = round(seconds / 60)
    if minutes < 120:
        return f"{minutes} min"
    return f"{minutes // 60} h {minutes % 60} min"


def _split(counts: Counter) -> str:
    """Counts as "web 3, telegram 2": largest first, ties by name."""
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return ", ".join(f"{name} {n}" for name, n in ranked)


def build_digest(hours: float = DIGEST_INTERVAL_HOURS) -> str:
    """Render the digest. A log or the approval registry that cannot be read
    is skipped and said, so a usable digest goes out regardless."""
    since = (datetime.now(UTC) - timedelta(hours=hours)).timestamp()
    window = f"last {int(hours)}h"

    app, turns, alerts = _scan_app_log(since)
    guard, pauses, reloads = _scan_guard_log(since)
    approvals, approvals_note = _approvals(since)

    lines = ["\U0001f4c5 [Ops] Daily digest", f"Window: {window}"]

    completed = Counter(platform for platform, failed in turns.values() if not failed)
    failed_turns = sum(1 for _platform, failed in turns.values() if failed)
    lines.append("")
    total = sum(completed.values())
    lines.append(f"Turns completed: {total}" + (f" ({_split(completed)})" if total else ""))
    asked = sum(approvals.values())
    if asked:
        outcomes: Counter = Counter()
        for outcome, n in approvals.items():
            outcomes[_outcome_word(outcome)] += n
        lines.append(f"  approvals asked: {asked} ({_split(outcomes)})")
    elif approvals_note:
        lines.append(f"  {approvals_note}")

    # Planned work: what the operator did on purpose. Context, never a fault.
    if reloads or pauses:
        lines.append("")
        lines.append("Planned work:")
        if reloads:
            lines.append(f"  reloads: {reloads}")
        if pauses:
            ended = [d for d in pauses if d is not None]
            detail = [f"longest {_human_duration(max(ended))}"] if ended else []
            if len(ended) < len(pauses):
                detail.append(f"{len(pauses) - len(ended)} with no end in the log")
            lines.append(f"  maintenance pauses: {len(pauses)} ({'; '.join(detail)})")

    # Recoveries: things that went wrong and were fixed without you.
    recovered = {
        label: guard.get(label, 0) + app.get(label, 0)
        for label in (
            "server restarts", "orphans cleaned up", "port conflicts",
            "answers recovered from checkpoint", "LLM retries",
        )
    }
    recovered = {k: v for k, v in recovered.items() if v}
    if recovered:
        lines.append("")
        lines.append("Recovered without you:")
        lines += [f"  {k}: {v}" for k, v in recovered.items()]

    # Problems that are still problems.
    problems = {
        label: guard.get(label, 0) + app.get(label, 0)
        for label in (
            "failed starts", "crash loops", "refused starts",
            "MCP connection failures",
            "guard alerts not delivered (no credentials)",
            "guard alerts that failed to send",
        )
    }
    problems["turns that ended in an error"] = failed_turns
    problems["approvals that could not resume"] = approvals.get("error", 0)
    problems = {k: v for k, v in problems.items() if v}
    if problems:
        lines.append("")
        lines.append("Needs attention:")
        lines += [f"  {k}: {v}" for k, v in problems.items()]

    if alerts:
        lines.append("")
        lines.append("Alerts raised:")
        ranked = sorted(alerts.items(), key=lambda kv: (-sum(kv[1]), kv[0]))
        for key, (sent, held) in ranked[:6]:
            extra = f" ({count_noun(held, 'repeat')} held back)" if held else ""
            lines.append(f"  {key}: {sent + held}{extra}")
        if len(ranked) > 6:
            lines.append(f"  and {len(ranked) - 6} more (each is in the log)")

    if not recovered and not problems and not alerts:
        lines.append("")
        lines.append("No failures, no restarts, no alerts.")

    return "\n".join(lines)


async def _deliver_digest(text: str) -> str:
    """Send the digest and say what happened: ``delivered``, ``no_channel``
    (nothing is configured to take ops messages) or ``failed``. Never raises.

    It went through ``ops_alerts._dispatch``, which delivers in the
    background and answers nothing, and the day was stamped as sent whatever
    happened: a digest no channel took was never tried again, and the
    silence it left reads as a quiet day -- the one thing the digest exists
    to rule out. Awaited here, on the loop the chat apps' senders belong to
    (AGENTS §33), so the answer is the platforms' own.
    """
    import asyncio

    from kazma_core.observability.ops_alerts import _deliver, _has_any_sink

    try:
        # A settings read (the bot token is a vault pointer): off the loop.
        if not await asyncio.to_thread(_has_any_sink):
            logger.info("[digest] no channel takes ops messages; the digest was not sent")
            return "no_channel"
        delivered = await _deliver(text)
    except Exception as exc:  # noqa: BLE001 -- the cadence must survive any sender
        logger.warning("[digest] delivery raised: %s", exc)
        return "failed"
    if delivered:
        logger.info("[digest] daily digest delivered (%d chars)", len(text))
        return "delivered"
    logger.warning("[digest] daily digest NOT delivered: no channel took it; "
                   "trying again in %d min", int(_RETRY_S // 60))
    return "failed"


#: When the digest was last delivered, so its day survives a restart.
_LAST_SENT_KEY = "observability.daily_digest.last_sent"
#: After boot, a due digest waits this long: never in the middle of startup,
#: and never one per restart (the stamp says it was sent).
_SETTLE_S = 600.0
#: A digest that could not be delivered is tried again this much later.
_RETRY_S = 3600.0


async def _send_due_digest() -> float:
    """Build and deliver the digest that is due. Returns how long to wait
    before looking again: 0 once it is stamped, an hour when it must be
    tried again (or the digest is switched off)."""
    import asyncio

    from kazma_core.observability.cadence import stamp_run

    if not digest_enabled():
        return _RETRY_S
    text = await asyncio.to_thread(build_digest, DIGEST_INTERVAL_HOURS)
    if await _deliver_digest(text) == "failed":
        return _RETRY_S
    await asyncio.to_thread(stamp_run, _LAST_SENT_KEY)
    return 0.0


async def digest_scheduler() -> None:
    """Deliver the digest once per interval, counted from the last delivery.

    It slept a full interval from boot before its first send, so a server
    restarted more often than daily never sent one: not once in the week to
    2026-09-28 on the live install, which reloads several times a day. The
    last delivery is stamped (kazma_core.observability.cadence); a restart
    neither re-sends a digest nor postpones a due one.

    The log reading runs off the event loop; the delivery runs on it
    (ops_alerts delivers through the loop's bus adapters).
    """
    import asyncio

    from kazma_core.observability.cadence import seconds_until_due

    interval = DIGEST_INTERVAL_HOURS * 3600
    while True:
        try:
            await asyncio.sleep(await asyncio.to_thread(
                seconds_until_due, _LAST_SENT_KEY, interval, settle_s=_SETTLE_S,
            ))
            await asyncio.sleep(await _send_due_digest())
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — a failed digest must not
            # kill the cadence; the next one still fires.
            logger.warning("[digest] scheduler iteration failed: %s", exc)
            await asyncio.sleep(300)
