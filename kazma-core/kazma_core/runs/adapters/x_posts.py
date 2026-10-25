"""X publication operations as runs.

Source of truth: the X publication store (``x_operations``). An operation is a
run while it is sent (``sending``), while it waits for the owner
(``awaiting_approval``), and afterwards when it ended badly or its outcome is
unknown. A booking waiting for its time (``scheduled`` / ``deferred``) is a
plan, shown in X Studio and on the Scheduled page, not here.

A ``sending`` operation's owner is alive while its lease runs. An unknown
outcome is never sent again (§29C): it is shown, with its effects unknown.
The publication notices already announce failures. Cancel is X Studio's
(``publication_service.update_schedule``, bound to the operation's version),
for a booking still waiting for the owner.
"""

from __future__ import annotations

import asyncio
from typing import Any

from kazma_core.runs.adapters._common import short_title
from kazma_core.runs.contract import ActionResult, Run, RunState

__all__ = ["ADAPTER"]

_LISTED = ("sending", "awaiting_approval", "outcome_unknown", "failed_permanent", "expired",
           "published", "cancelled")


def _store_and_tenants() -> tuple[Any, list[str]]:
    """The publication store and the tenants whose operations are watched.

    The request's tenant when one is bound; else every tenant holding an
    operation, as the X background workers read them. The supervisor runs
    with no request tenant, and in production ``x_tenant_id()`` refuses that
    (live 2026-10-25: every pass failed "X requires an authenticated tenant
    context"). Run supervision refuses installs with platform users (D1), so
    every tenant here is the one operator's.
    """
    from kazma_core.tenant_context import get_current_tenant_id
    from kazma_core.x_api.publication_store import get_publication_store

    store = get_publication_store()
    bound = str(get_current_tenant_id() or "").strip()
    return store, [bound] if bound else store.operation_tenants()


class _XAdapter:
    STATE_MAP: dict[str, str] = {
        "scheduled": RunState.QUEUED,
        "deferred": RunState.QUEUED,
        "awaiting_approval": RunState.WAITING_HUMAN,
        "sending": RunState.RUNNING,
        "outcome_unknown": RunState.UNKNOWN,
        "published": RunState.DONE,
        "cancelled": RunState.CANCELLED,
        "failed_permanent": RunState.FAILED,
        "expired": RunState.FAILED,
        "failed": RunState.FAILED,
    }

    async def list_runs(self, now: float, *, window_s: float) -> list[Run]:
        return await asyncio.to_thread(self._read, now, window_s)

    def _read(self, now: float, window_s: float) -> list[Run]:
        store, tenants = _store_and_tenants()
        runs: list[Run] = []
        for tenant in tenants:
            for state in _LISTED:
                for row in store.list_operations(tenant_id=tenant, state=state, limit=50):
                    ongoing = state in ("sending", "awaiting_approval", "outcome_unknown")
                    if not ongoing and float(row["updated_at"] or 0.0) < now - window_s:
                        continue
                    runs.append(self._run(row, now))
        return runs

    def _run(self, row: dict[str, Any], now: float) -> Run:
        native = str(row["state"])
        state = RunState(self.STATE_MAP.get(native, RunState.UNKNOWN))
        owner = None
        if native == "sending":
            owner = bool(row.get("lease_until")) and float(row["lease_until"]) > now
        effects = {"sending": "unknown", "outcome_unknown": "unknown", "published": "known"}.get(native, "none")
        actions: tuple[str, ...] = ()
        if native == "awaiting_approval" and row.get("origin") == "schedule":
            actions = ("cancel",)
        terminal = state in (RunState.DONE, RunState.FAILED, RunState.CANCELLED)
        return Run(
            run_id=f"x_post:{row['id']}", kind="x_post", native_id=str(row["id"]),
            title=short_title(row["text"]), state=state, native_state=native,
            started_at=float(row.get("sent_at") or row["created_at"] or 0.0) or None,
            progress_at=float(row["updated_at"] or 0.0) or None,
            progress_note=f"{int(row.get('attempts') or 0)} attempt(s)",
            owner_alive=owner, ended_at=float(row["updated_at"] or 0.0) if terminal else None,
            effects=effects, version=str(row["version"]), actions=actions, link="/x",
            detail=short_title(row.get("reason") or row.get("outcome") or "", 200)
            if state in (RunState.FAILED, RunState.UNKNOWN, RunState.WAITING_HUMAN) else "",
        )

    async def act(self, native_id: str, action: str, *, actor: str) -> ActionResult:
        if action != "cancel":
            return ActionResult(False, "not_offered", f"X posts do not take {action}.")
        return await asyncio.to_thread(self._cancel, native_id)

    @staticmethod
    def _cancel(native_id: str) -> ActionResult:
        from kazma_core.tenant_context import tenant_scope
        from kazma_core.x_api.publication_service import update_schedule

        store, tenants = _store_and_tenants()
        for tenant in tenants:
            row = store.get(native_id, tenant_id=tenant)
            if row is None:
                continue
            # X Studio's own control, in the operation's own tenant.
            with tenant_scope(tenant):
                changed = update_schedule(native_id, expected_version=int(row["version"]), action="cancel")
            if changed:
                return ActionResult(True, "cancelled", "The booking is cancelled.")
            return ActionResult(False, "conflict", "The booking changed or can no longer be cancelled.")
        return ActionResult(False, "not_found", "No such X post.")


ADAPTER = _XAdapter()
