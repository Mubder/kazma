"""First-class PLAN MODE — inspect and propose, then execute on approve.

Not a prompt-only nudge. While active, the existing ``read_only`` /
``no_writes`` hard_constraints strip mutating tools from the schema and
the tool worker (YOLO cannot expand that allowlist). HITL and commitment
still apply when the user approves and plan mode turns off.

Surfaces (SSE, WS, gateway) call ``apply_plan_command``. The supervisor
calls ``apply_plan_mode_to_turn`` every hop.

Kill-switch: ``KAZMA_PLAN_MODE=0``.
"""

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

PLAN_MODE_MARKER = "[KAZMA_PLAN_MODE]"
PLAN_EXECUTE_MARKER = "[KAZMA_PLAN_EXECUTE]"

_DEFAULT_TTL_SECONDS = 4 * 60 * 60
_STORE_PREFIX = "plan_mode."

_PLAN_CONSTRAINTS = ("read_only", "no_writes")

_CONTROL = frozenset({"on", "enable", "1", "off", "disable", "0", "status", "?", "info", "help"})
_EXECUTE = frozenset({"go", "execute", "approve", "run", "apply"})
_OFF = frozenset({"off", "disable", "0"})
_ON = frozenset({"on", "enable", "1"})
_STATUS = frozenset({"status", "?", "info", "help", ""})

_APPROVE_RE = re.compile(
    r"(?is)^\s*(?:/"
    r"?plan\s+(?:go|execute|approve|run)|"
    r"proceed|continue|go\s*ahead|do\s+it|"
    r"approve(?:\s+the\s+plan)?|execute(?:\s+the\s+plan)?|"
    r"يلا|نفّذ|نفذ|نفذ الخطة|موافق)\s*[.!]?\s*$"
)

PLAN_MODE_NOTE = (
    f"{PLAN_MODE_MARKER}\n"
    "You are in PLAN MODE. Mutating tools (write, patch, shell, exec, git "
    "push, send) are structurally unavailable this turn — not a suggestion.\n"
    "1. Inspect the workspace (`codebase_search`, `file_read`, `file_list`).\n"
    "2. Produce a numbered plan: files to touch, tools you would use, risks.\n"
    "3. Stop. Do not implement. Ask the user to `/plan go` (or say **Proceed**) "
    "to execute.\n"
    "[/KAZMA_PLAN_MODE]"
)

PLAN_EXECUTE_NOTE = (
    f"{PLAN_EXECUTE_MARKER}\n"
    "The user APPROVED the plan. Plan mode is OFF. Mutating tools are available "
    "again (HITL still applies to danger tools). Execute the plan you just wrote. "
    "Do not re-plan unless a step is blocked. If there is no plan in history, "
    "ask what to do.\n"
    "[/KAZMA_PLAN_EXECUTE]"
)


@dataclass
class PlanCommandResult:
    """Outcome of a /plan slash command."""

    handled: bool
    reply: str = ""
    action: str = ""
    plan_active: bool = False
    rewrite_user_text: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def plan_mode_enabled() -> bool:
    """False when the operator killed plan mode (``KAZMA_PLAN_MODE=0``)."""
    raw = (os.environ.get("KAZMA_PLAN_MODE") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _key(thread_id: str) -> str:
    return f"{_STORE_PREFIX}{(thread_id or '').strip()}"


def _load(thread_id: str) -> dict[str, Any] | None:
    if not thread_id:
        return None
    try:
        from kazma_core.config_store import get_config_store

        raw = get_config_store().get(_key(thread_id))
        if not isinstance(raw, dict) or not raw:
            return None
        ttl = int(raw.get("ttl") or _DEFAULT_TTL_SECONDS)
        ts = float(raw.get("ts") or 0)
        if ttl > 0 and ts and (time.time() - ts) > ttl:
            get_config_store().delete(_key(thread_id))
            return None
        return raw
    except Exception:
        logger.debug("[plan_mode] load failed", exc_info=True)
        return None


def _save(thread_id: str, payload: dict[str, Any]) -> None:
    from kazma_core.config_store import get_config_store

    get_config_store().set(_key(thread_id), payload, category="agent")


def is_plan_mode(thread_id: str | None) -> bool:
    if not thread_id or not plan_mode_enabled():
        return False
    raw = _load(thread_id)
    return bool(raw and raw.get("enabled"))


def enable_plan_mode(thread_id: str, *, actor: str = "unknown") -> dict[str, Any]:
    payload = {
        "enabled": True,
        "pending_execute": False,
        "actor": actor,
        "ts": time.time(),
        "ttl": _DEFAULT_TTL_SECONDS,
    }
    _save(thread_id, payload)
    logger.info("[plan_mode] ON thread=%s actor=%s", thread_id[:16], actor)
    return payload


def disable_plan_mode(
    thread_id: str,
    *,
    actor: str = "unknown",
    pending_execute: bool = False,
) -> None:
    if pending_execute:
        _save(
            thread_id,
            {
                "enabled": False,
                "pending_execute": True,
                "actor": actor,
                "ts": time.time(),
                "ttl": _DEFAULT_TTL_SECONDS,
            },
        )
        logger.info("[plan_mode] EXECUTE-PENDING thread=%s actor=%s", thread_id[:16], actor)
        return
    try:
        from kazma_core.config_store import get_config_store

        get_config_store().delete(_key(thread_id))
    except Exception:
        logger.debug("[plan_mode] delete failed", exc_info=True)
    logger.info("[plan_mode] OFF thread=%s actor=%s", thread_id[:16], actor)


def consume_pending_execute(thread_id: str | None) -> bool:
    """True once if this turn should run the approved plan. Clears the flag."""
    if not thread_id:
        return False
    raw = _load(thread_id)
    if not raw or not raw.get("pending_execute"):
        return False
    try:
        from kazma_core.config_store import get_config_store

        get_config_store().delete(_key(thread_id))
    except Exception:
        logger.debug("[plan_mode] consume pending failed", exc_info=True)
    return True


def is_plan_approve_reply(text: str | None) -> bool:
    """True when the user is approving the plan (not a new task)."""
    t = (text or "").strip()
    if not t or len(t.split()) > 8:
        return False
    return bool(_APPROVE_RE.search(t))


def apply_plan_mode_constraints(hard_constraints: list[str] | None) -> list[str]:
    """Union read-only tags so existing tool filters enforce plan mode."""
    out: list[str] = []
    seen: set[str] = set()
    for item in list(hard_constraints or []) + list(_PLAN_CONSTRAINTS):
        key = str(item).strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def _drop_plan_constraints(hard_constraints: list[str] | None) -> list[str]:
    """Remove tags plan mode added so execute can use write tools again."""
    drop = set(_PLAN_CONSTRAINTS)
    return [str(c).strip() for c in (hard_constraints or []) if str(c).strip().lower() not in drop]


def _strip_plan_notes(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in messages:
        if not isinstance(m, dict) or m.get("role") != "system":
            out.append(m)
            continue
        content = str(m.get("content") or "")
        if PLAN_MODE_MARKER in content or PLAN_EXECUTE_MARKER in content:
            continue
        out.append(m)
    return out


def apply_plan_mode_to_turn(
    thread_id: str,
    *,
    hard_constraints: list[str] | None,
    messages: list[dict[str, Any]],
    user_text: str = "",
) -> tuple[list[str], list[dict[str, Any]], str]:
    """Apply plan-mode policy for this supervisor hop.

    Returns ``(constraints, messages, kind)`` where *kind* is
    ``plan``, ``execute``, or ``off``.
    """
    msgs = list(messages or [])
    constraints = list(hard_constraints or [])
    if not thread_id or not plan_mode_enabled():
        return constraints, msgs, "off"

    if consume_pending_execute(thread_id):
        msgs = _strip_plan_notes(msgs)
        msgs.append({"role": "system", "content": PLAN_EXECUTE_NOTE})
        return _drop_plan_constraints(constraints), msgs, "execute"

    if is_plan_mode(thread_id) and is_plan_approve_reply(user_text):
        disable_plan_mode(thread_id, actor="approve-reply", pending_execute=False)
        msgs = _strip_plan_notes(msgs)
        msgs.append({"role": "system", "content": PLAN_EXECUTE_NOTE})
        return _drop_plan_constraints(constraints), msgs, "execute"

    if is_plan_mode(thread_id):
        constraints = apply_plan_mode_constraints(constraints)
        msgs = _strip_plan_notes(msgs)
        msgs.append({"role": "system", "content": PLAN_MODE_NOTE})
        return constraints, msgs, "plan"

    return constraints, msgs, "off"


def is_plan_command(text: str, *, require_slash: bool = True) -> bool:
    parts = (text or "").strip().split()
    if not parts:
        return False
    cmd = parts[0].lower()
    if require_slash and not cmd.startswith("/"):
        return False
    return cmd.lstrip("/") == "plan"


def apply_plan_command(
    thread_id: str,
    text: str,
    *,
    actor: str = "unknown",
    require_slash: bool = True,
) -> PlanCommandResult:
    """Parse /plan. Control commands reply; go/task rewrite into a graph turn."""
    if not thread_id or not is_plan_command(text, require_slash=require_slash):
        return PlanCommandResult(handled=False, reply="")

    parts = (text or "").strip().split(None, 2)
    sub = parts[1].lower() if len(parts) > 1 else ""
    rest = parts[2].strip() if len(parts) > 2 else ""
    # "/plan on fix the login" → rest after "on"
    if sub in _ON and rest:
        task = rest
        sub = "on"
    elif sub in _CONTROL or sub in _EXECUTE:
        task = rest
    else:
        task = (text or "").strip().split(None, 1)
        task = task[1].strip() if len(task) > 1 else ""
        sub = "task" if task else "status"

    active = is_plan_mode(thread_id)

    if not plan_mode_enabled() and sub in _ON | {"task"} | _EXECUTE:
        return PlanCommandResult(
            handled=True,
            action="disabled",
            plan_active=False,
            reply=(
                "📋 Plan mode is **disabled** (`KAZMA_PLAN_MODE=0`). "
                "Unset that env and restart to use `/plan`."
            ),
        )

    if sub in _STATUS and sub != "help" and not (sub == "" and task):
        return PlanCommandResult(
            handled=True,
            action="status",
            plan_active=active,
            reply=_status_text(thread_id),
        )
    if sub == "help":
        return PlanCommandResult(
            handled=True,
            action="help",
            plan_active=active,
            reply=_help_text(),
        )
    if sub in _OFF:
        disable_plan_mode(thread_id, actor=actor)
        return PlanCommandResult(
            handled=True,
            action="off",
            plan_active=False,
            reply=(
                "📋 Plan mode **OFF**. Write/exec tools are available again "
                "(HITL still gates danger tools).\n"
                "Re-enter: `/plan on` · `/plan <task>`"
            ),
        )
    if sub in _EXECUTE:
        if not active and not _load(thread_id):
            return PlanCommandResult(
                handled=True,
                action="execute_idle",
                plan_active=False,
                reply=(
                    "📋 No plan is in progress. `/plan on` or `/plan <task>` "
                    "first, then `/plan go` after the plan is written."
                ),
            )
        disable_plan_mode(thread_id, actor=actor, pending_execute=True)
        return PlanCommandResult(
            handled=True,
            action="execute",
            plan_active=False,
            rewrite_user_text=(
                "Execute the approved plan. Plan mode is off. "
                "Follow the numbered plan you just wrote; HITL still applies "
                "to danger tools."
            ),
        )
    # on or task
    enable_plan_mode(thread_id, actor=actor)
    if task:
        return PlanCommandResult(
            handled=True,
            action="plan_task",
            plan_active=True,
            rewrite_user_text=task,
        )
    return PlanCommandResult(
        handled=True,
        action="on",
        plan_active=True,
        reply=(
            "📋 **Plan mode ON.** Mutating tools are blocked until you "
            "`/plan go` (or say **Proceed**).\n"
            "Send the task to plan, or `/plan <task>` in one shot.\n"
            "Off: `/plan off`"
        ),
    )


def _status_text(thread_id: str) -> str:
    if is_plan_mode(thread_id):
        return (
            "📋 Plan mode is **ON**. The agent can inspect (search/read) but "
            "cannot write, patch, shell, or exec.\n"
            "Approve: `/plan go` · **Proceed**\n"
            "Leave: `/plan off`"
        )
    return (
        "📋 Plan mode is **OFF**. `/plan on` to inspect-then-propose, "
        "`/plan <task>` to plan a specific job, `/plan go` after a plan."
    )


def _help_text() -> str:
    return (
        "📋 **Plan mode** — inspect and propose, then execute on approve.\n"
        "• `/plan on` — enter (write/exec tools blocked)\n"
        "• `/plan <task>` — enter and plan that task now\n"
        "• `/plan go` · **Proceed** — approve and execute (HITL still on)\n"
        "• `/plan off` · `/plan status`\n"
        "Not a permission bypass. Kill-switch: `KAZMA_PLAN_MODE=0`."
    )
