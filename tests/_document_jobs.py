"""Waiting for a document job: the one way the document tests do it.

A document is parsed in a fresh subprocess, so a parse takes seconds alone
and far longer while a full run keeps every core busy. Waits of 10-20 s
failed a test whose document was still parsing
(``test_convert_denies_other_actor``, 2026-10-02, eight chunks in parallel;
the file passed alone). The deadline guards against a job that never ends;
the wait returns the moment the job reaches its state, so a generous one
costs nothing when the parse is quick.
"""

from __future__ import annotations

import asyncio
from typing import Any

#: Long enough for a parse on a loaded machine; a hung job still fails.
PARSE_DEADLINE_S = 120.0

#: States a job does not leave.
_FINAL_STATES = frozenset({"ready", "rejected", "dead_letter", "cancelled"})


async def wait_for_job(
    svc: Any,
    tenant: str,
    job_id: Any,
    expected: str = "ready",
    *,
    timeout: float = PARSE_DEADLINE_S,
) -> dict[str, Any]:
    """The job's status once it is in *expected*; fails as soon as it ends elsewhere."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    last = None
    while loop.time() < deadline:
        status = await asyncio.to_thread(svc.job_status, tenant_id=tenant, job_id=job_id)
        last = status
        if status is not None and status["state"] == expected:
            return status
        if status is not None and status["state"] in _FINAL_STATES:
            raise AssertionError(f"job ended in {status['state']}, not {expected}; last={last}")
        await asyncio.sleep(0.05)
    raise AssertionError(f"job did not reach {expected} within {timeout:.0f} s; last={last}")
