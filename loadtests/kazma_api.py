"""The Kazma HTTP API as the load tests call it — one place.

The locustfiles were written in July against an API that did not exist:
dispatches sent ``prompt`` (the route reads ``task``) and got a 400 on every
call, polled routes that 404ed, and read response keys no route returns. So
the load tests measured error paths. Every request shape the load tests send
is built here, and ``tests/test_loadtest_routes.py`` checks it — and every
path the load tests call — against the real app.

Locust puts the locustfile's folder on ``sys.path``, so ``import kazma_api``
works when running ``locust -f loadtests/locustfile_*.py``.
"""

from __future__ import annotations

from typing import Any

#: Patterns that need no named worker: ``auto`` spawns one from the shipped
#: templates (coder / researcher / generalist, AGENTS §14). Pipeline, consult
#: and conditional need registered workers or routes a fresh install lacks.
DISPATCH_PATTERNS: tuple[str, ...] = ("dispatch", "fan_out", "broadcast")

#: Swarm task states a checkpoint pause and a finished task report.
PAUSED = "paused"
TERMINAL = frozenset({"completed", "failed", "timeout", "cancelled"})


def dispatch_body(task: str, *, pattern: str = "dispatch", background: bool = True) -> dict[str, Any]:
    """The body ``POST /api/swarm/dispatch`` accepts.

    ``background`` returns the task id at once instead of holding the request
    open for the whole task — a load test measures dispatch, then polls or
    streams the task separately.
    """
    if pattern not in DISPATCH_PATTERNS:
        raise ValueError(f"unsupported load-test pattern: {pattern!r}")
    return {"task": task, "workers": ["auto"], "pattern": pattern, "background": background}


def chat_body(message: str, session_id: str) -> dict[str, Any]:
    """The body ``POST /api/chat/stream`` reads (``message`` + ``session_id``)."""
    return {"message": message, "session_id": session_id}


def task_status(detail_json: dict[str, Any]) -> str:
    """The status from ``GET /api/swarm/tasks/{id}`` (``{"task": {...}}``)."""
    task = detail_json.get("task") if isinstance(detail_json, dict) else None
    return str((task or {}).get("status") or "").lower()
