"""The maintenance sweeps as runs (read-only).

Source of truth: the maintenance runner's record of each sweep's last outcome
(``worker_bootstrap.sweep_health``: last success, last failure, failures in a
row). A sweep failing three passes in a row is a feature that is off
(``sweep_failing`` in memory health); it is shown as failed and paged, with
Details only: a sweep runs again by itself every pass, and there is no safe
button for "make it work".
"""

from __future__ import annotations

from typing import Any

from kazma_core.runs.adapters._common import short_title
from kazma_core.runs.contract import ActionResult, Run, RunState

__all__ = ["ADAPTER"]

#: Failed passes in a row that make a sweep "failing" (memory health's rule).
FAILING_AFTER = 3


class _MaintenanceAdapter:
    STATE_MAP: dict[str, str] = {
        "ok": RunState.DONE,
        "retrying": RunState.RUNNING,
        "failing": RunState.FAILED,
    }

    async def list_runs(self, now: float, *, window_s: float) -> list[Run]:
        from kazma_core.memory.worker_bootstrap import sweep_health

        return [self._run(label, state) for label, state in sweep_health().items()
                if int(state.get("consecutive_failures") or 0) > 0]

    def _run(self, label: str, state: dict[str, Any]) -> Run:
        failures = int(state.get("consecutive_failures") or 0)
        native = "failing" if failures >= FAILING_AFTER else ("retrying" if failures else "ok")
        last = state.get("last_failed_at") or state.get("last_ok_at")
        return Run(
            run_id=f"maintenance:{label}", kind="maintenance", native_id=label,
            title=label, state=RunState(self.STATE_MAP[native]), native_state=native,
            started_at=float(last) if last else None,
            progress_at=float(state["last_ok_at"]) if state.get("last_ok_at") else None,
            progress_note=f"{failures} failed pass(es) in a row",
            ended_at=float(last) if native == "failing" and last else None,
            version=str(failures), link="/memory",
            detail=short_title(state.get("last_error") or "", 200) if native == "failing" else "",
        )

    async def act(self, native_id: str, action: str, *, actor: str) -> ActionResult:
        return ActionResult(False, "read_only", "A maintenance sweep runs again by itself every pass.")


ADAPTER = _MaintenanceAdapter()
