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

Sources are all cheap and local:

* ``ops_alerts.alert_state()`` — what has fired since this process started
* the supervisor's own log — restarts, refusals, orphan reaps
* the application log — turn counts and recovery events

Nothing here queries the LLM, the database or the network.
"""

from __future__ import annotations

import json
import logging
import os
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = ["build_digest", "send_digest", "digest_enabled"]

DIGEST_INTERVAL_HOURS = float(os.environ.get("KAZMA_DIGEST_INTERVAL_HOURS", "24"))

# Guard events worth counting in a daily summary. Anything not listed is
# noise for this purpose -- the log remains the full record.
_GUARD_EVENTS = {
    "guard.restarting": "server restarts",
    "child.never_ready": "failed starts",
    "orphan.reaped": "orphans cleaned up",
    "guard.crash_loop": "crash loops",
    "guard.refused_to_start": "refused starts",
    "maintenance.active": "maintenance pauses",
    "child.foreign_server_holds_port": "port conflicts",
}

# Application log markers. Substring match on the message, because these
# lines are formatted with %-args and the values vary per turn.
_APP_MARKERS = {
    "SSE turn complete": "turns completed",
    "HITL interrupt": "approvals requested",
    "Backfilled unanswered turn": "answers recovered from checkpoint",
    "Detached turn completed": "turns finished after you left",
    "LLM call attempt": "LLM retries",
    "failed to connect": "MCP connection failures",
}


def digest_enabled() -> bool:
    raw = os.environ.get("KAZMA_DAILY_DIGEST", "").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _guard_log_path() -> Path:
    env = os.environ.get("KAZMA_GUARD_LOG")
    if env:
        return Path(env)
    try:
        from kazma_core.paths import user_home

        return Path(user_home()) / "guard.log"
    except Exception:  # noqa: BLE001
        return Path.cwd() / ".kazma" / "guard.log"


def _app_log_path() -> Path:
    env = os.environ.get("KAZMA_LOG_FILE")
    if env:
        return Path(env)
    try:
        from kazma_core.paths import log_file

        return log_file()
    except Exception:  # noqa: BLE001
        return Path.cwd() / ".kazma" / "kazma.log"


def _scan_guard_log(since: float) -> Counter:
    counts: Counter = Counter()
    path = _guard_log_path()
    try:
        if not path.exists():
            return counts
        with path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    ts = datetime.fromisoformat(rec["ts"]).timestamp()
                except Exception:
                    continue
                if ts < since:
                    continue
                label = _GUARD_EVENTS.get(str(rec.get("event", "")))
                if label:
                    counts[label] += 1
    except Exception as exc:  # noqa: BLE001
        logger.debug("[digest] guard log scan failed: %s", exc)
    return counts


def _scan_app_log(since: float) -> Counter:
    counts: Counter = Counter()
    path = _app_log_path()
    try:
        if not path.exists():
            return counts
        with path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    ts = datetime.fromisoformat(rec["timestamp"]).timestamp()
                    msg = str(rec.get("message", ""))
                except Exception:
                    continue
                if ts < since:
                    continue
                for marker, label in _APP_MARKERS.items():
                    if marker in msg:
                        counts[label] += 1
    except Exception as exc:  # noqa: BLE001
        logger.debug("[digest] app log scan failed: %s", exc)
    return counts


def build_digest(hours: float = DIGEST_INTERVAL_HOURS) -> str:
    """Render the digest. Never raises; returns a usable string regardless."""
    since = (datetime.now(UTC) - timedelta(hours=hours)).timestamp()
    window = f"last {int(hours)}h"

    app = _scan_app_log(since)
    guard = _scan_guard_log(since)

    alerts: dict[str, dict] = {}
    try:
        from kazma_core.observability.ops_alerts import alert_state

        alerts = alert_state()
    except Exception:
        alerts = {}

    lines = ["\U0001f4c5 Kazma — Info", f"Window: {window}"]

    turns = app.get("turns completed", 0)
    lines.append("")
    lines.append(f"Turns completed: {turns}")
    for label in ("approvals requested", "turns finished after you left"):
        if app.get(label):
            lines.append(f"  {label}: {app[label]}")

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
            "MCP connection failures", "maintenance pauses",
        )
    }
    problems = {k: v for k, v in problems.items() if v}
    if problems:
        lines.append("")
        lines.append("Needs attention:")
        lines += [f"  {k}: {v}" for k, v in problems.items()]

    if alerts:
        lines.append("")
        lines.append("Alerts raised (since restart):")
        for key, st in sorted(alerts.items(), key=lambda kv: -kv[1]["total"])[:6]:
            extra = f" ({st['suppressed']} suppressed)" if st.get("suppressed") else ""
            lines.append(f"  {key}: {st['total']}{extra}")

    if not recovered and not problems and not alerts:
        lines.append("")
        lines.append("No failures, no restarts, no alerts.")

    lines.append("Ops")
    return "\n".join(lines)


def send_digest(hours: float = DIGEST_INTERVAL_HOURS) -> bool:
    """Build and deliver the digest. Never raises."""
    try:
        if not digest_enabled():
            return False
        text = build_digest(hours)
        # Reuse the alert delivery path: it already falls back to a direct
        # Telegram send when no platform bus exists in this process.
        from kazma_core.observability.ops_alerts import _dispatch

        _dispatch(text)
        logger.info("[digest] daily digest dispatched (%d chars)", len(text))
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("[digest] failed to send: %s", exc)
        return False


async def digest_scheduler() -> None:
    """Fire the digest once per interval. Fire-and-forget, crash-isolated.

    Sleeps first: a digest sent at every boot would fire on each restart,
    which during an incident is precisely when the operator least needs
    another message.
    """
    import asyncio

    while True:
        try:
            await asyncio.sleep(DIGEST_INTERVAL_HOURS * 3600)
            send_digest()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — a failed digest must not
            # kill the cadence; the next one still fires.
            logger.warning("[digest] scheduler iteration failed: %s", exc)
            await asyncio.sleep(300)
