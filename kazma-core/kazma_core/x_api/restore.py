"""Restored publishing authority requires an explicit verified-account resume."""

from __future__ import annotations

import time


def restore_paused() -> bool:
    from kazma_core.config_store import get_config_store

    return bool(get_config_store().get("system.x.restore_paused", False))


def pause_restored_publishing() -> None:
    """Mark every new build paused before restored queues can run."""
    from kazma_core.config_store import get_config_store
    from kazma_core.x_api.publication_store import get_publication_store

    get_config_store().batch_set([
        ("system.x.restore_paused", True, "system"),
        ("system.x.restore_verification_after", time.time(), "system"),
    ])
    store = get_publication_store()
    with store._connection(transaction=True) as conn:
        conn.execute("UPDATE x_operations SET state = CASE WHEN state = 'sending' THEN 'outcome_unknown' "
                     "ELSE 'awaiting_approval' END, reason = 'Restored publication: review account and reschedule; no automatic replay.', "
                     "outcome = CASE WHEN state = 'sending' THEN 'unknown' ELSE outcome END, "
                     "updated_at = ?, version = version + 1 WHERE state IN ('scheduled', 'deferred', 'sending')", (time.time(),))
        for row in conn.execute("SELECT * FROM x_operations WHERE reason LIKE 'Restored publication:%'").fetchall():
            store._event(conn, row)
        conn.execute("UPDATE x_threads SET state = 'review', revision = revision + 1, token = '', expires_at = 0, "
                     "owner = '', lease_until = 0, updated_at = ? WHERE state NOT IN ('published', 'cancelled')", (time.time(),))

    from kazma_core.x_api.schedule import get_x_scheduled_store

    legacy = get_x_scheduled_store()
    with legacy._lock:
        conn = legacy._connect()
        try:
            conn.execute("UPDATE x_scheduled_posts SET status = CASE WHEN status = 'sending' THEN 'outcome_unknown' ELSE 'held' END, "
                         "error = 'Restored schedule: review account and rebook.' WHERE status IN ('pending', 'sending')")
            conn.commit()
        finally:
            conn.close()


def resume_verified_account() -> None:
    """Resume new work; old held/unknown operations are never released here."""
    from kazma_core.config_store import get_config_store
    from kazma_core.x_api.config import get_x_config
    from kazma_core.x_api.ownership import x_config_key

    account = get_config_store().get(x_config_key("connectors.x.account")) or {}
    restored_at = get_config_store().get("system.x.restore_verification_after", 0)
    if (not isinstance(account, dict) or type(account.get("verified_at")) not in (int, float)
            or not account["verified_at"] >= restored_at):
        raise ValueError("Test the connected account in Settings after this restore before resuming publishing.")
    cfg = get_x_config()
    if not cfg.account_id or not cfg.credentials.complete():
        raise ValueError("Verify the connected X account in Settings before resuming publishing.")
    get_config_store().set("system.x.restore_paused", False, category="system")
