"""Bounded, text-free X health and acknowledged incident transition notices."""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)
_lock = threading.Lock()
_cycles: dict[str, dict[str, Any]] = {}


def record_cycle(name: str, *, success: bool, interval: float) -> None:
    """Run off-loop. Three consecutive failures alert once, recovery once."""
    if name not in ("mentions", "scheduler"):
        raise ValueError("Unknown X loop.")
    now = time.time()
    with _lock:
        previous = _cycles.get(name, {})
        failures = 0 if success else previous.get("failures", 0) + 1
        incident = previous.get("incident", False)
        transition = ("recovered" if success else "failed") if (success and incident) or (failures >= 3 and not incident) else ""
        _cycles[name] = {"last_cycle_at": now, "last_success_at": now if success else previous.get("last_success_at"),
                         "failures": failures, "incident": failures >= 3 or (incident and not success),
                         "last_monotonic": time.monotonic(), "interval": interval,
                         "successful_cycles": previous.get("successful_cycles", 0) + int(success)}
    if transition:
        from kazma_core.x_api.notifications import enqueue
        from kazma_core.x_api.publication_store import get_publication_store

        try:
            with get_publication_store()._connection(transaction=True) as conn:
                enqueue(conn, tenant="default", key=f"health:{name}:{transition}:{now}",
                        message=f"X {name} loop {transition}. Inspect X Studio health and the X operations runbook.")
        except (sqlite3.Error, OSError, RuntimeError):
            logger.exception("X health incident notice could not be persisted")


def _loop_health(name: str, task: Any) -> dict[str, Any]:
    with _lock:
        result = dict(_cycles.get(name, {}))
    running = task is not None and not task.done()
    mark = result.pop("last_monotonic", None)
    result["running"] = running
    result["stale"] = bool(running and mark is not None and time.monotonic() - mark > max(120, result.get("interval", 30) * 2))
    result["observed"] = mark is not None
    return result


def _reply_metrics(tenant: str) -> dict[str, Any]:
    from kazma_core.x_api.reply_store import get_reply_store

    store = get_reply_store()
    with store._lock:
        conn = store._connect()
        try:
            states = {row["status"]: row["n"] for row in conn.execute("SELECT status, COUNT(*) AS n FROM x_replies WHERE tenant_id = ? GROUP BY status", (tenant,))}
            notices = conn.execute("SELECT COUNT(*) AS n, MIN(created_at) AS oldest FROM x_notification_outbox WHERE tenant_id = ? AND delivered_at IS NULL AND next_attempt < 1000000000000", (tenant,)).fetchone()
            usage = conn.execute("SELECT SUM(json_extract(decision_json, '$.usage.calls')) AS calls, SUM(json_extract(decision_json, '$.usage.output_tokens')) AS tokens, "
                                 "SUM(json_extract(decision_json, '$.usage.missing_usage')) AS missing, SUM(json_extract(decision_json, '$.usage.failed_calls')) AS failures "
                                 "FROM x_replies WHERE tenant_id = ? AND json_valid(decision_json)", (tenant,)).fetchone()
            checks = {row["verdict"]: row["n"] for row in conn.execute("SELECT json_extract(checks.value, '$.verdict') AS verdict, COUNT(*) AS n "
                       "FROM x_replies, json_each(CASE WHEN json_valid(decision_json) THEN json_extract(decision_json, '$.checks') ELSE '[]' END) AS checks "
                       "WHERE tenant_id = ? AND checks.type = 'object' GROUP BY verdict", (tenant,)) if row["verdict"] in ("pass", "fail", "unknown")}
            return {"states": states, "pending_notifications": notices["n"], "oldest_notification_at": notices["oldest"],
                    "model_calls": usage["calls"] or 0, "output_tokens": usage["tokens"] or 0,
                    "missing_usage": usage["missing"] or 0, "failed_calls": usage["failures"] or 0, "verification_verdicts": checks}
        finally:
            conn.close()


def health_snapshot() -> dict[str, Any]:
    from kazma_core.x_api.config import get_x_config
    from kazma_core.x_api.mentions_fire import get_mentions_task
    from kazma_core.x_api.ownership import x_tenant_id
    from kazma_core.x_api.publication_store import get_publication_store
    from kazma_core.x_api.scheduled_fire import get_scheduled_x_task

    cfg, tenant = get_x_config(), x_tenant_id()
    store = get_publication_store()
    publication = store.summary(tenant_id=tenant, account_id=cfg.account_id)
    with store._connection() as conn:
        repairs = conn.execute("SELECT MIN(created_at) FROM x_projection_outbox WHERE tenant_id = ? AND done_at IS NULL", (tenant,)).fetchone()[0]
        lag = conn.execute("SELECT MAX(published_at - due_at) FROM x_operations WHERE tenant_id = ? AND origin = 'schedule' AND state = 'published'", (tenant,)).fetchone()[0]
    return {"measured_at": time.time(), "loops": {"mentions": _loop_health("mentions", get_mentions_task()),
                "scheduler": _loop_health("scheduler", get_scheduled_x_task())},
            "publication": {**publication, "oldest_repair_at": repairs, "max_schedule_lateness_s": lag},
            "replies": _reply_metrics(tenant),
            "capabilities": {"account_verified": bool(cfg.account_id), "transport_enabled": cfg.can_post(),
                             "write_access": "not_attested_by_account_read", "media": "disabled_pending_capability_verification",
                             "analytics": "disabled_pending_capability_verification"},
            "scope": "tenant counters; process loop heartbeats; mentions loop serves default tenant"}
