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
* the application log — finished turns, alerts (sent and held back),
  recovery events, YOLO windows and the calls that ran without their
  approval card, rotated files included
* the approval registry (``hitl_gates.db``) — the questions asked

Nothing here queries the LLM, a remote database or the network.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import time
from collections import Counter
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

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

# Application log lines, each read by how its message OPENS: its "[tag] " and
# the words after it, matched at the start of the message (``re.match``).
# A marker matched anywhere in the message also matched text a variable
# carried: on 2026-11-05 the digest reported five "MCP connection failures"
# that were the health check quoting Docker's own "failed to connect to the
# docker API" during a Docker outage, with every MCP server connected. Each
# entry: the needle (a literal the raw line holds, for the quick scan), the
# pattern, the label. A pattern with a group "n" adds that number: the MCP
# summary names how many servers failed, one line per connect.
_APP_LINES: tuple[tuple[str, re.Pattern[str], str], ...] = (
    ("Backfilled unanswered turn",
     re.compile(r"\[SSE\] Backfilled unanswered turn"),
     "answers recovered from checkpoint"),
    ("LLM call attempt",
     re.compile(r"\[[^\]\s]+\] LLM call attempt \d+/\d+ failed"),
     "LLM retries"),
    ("failed to connect",
     re.compile(r"\[MCP\] (?P<n>\d+) server\(s\) failed to connect"),
     "MCP connection failures"),
)

# One line per finished turn, from every transport
# (kazma_ui.turn_runtime._note_finished_turn). Turns were counted from "SSE
# turn complete", which only the web stream writes.
_TURN_MARKER = "[turn] Turn finished:"
# The memory changes the operator made (kazma_ui.memory_actions, Memory audit
# M35): one line each, with the route and the status. The forgets are counted.
_MEMORY_ACTION = "[memory] operator action="
_TURN_LINE = re.compile(r"\[turn\] Turn finished: platform=(\S+) key=(\S+) failed=(\S+)")
# One line per scheduled fire and per delivery outcome (kazma_core.cron.scheduler,
# Scheduled audit SCH-E02/E11): a reminder not delivered, missed or failed was a
# CRITICAL log line and nothing else.
_CRON_MARKER = "[cron] outcome job="
_CRON_LINE = re.compile(r"\[cron\] outcome job=(\S+) outcome=(\S+)")
#: Outcome -> (label, needs attention?).
_CRON_OUTCOMES = {
    "delivery_delivered": ("scheduled messages delivered", False),
    "delivery_undeliverable": ("scheduled messages not delivered", True),
    "delivery_unknown": ("scheduled messages that may not have arrived", True),
    "missed": ("scheduled jobs missed while Kazma was down", True),
    "turn_failed": ("scheduled tasks that failed", True),
    "timed_out": ("scheduled tasks that failed", True),
    "error": ("scheduled tasks that failed", True),
    "hitl_off": ("scheduled tasks not run (approvals off)", True),
}

# Every ops alert leaves this line, sent or held back by its cooldown
# (ops_alerts.alert): "[ops_alert] <key> | <title>[ (throttled)]". The digest
# read the alerts raised since the process started, and the live install
# reloads several times a day: an alert from before the last reload was
# never in a digest. The line's level is the alert's: a success or an info
# notice is logged at INFO and listed under "Notices", never as a problem.
_ALERT_MARKER = "[ops_alert] "
_THROTTLED = " (throttled)"

# What ran without its approval card, and the YOLO windows that let it
# (safety.unasked, safety.yolo). The digest did not say: on 2026-10-31 a chat
# ran with YOLO on for an hour and sent a file unasked, and the digest listed
# the day's approvals as if every call had been asked.
_UNASKED_MARKER = "[HITL] ran without asking:"
_UNASKED_LINE = re.compile(r"\[HITL\] ran without asking: tool=(\S+) thread=(\S+) by=(\S+)")
_YOLO_ON = "[SECURITY] YOLO ENABLED"
_YOLO_ON_LINE = re.compile(
    r"\[SECURITY\] YOLO ENABLED thread=(\S+) actor=(\S+) ttl=(\S+?)s( \(unified clock\))?$"
)
_YOLO_OFF = "[SECURITY] YOLO DISABLED"
_YOLO_OFF_LINE = re.compile(r"\[SECURITY\] YOLO DISABLED thread=(\S+) actor=(\S+)")
# Before 2026-11-01 the swarm bus said it in its own words (and counted the
# card's own call, which ran under the grant the card had just made); the
# chat graph said nothing for a grant.
_LEGACY_YOLO = "YOLO mode active for thread="
_LEGACY_YOLO_LINE = re.compile(r"YOLO mode active for thread=(\S+) \S+ auto-approving: (\S+)")
#: An old-format YOLO approval this soon after its chat's window opened is
#: the call on the card that opened it (live: 0.09 s and 0.15 s; the next
#: call waits for a model reply).
_LEGACY_CARD_S = 2.0
_LEGACY_GRANT = " grant covers "
_LEGACY_GRANT_LINE = re.compile(r"\[Safety\] (task|tool) grant covers (\S+) on thread=(\S+)")
_UNASKED_NEEDLES = (_UNASKED_MARKER, _YOLO_ON, _YOLO_OFF, _LEGACY_YOLO, _LEGACY_GRANT)
_UNASKED_WORDS = {
    "yolo": "under YOLO",
    "tool_grant": 'under "Allow tool"',
    "task_grant": 'under "Approve for task"',
}

#: Markers whose line is always one whole log record.
_ONE_LINE_RECORDS = (_TURN_MARKER, _ALERT_MARKER, _UNASKED_MARKER, _YOLO_ON)
#: Lines per alert section; the rest are named on one line. The digest said
#: "and 1 more (each is in the log)", and a phone holds no log.
_ALERT_LINES = 12

# The application log's plain line (logging_config's "standard" formatter,
# local time). The file is JSON when the ``logging.format`` setting says so;
# a server that set up logging while the settings store could not answer
# wrote plain lines, and the digest read JSON only: on 2026-10-31 it reported
# "Turns completed: 0 ... No failures, no restarts, no alerts" over a day of
# turns and alerts it never read.
_PLAIN_LINE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \[(\w+)\] (\S+): (.*)$")

# How a question in the approval registry ended, in the digest's words.
_OUTCOME_WORDS = {
    "approve": "approved",
    "deny": "denied",
    "yolo": "approved by YOLO mode",
    "timeout": "expired unanswered",
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


def _app_line(line: str) -> tuple[float, dict] | None:
    """``(time, record)`` of one application log line, JSON or plain."""
    if line.startswith("{"):
        try:
            rec = json.loads(line)
            return datetime.fromisoformat(rec["timestamp"]).timestamp(), rec
        except (ValueError, KeyError, TypeError):
            return None
    m = _PLAIN_LINE.match(line.rstrip("\r\n"))
    if m is None:
        return None
    try:
        # Naive: the formatter writes local time, and .timestamp() reads it so.
        ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").timestamp()
    except ValueError:
        return None
    return ts, {"level": m.group(2), "logger": m.group(3), "message": m.group(4)}


def _guard_line(line: str) -> tuple[float, dict] | None:
    """``(time, record)`` of one guard log line (always JSON)."""
    try:
        rec = json.loads(line)
        return datetime.fromisoformat(rec["ts"]).timestamp(), rec
    except (ValueError, KeyError, TypeError):
        return None


def _records(locate, since: float, parse,
             needles: tuple[str, ...], unread: Counter | None = None,
             ) -> Iterator[tuple[float, dict]]:
    """``(time, record)`` for each line in the window holding a needle.

    *locate* returns the live log's path; *parse* reads one line. A log that
    cannot be found or read is skipped and said; the digest still goes out.
    A turn or alert line *parse* cannot read is counted in *unread* (both are
    always one line, so they are never a traceback's continuation): a log
    written in a format the digest does not know is reported, not read as a
    quiet day.
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
                    parsed = parse(line)
                    if parsed is None:
                        if unread is not None and any(n in line for n in _ONE_LINE_RECORDS):
                            unread[log.name] += 1
                        continue
                    if parsed[0] >= since:
                        yield parsed
        except OSError as exc:
            logger.warning("[digest] could not read %s: %s", log.name, exc)


def _scan_guard_log(since: float) -> tuple[Counter, list[float | None], int]:
    """Counted guard events, maintenance pause durations, reloads."""
    counts: Counter = Counter()
    planned: list[tuple[float, str]] = []
    needles = (*_GUARD_EVENTS, _PAUSE_START, _PAUSE_END, _RELOAD)
    for ts, rec in _records(_guard_log_path, since, _guard_line, needles):
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


def _unasked_chat(chats: dict[str, dict], thread: str, ts: float) -> dict:
    chat = chats.setdefault(thread, {"first": ts, "windows": [], "calls": {}})
    chat["first"] = min(chat["first"], ts)
    return chat


def _note_unasked(chats: dict[str, dict], ts: float, msg: str) -> bool:
    """Read one YOLO or ran-without-asking line into *chats*; False when *msg*
    is none of them."""
    m = _UNASKED_LINE.search(msg)
    if m:
        tool, thread, by = m.groups()
        _unasked_chat(chats, thread, ts)["calls"].setdefault(by, Counter())[tool] += 1
        return True
    m = _LEGACY_YOLO_LINE.search(msg)
    if m:
        thread, tool = m.groups()
        windows = chats.get(thread, {}).get("windows") or []
        if windows and 0 <= ts - windows[-1]["on"] <= _LEGACY_CARD_S:
            return True  # the card's own call, run as the window opened
        _unasked_chat(chats, thread, ts)["calls"].setdefault("yolo", Counter())[tool] += 1
        return True
    m = _LEGACY_GRANT_LINE.search(msg)
    if m:
        kind, tool, thread = m.groups()
        _unasked_chat(chats, thread, ts)["calls"].setdefault(f"{kind}_grant", Counter())[tool] += 1
        return True
    m = _YOLO_ON_LINE.search(msg)
    if m:
        thread, actor, ttl, unified = m.groups()
        _unasked_chat(chats, thread, ts)["windows"].append({
            "on": ts, "actor": actor, "ttl": int(ttl) if ttl.isdigit() else None,
            "unified": bool(unified), "off": None,
        })
        return True
    m = _YOLO_OFF_LINE.search(msg)
    if m:
        # Turning off a chat's YOLO is logged whether or not it was on: an
        # "off" ends the window this digest saw open, and is nothing alone.
        windows = chats.get(m.group(1), {}).get("windows") or []
        if windows and windows[-1]["off"] is None and windows[-1]["on"] <= ts:
            windows[-1]["off"] = ts
        return True
    return False


def _scan_app_log(
    since: float, unread: Counter | None = None,
) -> tuple[Counter, dict[str, tuple[str, bool]], dict[str, dict], dict[str, dict]]:
    """Counted markers; each finished turn, key -> (platform, failed); each
    alert key -> its tally (sent, held back, titles, needs attention); and
    each chat in which a call ran without asking or YOLO was turned on ->
    its YOLO windows and those calls by standing approval.

    A turn is counted once by its key: close_turn can run again for a turn
    it closed before a restart.
    """
    counts: Counter = Counter()
    turns: dict[str, tuple[str, bool]] = {}
    alerts: dict[str, dict] = {}
    chats: dict[str, dict] = {}
    needles = (
        *(needle for needle, _pattern, _label in _APP_LINES),
        _TURN_MARKER, _ALERT_MARKER, _MEMORY_ACTION, _CRON_MARKER,
        *_UNASKED_NEEDLES,
    )
    # The live file is read before the rotated ones: a window's "off" is
    # matched to its "on" in time order.
    unasked: list[tuple[float, str]] = []
    for ts, rec in _records(_app_log_path, since, _app_line, needles, unread):
        msg = str(rec.get("message", ""))
        if any(n in msg for n in _UNASKED_NEEDLES):
            unasked.append((ts, msg))
            continue
        turn = _TURN_LINE.search(msg)
        if turn:
            turns[turn.group(2)] = (turn.group(1), turn.group(3) == "yes")
            continue
        cron = _CRON_LINE.search(msg)
        if cron:
            label = _CRON_OUTCOMES.get(cron.group(2), (None, False))[0]
            if label:
                counts[label] += 1
            continue
        if msg.startswith(_MEMORY_ACTION):
            if "/forget " in msg and " status=200" in msg:
                counts["memory forgets"] += 1
            continue
        if msg.startswith(_ALERT_MARKER):
            key, _sep, title = msg[len(_ALERT_MARKER):].partition(" | ")
            held = title.endswith(_THROTTLED)
            if held:
                title = title[: -len(_THROTTLED)]
            tally = alerts.setdefault(key.strip(), {
                "sent": 0, "held": 0, "titles": Counter(), "attention": False,
            })
            tally["held" if held else "sent"] += 1
            if title.strip():
                tally["titles"][title.strip()] += 1
            # A success or an info notice is logged at INFO (ops_alerts.alert).
            if str(rec.get("level", "WARNING")).upper() != "INFO":
                tally["attention"] = True
            continue
        for _needle, pattern, label in _APP_LINES:
            line = pattern.match(msg)
            if line:
                counts[label] += int(line.groupdict().get("n") or 1)
    for ts, msg in sorted(unasked, key=lambda item: item[0]):
        _note_unasked(chats, ts, msg)
    return counts, turns, alerts, chats


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


def _alert_label(key: str, titles: Counter) -> str:
    """What an alert said, in its own words: its title when every occurrence
    had the same one, else its key. The digest listed keys alone, and
    ``acquisition.completed`` (a crawl) beside ``acquisition.done`` (a
    research run) read as one thing counted twice."""
    return next(iter(titles)) if len(titles) == 1 else key


def _alert_lines(tallies: dict[str, dict]) -> list[str]:
    """One line per alert, most frequent first; past ``_ALERT_LINES`` the rest
    are named on one line (never "see the log": the digest is read on a
    phone)."""
    ranked = sorted(tallies.items(), key=lambda kv: (-(kv[1]["sent"] + kv[1]["held"]), kv[0]))
    lines: list[str] = []
    for key, tally in ranked[:_ALERT_LINES]:
        held = tally["held"]
        extra = f" ({count_noun(held, 'repeat')} held back)" if held else ""
        lines.append(f"  {_alert_label(key, tally['titles'])}: {tally['sent'] + held}{extra}")
    rest = ranked[_ALERT_LINES:]
    if rest:
        named = ", ".join(f"{key} {t['sent'] + t['held']}" for key, t in rest)
        lines.append(f"  and {len(rest)} more: {named}")
    return lines


def _operator_zone() -> tuple[Any, str]:
    """``(zone, name)`` the digest's times are in: the operator's (Scheduled's
    ``cron.timezone``, else ``KAZMA_TZ``), else UTC. Never raises: an
    unreadable setting or an unknown zone is UTC there."""
    from kazma_core.cron.scheduler import resolve_cron_zone

    return resolve_cron_zone()


def _window_text(window: dict, zone: Any) -> str:
    """One YOLO window: when it opened, for how long, who turned it on."""
    def at(ts: float) -> str:
        return datetime.fromtimestamp(ts, zone).strftime("%H:%M")

    ttl = window["ttl"]
    if ttl is None:
        length = "with no end time"
    elif window["unified"]:
        length = f"for {_human_duration(ttl)} from each message"
    else:
        length = f"for {_human_duration(ttl)}"
    text = f"YOLO on at {at(window['on'])} {length}, by {window['actor']}"
    if window["off"] is not None:
        text += f", off at {at(window['off'])}"
    return text


def _unasked_lines(chats: dict[str, dict], zone: Any, zone_name: str) -> list[str]:
    """The section: every chat where a call ran without its card or YOLO was
    turned on, oldest first, with what ran under which standing approval."""
    total = sum(sum(c.values()) for chat in chats.values() for c in chat["calls"].values())
    lines = [f"Ran without asking: {count_noun(total, 'call')} (times in {zone_name})"]
    for thread, chat in sorted(chats.items(), key=lambda kv: (kv[1]["first"], kv[0])):
        lines.append(f"  chat {thread[:8]}:")
        lines += [f"    {_window_text(w, zone)}" for w in chat["windows"]]
        calls = chat["calls"]
        for by, words in _UNASKED_WORDS.items():
            if calls.get(by):
                lines.append(f"    {words}: {_split(calls[by])}")
        for by in sorted(set(calls) - set(_UNASKED_WORDS)):
            lines.append(f"    under {by}: {_split(calls[by])}")
        if not any(calls.values()):
            lines.append("    nothing ran without asking")
    return lines


def _split(counts: Counter) -> str:
    """Counts as "web 3, telegram 2": largest first, ties by name."""
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return ", ".join(f"{name} {n}" for name, n in ranked)


#: Open advisories named one by one; past this, the rest are counted.
_ADVISORIES_SHOWN = 10


def _package_lines() -> list[str]:
    """Every unmet requirement and open dependency advisory, in every digest
    until it is fixed (roadmap theme 12). The boot check alerts once per boot
    and the advisory check once per advisory, and a cooldown or one missed
    message let them drop out of sight; the digest keeps them in front of
    the owner. A store that cannot be read is said, never taken for none."""
    from kazma_core.db.pg_helpers import store_errors
    from kazma_core.install_requirements import last_requirements_check
    from kazma_core.security.advisory_watch import open_advisories

    lines: list[str] = []
    try:
        check = last_requirements_check()
    except store_errors():  # said in the digest, never a silent "none"
        logger.debug("[digest] the requirements check could not be read", exc_info=True)
        check = {}
        lines.append("  the last requirements check could not be read")
    unmet = [str(u) for u in check.get("unmet") or ()]
    if unmet:
        lines.append(f"  {count_noun(len(unmet), 'installed package')} older than this build requires:")
        lines += [f"    {u}" for u in unmet]
        if check.get("fix"):
            lines.append(f"    {check['fix']}")
    try:
        advisories = open_advisories()
    except store_errors():
        logger.debug("[digest] the advisory check could not be read", exc_info=True)
        advisories = []
        lines.append("  the last dependency advisory check could not be read")
    if advisories:
        lines.append(f"  {count_noun(len(advisories), 'dependency advisory', 'dependency advisories')} still open:")
        for item in advisories[:_ADVISORIES_SHOWN]:
            lines.append(f"    {item.get('package')} {item.get('version')}: {item.get('id')} -- {item.get('fix', '')}")
        if len(advisories) > _ADVISORIES_SHOWN:
            lines.append(f"    and {len(advisories) - _ADVISORIES_SHOWN} more (the security report lists them)")
    return lines


def _outside_watch_line() -> str | None:
    """The outside watch's line, or None when the install is not paired."""
    from kazma_core.observability.outside_watch import outside_digest_line

    try:
        return outside_digest_line()
    except (OSError, ValueError, RuntimeError) as exc:
        logger.warning("[digest] the outside watch could not be read: %s", type(exc).__name__)
        return "Outside watch: could not be read"


def build_digest(hours: float = DIGEST_INTERVAL_HOURS) -> str:
    """Render the digest. A log or the approval registry that cannot be read
    is skipped and said, so a usable digest goes out regardless."""
    since = (datetime.now(UTC) - timedelta(hours=hours)).timestamp()
    window = f"last {int(hours)}h"

    unread: Counter = Counter()
    app, turns, alerts, unasked = _scan_app_log(since, unread)
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
    delivered = app.get("scheduled messages delivered", 0)
    if delivered:
        lines.append(f"Scheduled messages delivered: {delivered}")
    # How the install looked from outside the machine this week (resilience
    # plan B1), only once it is paired with the observer.
    outside = _outside_watch_line()
    if outside:
        lines.append(outside)

    # Planned work: what the operator did on purpose. Context, never a fault.
    forgets = app.get("memory forgets", 0)
    if reloads or pauses or forgets:
        lines.append("")
        lines.append("Planned work:")
        if reloads:
            lines.append(f"  reloads: {reloads}")
        if forgets:
            lines.append(f"  memories forgotten from the Memory page: {forgets}")
        if pauses:
            ended = [d for d in pauses if d is not None]
            detail = [f"longest {_human_duration(max(ended))}"] if ended else []
            if len(ended) < len(pauses):
                detail.append(f"{len(pauses) - len(ended)} with no end in the log")
            lines.append(f"  maintenance pauses: {len(pauses)} ({'; '.join(detail)})")

    # What ran without its card: the operator's choice, never a fault, but
    # the approvals above are not the whole story without it.
    if unasked:
        lines.append("")
        lines += _unasked_lines(unasked, *_operator_zone())

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
    problems["log lines this digest could not read"] = sum(unread.values())
    for label, attention in sorted({v for v in _CRON_OUTCOMES.values()}):
        if attention:
            problems[label] = app.get(label, 0)
    # The Knowledge Library's last vector repair pass (KB-E11).
    from kazma_core.stores.knowledge_index import digest_problems

    problems.update(digest_problems())
    problems = {k: v for k, v in problems.items() if v}
    packages = _package_lines()
    if problems or packages:
        lines.append("")
        lines.append("Needs attention:")
        lines += [f"  {k}: {v}" for k, v in problems.items()]
        lines += packages

    raised = {k: t for k, t in alerts.items() if t["attention"]}
    notices = {k: t for k, t in alerts.items() if not t["attention"]}
    for heading, group in (("Alerts raised:", raised), ("Notices:", notices)):
        if group:
            lines.append("")
            lines.append(heading)
            lines += _alert_lines(group)

    if not recovered and not problems and not packages and not raised:
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


#: When the daily digest is sent, in the operator's time zone (``cron.timezone``),
#: "HH:MM" (2026-11, FA-208).
DIGEST_TIME_KEY = "observability.daily_digest.time"
_DEFAULT_DIGEST_TIME = (8, 0)
#: A digest sent this long before its time counts for it: the one sent on the
#: old drifting schedule earlier the same morning is not sent again.
_EARLY_COUNTS_S = 12 * 3600.0


def _digest_time() -> tuple[int, int]:
    """The set time of day, ``(hour, minute)``; 08:00 when unset or invalid."""
    try:
        from kazma_core.config_store import get_config_store

        raw = str(get_config_store().get(DIGEST_TIME_KEY) or "").strip()
    except Exception:  # noqa: BLE001 - an unreadable setting keeps the default time
        logger.debug("[digest] the digest time could not be read", exc_info=True)
        return _DEFAULT_DIGEST_TIME
    hour, _, minute = raw.partition(":")
    try:
        h, m = int(hour), int(minute or 0)
    except ValueError:
        return _DEFAULT_DIGEST_TIME
    return (h, m) if 0 <= h < 24 and 0 <= m < 60 else _DEFAULT_DIGEST_TIME


def _seconds_until_digest(now: float | None = None) -> float:
    """How long until the next digest is due (blocking: settings reads).

    Daily, at :data:`DIGEST_TIME_KEY` in the operator's zone (2026-11,
    FA-208): it was "24 hours after the last one", so each restart's settle
    and each retry pushed it later, and the morning digest drifted into the
    afternoon. A digest whose time has passed and that was not sent is due
    now (``_SETTLE_S`` after a boot); otherwise the wait is to the next
    time. ``KAZMA_DIGEST_INTERVAL_HOURS`` other than 24 keeps the interval.
    """
    from datetime import datetime, timedelta

    from kazma_core.cron.scheduler import resolve_cron_zone
    from kazma_core.observability.cadence import last_run, seconds_until_due

    if DIGEST_INTERVAL_HOURS != 24:
        return seconds_until_due(_LAST_SENT_KEY, DIGEST_INTERVAL_HOURS * 3600, settle_s=_SETTLE_S, now=now)
    zone, _name = resolve_cron_zone()
    now_ts = time.time() if now is None else now
    local = datetime.fromtimestamp(now_ts, zone)
    hour, minute = _digest_time()
    slot = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if local < slot:
        slot -= timedelta(days=1)  # today's time is still ahead: the last slot was yesterday's
    sent = last_run(_LAST_SENT_KEY)
    if sent is None or sent < slot.timestamp() - _EARLY_COUNTS_S:
        return _SETTLE_S  # the last slot's digest was never sent
    following = (slot + timedelta(days=1)).timestamp()
    return max(60.0, following - now_ts)


async def digest_scheduler() -> None:
    """Deliver the digest daily at its set time, whatever the restarts.

    It slept a full interval from boot before its first send, so a server
    restarted more often than daily never sent one: not once in the week to
    2026-09-28 on the live install, which reloads several times a day. The
    last delivery is stamped (kazma_core.observability.cadence); a restart
    neither re-sends a digest nor postpones a due one, and the time of day
    no longer drifts (:func:`_seconds_until_digest`).

    The log reading runs off the event loop; the delivery runs on it
    (ops_alerts delivers through the loop's bus adapters). The waits end at
    once when the server begins to stop (FA-265).
    """
    import asyncio

    from kazma_core.db import availability
    from kazma_core.shutdown import sleep_or_shutdown

    while True:
        try:
            # An unreadable last-sent stamp reads as "never sent": a check
            # during an outage would send the day's digest again.
            if await availability.wait_while_away():
                return
            if await sleep_or_shutdown(await asyncio.to_thread(_seconds_until_digest)):
                return
            if await availability.wait_while_away():
                return
            if await sleep_or_shutdown(await _send_due_digest()):
                return
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — a failed digest must not
            # kill the cadence; the next one still fires.
            logger.warning("[digest] scheduler iteration failed: %s", exc)
            if await sleep_or_shutdown(300):
                return
