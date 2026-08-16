"""Long-task mode — aligned ReAct + LangGraph budgets for deep audits.

Orthogonal to YOLO (HITL bypass). Long-task only raises capacity ceilings
and is thread-scoped (like YOLO) with optional global defaults from Settings.

Resolution order for a turn:
  1. Active ``long_task.{thread_id}`` (slash ``/long on``)
  2. Global ``agent.max_iterations`` + derived recursion
  3. Safe defaults (15 / 100)

ConfigStore keys:
  - ``long_task.{thread_id}`` — per-chat enable payload
  - ``agent.max_iterations`` — baseline when long-task is OFF (Settings)
  - ``agent.long_task.default_enabled`` — optional default ON for new chats
  - ``agent.long_task.default_preset`` — chat|deep|research
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Awaitable, Callable
from contextvars import ContextVar, Token
from typing import Any

__all__ = [
    "PRESETS",
    "clamp_iterations",
    "consume_continue_context",
    "consume_long_task_turn",
    "derive_recursion_limit",
    "detect_tool_loop",
    "disable_long_task",
    "enable_long_task",
    "format_status_message",
    "get_progress_sender",
    "is_long_task_active",
    "is_mission_mode",
    "long_task_status",
    "maybe_heartbeat",
    "mission_hard_rounds",
    "mission_recursion_limit",
    "record_budget_exhausted",
    "record_long_task_event",
    "reset_progress_sender",
    "resolve_turn_budgets",
    "set_progress_sender",
    "store_continue_context",
    "tool_call_signature",
]

_ProgressSender = Callable[[str], Awaitable[None] | None]
_progress_sender: ContextVar[_ProgressSender | None] = ContextVar(
    "kazma_long_task_progress_sender", default=None
)

logger = logging.getLogger(__name__)

# Chat / Deep / Research — max_iterations (ReAct supervisor rounds)
PRESETS: dict[str, int] = {
    "chat": 15,
    "deep": 30,
    "research": 40,
}

_DEFAULT_TTL_SECONDS = 30 * 60  # 30 min — a /long from hours ago must NOT haunt the thread
_MIN_ITER = 5
_MAX_ITER = 100  # budget-mode soft ceiling
_MIN_RECURSION = 50
_MAX_RECURSION = 500  # budget-mode LangGraph cap
_MAX_MISSION_ITER = 2000
_MAX_MISSION_RECURSION = 5000
_DEFAULT_ITER = 15
_DEFAULT_RECURSION = 100


def clamp_iterations(
    value: Any,
    *,
    default: int = _DEFAULT_ITER,
    hard_cap: int | None = None,
) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    cap = hard_cap if hard_cap is not None else _MAX_ITER
    return max(_MIN_ITER, min(cap, n))


def derive_recursion_limit(max_iterations: int) -> int:
    """LangGraph node-hop budget aligned with ReAct rounds.

    Roughly 5 hops per tool round (supervisor → tools → …) plus a buffer so
    Research (40) is not killed by a hard-coded recursion_limit of 100.
    """
    mi = clamp_iterations(max_iterations)
    raw = mi * 5 + 20
    # Env absolute cap (ops safety)
    env_cap = (os.environ.get("KAZMA_LONG_TASK_MAX_RECURSION") or "").strip()
    cap = _MAX_RECURSION
    if env_cap.isdigit():
        cap = max(_MIN_RECURSION, min(_MAX_RECURSION, int(env_cap)))
    return min(cap, max(_DEFAULT_RECURSION, raw))


def _ttl_seconds() -> int:
    raw = (os.environ.get("KAZMA_LONG_TASK_TTL_SECONDS") or "").strip()
    if raw.isdigit():
        return max(60, int(raw))
    if raw in ("0", "off", "none", "infinite"):
        return 0
    return _DEFAULT_TTL_SECONDS


def _env_max_iter_cap() -> int:
    raw = (os.environ.get("KAZMA_LONG_TASK_MAX_ITER") or "").strip()
    if raw.isdigit():
        return max(_MIN_ITER, min(_MAX_ITER, int(raw)))
    return _MAX_ITER


def _baseline_iterations() -> int:
    try:
        from kazma_core.config_store import get_config_store

        return clamp_iterations(
            get_config_store().get("agent.max_iterations", _DEFAULT_ITER)
        )
    except Exception:
        return _DEFAULT_ITER


def mission_hard_rounds() -> int:
    """Safety ceiling for mission mode (real run-until-done cap).

    Not infinite — ops must be able to stop runaway cost/loops. Default 500
    tool rounds (~hours of work). Override with ``KAZMA_MISSION_MAX_ROUNDS``.
    """
    raw = (os.environ.get("KAZMA_MISSION_MAX_ROUNDS") or "").strip()
    if raw.isdigit():
        return max(40, min(_MAX_MISSION_ITER, int(raw)))
    return 500


def mission_recursion_limit() -> int:
    """LangGraph node-hop budget for mission (must track hard rounds)."""
    raw = (os.environ.get("KAZMA_MISSION_RECURSION") or "").strip()
    if raw.isdigit():
        return max(500, min(_MAX_MISSION_RECURSION, int(raw)))
    # ~5 hops × hard rounds, capped
    return min(_MAX_MISSION_RECURSION, max(500, mission_hard_rounds() * 5))


def enable_long_task(
    thread_id: str,
    *,
    actor: str = "unknown",
    preset: str = "research",
    max_iterations: int | None = None,
    mode: str = "budget",
    remaining_turns: int | None = None,
) -> dict[str, Any]:
    """Enable long-task mode for *thread_id*. Returns status dict.

    Modes:
      - ``budget`` (default): raised soft ceilings (Research = 40). Still
        force-stops and may reply PARTIAL — user must continue.
      - ``mission``: true run-until-done for this chat — ``max_iterations``
        and LangGraph recursion are set to the mission hard wall (default
        500 rounds / ~2500 steps). Not literally infinite (cost, process
        lifetime, hard wall) but no soft 40-round PARTIAL stop.
    """
    from kazma_core.config_store import get_config_store

    mode_key = (mode or "budget").strip().lower()
    if mode_key in ("mission", "unlimited", "unbounded", "auto", "full"):
        mode_key = "mission"
    else:
        mode_key = "budget"

    preset_key = (preset or "research").strip().lower()
    if preset_key in ("mission", "unlimited", "unbounded"):
        mode_key = "mission"
        preset_key = "mission"

    if mode_key == "mission":
        # Real ceiling = hard wall. Optional custom max raises within mission cap.
        hard = mission_hard_rounds()
        if max_iterations is not None:
            mi = clamp_iterations(max_iterations, hard_cap=_MAX_MISSION_ITER)
            mi = max(mi, PRESETS["research"])
            hard = max(hard, mi)
        else:
            mi = hard
        recursion = mission_recursion_limit()
        # Keep recursion aligned if hard wall raised via custom max
        recursion = max(recursion, min(_MAX_MISSION_RECURSION, mi * 5))
        preset_key = "mission"
    else:
        if max_iterations is not None:
            mi = clamp_iterations(max_iterations)
            preset_key = "custom"
        elif preset_key in PRESETS:
            mi = PRESETS[preset_key]
        elif preset_key.isdigit():
            mi = clamp_iterations(int(preset_key))
            preset_key = "custom"
        else:
            preset_key = "research"
            mi = PRESETS["research"]

        mi = min(mi, _env_max_iter_cap())
        recursion = derive_recursion_limit(mi)
        hard = mi

    now = time.time()
    ttl = _ttl_seconds()
    if remaining_turns is None:
        # Mission is a multi-follow-up job; budget /long is one task turn.
        turns = 3 if mode_key == "mission" else 1
    else:
        try:
            turns = max(1, min(20, int(remaining_turns)))
        except (TypeError, ValueError):
            turns = 1
    payload = {
        "enabled": True,
        "mode": mode_key,
        "preset": preset_key,
        "max_iterations": mi,
        "recursion_limit": recursion,
        "mission_hard_rounds": hard,
        "since": now,
        "actor": actor,
        "ttl_seconds": ttl,
        "expires_at": (now + ttl) if ttl > 0 else None,
        # Slots for upcoming user turns. consume() decrements at the START of
        # a real prompt; expire when a new turn finds remaining <= 0. Do NOT
        # expire in long_task_status() on 0 — that used to kill the budget
        # on the first task after /long (the turn that should benefit).
        "remaining_turns": turns,
    }
    get_config_store().set(f"long_task.{thread_id}", payload, category="agent")
    record_long_task_event("enable_mission" if mode_key == "mission" else "enable")
    logger.info(
        "[long_task] ENABLED thread=%s actor=%s mode=%s preset=%s max_iter=%s "
        "recursion=%s hard=%s ttl=%s",
        thread_id,
        actor,
        mode_key,
        payload["preset"],
        mi,
        recursion,
        hard,
        ttl or "none",
    )
    return long_task_status(thread_id)


def disable_long_task(thread_id: str, *, actor: str = "unknown") -> None:
    from kazma_core.config_store import get_config_store

    get_config_store().delete(f"long_task.{thread_id}")
    record_long_task_event("disable")
    logger.info("[long_task] DISABLED thread=%s actor=%s", thread_id, actor)


def consume_long_task_turn(thread_id: str | None) -> None:
    """Decrement the long_task turn counter at the START of a new user turn.

    This is the structural fix for stale-budget runaway: a /long applies to
    the turn it was set for, NOT every subsequent conversation. After the turn
    resolves, the NEXT user message decrements remaining_turns; when it hits 0,
    the long_task auto-expires and max_iterations resets to baseline.

    Call this from EVERY entry point (ws_chat, sse_chat, gateway) before
    initial_supervisor_state resolves the budget.
    """
    if not thread_id:
        return
    try:
        from kazma_core.config_store import get_config_store

        cs = get_config_store()
        raw = cs.get(f"long_task.{thread_id}")
        if not raw or not isinstance(raw, dict) or not raw.get("enabled"):
            return
        remaining = int(raw.get("remaining_turns", 1))
        # Enabling /long is intercepted (no consume). The first real prompt
        # after enable must still receive the raised budget. Expire only when
        # a *new* turn arrives and no slots remain.
        if remaining <= 0:
            cs.delete(f"long_task.{thread_id}")
            logger.info(
                "[long_task] EXPIRED thread=%s (turn-count exhausted at consume)",
                thread_id[:12],
            )
            return
        raw["remaining_turns"] = remaining - 1
        cs.set(f"long_task.{thread_id}", raw, category="agent")
        logger.info(
            "[long_task] turn consumed thread=%s remaining_turns=%d",
            thread_id[:12], remaining - 1,
        )
    except Exception:
        logger.debug("[long_task] consume_long_task_turn failed", exc_info=True)


def is_long_task_active(thread_id: str | None) -> bool:
    if not thread_id:
        return False
    return bool(long_task_status(thread_id).get("active"))


def long_task_status(thread_id: str) -> dict[str, Any]:
    """Structured status; auto-disables on TTL expiry OR turn-count exhaustion."""
    from kazma_core.config_store import get_config_store

    cs = get_config_store()
    raw = cs.get(f"long_task.{thread_id}")
    if not raw or not isinstance(raw, dict) or not raw.get("enabled"):
        return {
            "active": False,
            "thread_id": thread_id,
            "max_iterations": _baseline_iterations(),
            "recursion_limit": derive_recursion_limit(_baseline_iterations()),
        }

    expires = raw.get("expires_at")
    if expires is not None:
        try:
            if time.time() > float(expires):
                cs.delete(f"long_task.{thread_id}")
                logger.info("[long_task] EXPIRED thread=%s (TTL)", thread_id)
                return {
                    "active": False,
                    "thread_id": thread_id,
                    "expired": True,
                    "max_iterations": _baseline_iterations(),
                    "recursion_limit": derive_recursion_limit(_baseline_iterations()),
                }
        except (TypeError, ValueError):
            pass

    # Turn-count is owned by consume_long_task_turn() (expire at the *next*
    # prompt when remaining already hit 0). Status must stay active for the
    # turn that just consumed the last slot, or /long never applies.
    remaining_turns = int(raw.get("remaining_turns", 1))

    remaining = None
    if expires is not None:
        try:
            remaining = max(0, int(float(expires) - time.time()))
        except (TypeError, ValueError):
            remaining = None

    mode = str(raw.get("mode") or "budget").lower()
    if mode not in ("budget", "mission"):
        mode = "budget"

    if mode == "mission":
        hard = int(
            raw.get("mission_hard_rounds")
            or raw.get("max_iterations")
            or mission_hard_rounds()
        )
        hard = max(40, min(_MAX_MISSION_ITER, hard))
        mi = clamp_iterations(
            raw.get("max_iterations", hard),
            hard_cap=_MAX_MISSION_ITER,
        )
        # Mission ceiling is the hard wall — never re-clamp to budget 100.
        mi = min(_MAX_MISSION_ITER, max(mi, hard, PRESETS["research"]))
        recursion = int(raw.get("recursion_limit") or mission_recursion_limit())
        recursion = max(
            recursion,
            mission_recursion_limit(),
            min(_MAX_MISSION_RECURSION, mi * 5),
        )
        recursion = min(_MAX_MISSION_RECURSION, max(500, recursion))
    else:
        mi = clamp_iterations(raw.get("max_iterations", PRESETS["research"]))
        recursion = int(raw.get("recursion_limit") or derive_recursion_limit(mi))
        recursion = min(_MAX_RECURSION, max(_MIN_RECURSION, recursion))
        hard = mi

    return {
        "active": True,
        "thread_id": thread_id,
        "mode": mode,
        "preset": raw.get("preset", "research"),
        "max_iterations": mi,
        "recursion_limit": recursion,
        "mission_hard_rounds": hard,
        "actor": raw.get("actor", "unknown"),
        "since": raw.get("since"),
        "ttl_seconds": raw.get("ttl_seconds"),
        "expires_at": expires,
        "remaining_seconds": remaining,
        "remaining_turns": remaining_turns,
    }


def is_mission_mode(thread_id: str | None) -> bool:
    """True when this thread should auto-extend past soft max_iterations."""
    if not thread_id:
        return False
    st = long_task_status(thread_id)
    return bool(st.get("active") and st.get("mode") == "mission")


def resolve_turn_budgets(thread_id: str | None = None) -> dict[str, Any]:
    """Return effective budgets for a turn.

    Keys: ``max_iterations``, ``recursion_limit``, ``mode`` (budget|mission),
    ``mission_hard_rounds``. Prefer active per-thread long-task; else Settings
    baseline (and optional global long-task default).
    """
    if thread_id:
        st = long_task_status(thread_id)
        if st.get("active"):
            return {
                "max_iterations": int(st["max_iterations"]),
                "recursion_limit": int(st["recursion_limit"]),
                "mode": str(st.get("mode") or "budget"),
                "mission_hard_rounds": int(
                    st.get("mission_hard_rounds") or st["max_iterations"]
                ),
            }

    mi = _baseline_iterations()
    try:
        from kazma_core.config_store import get_config_store

        cs = get_config_store()
        default_on = cs.get("agent.long_task.default_enabled") in (
            True, 1, "1", "true", "yes", "on",
        )
        if default_on and not (thread_id and long_task_status(thread_id).get("active")):
            preset = str(cs.get("agent.long_task.default_preset") or "research").lower()
            if preset in PRESETS:
                mi = PRESETS[preset]
            # else keep baseline max_iterations from Settings
    except Exception:
        pass

    # Always derive recursion from iterations so Research (40) cannot fight
    # a hard-coded recursion_limit of 100.
    return {
        "max_iterations": mi,
        "recursion_limit": derive_recursion_limit(mi),
        "mode": "budget",
        "mission_hard_rounds": mi,
    }


def format_status_message(thread_id: str) -> str:
    """User-facing status for ``/long`` / ``/long status``."""
    st = long_task_status(thread_id)
    if not st.get("active"):
        base = resolve_turn_budgets(None)
        return (
            "📋 **Long-task mode is OFF** for this chat.\n"
            f"Baseline: {base['max_iterations']} tool rounds · "
            f"~{base['recursion_limit']} graph steps.\n"
            "**Budget mode** (still stops & asks you to continue):\n"
            "  `/long on` · `/long deep` · `/long research`\n"
            "**Mission mode** (auto-continues until done or hard safety wall):\n"
            "  `/long mission`  or  `/mission on`\n"
            "**Both** (budget + skip HITL): `/long yolo` · `/unrestricted`\n"
            "HITL is a separate knob — `/yolo` alone does not raise the round cap."
        )
    rem = st.get("remaining_seconds")
    ttl_note = f"Expires in ~{rem // 60}m." if rem is not None else "No auto-expiry."
    if st.get("mode") == "mission":
        hard = st.get("mission_hard_rounds", mission_hard_rounds())
        return (
            "🚀 **MISSION mode ON** — run until done (or hard safety wall).\n"
            f"Tool rounds: **{st['max_iterations']}** · "
            f"graph steps ~**{st['recursion_limit']}** · "
            f"hard wall: **{hard}**.\n"
            f"{ttl_note}\n"
            "This is **not** infinite (cost, process lifetime, hard wall). "
            "It **is** far past Research/40 PARTIAL stops.\n"
            "HITL still on unless `/yolo`. Disable: `/long off`"
        )
    return (
        f"🧠 **Long-task BUDGET mode ON** ({st.get('preset', 'research')}).\n"
        f"Soft ceiling: **{st['max_iterations']}** tool rounds · "
        f"**~{st['recursion_limit']}** graph steps — then forced answer "
        f"(may say PARTIAL).\n"
        f"{ttl_note}\n"
        "For real long runs without the soft stop: `/long mission`\n"
        "Budget + YOLO: `/long yolo`. Disable budget: `/long off`"
    )


# ── Progress sender (gateway sets before ainvoke for heartbeats) ─────────


def set_progress_sender(sender: _ProgressSender | None) -> Token:
    return _progress_sender.set(sender)


def reset_progress_sender(token: Token) -> None:
    _progress_sender.reset(token)


def get_progress_sender() -> _ProgressSender | None:
    return _progress_sender.get()


async def maybe_heartbeat(
    *,
    thread_id: str | None,
    iteration: int,
    max_iterations: int,
    last_tools: list[str] | None = None,
) -> None:
    """Send a short progress note every 5 rounds when long-task is active."""
    if not thread_id or iteration <= 0 or iteration % 5 != 0:
        return
    if not is_long_task_active(thread_id):
        # Still heartbeat when default long-task or deep budgets
        budgets = resolve_turn_budgets(thread_id)
        if budgets["max_iterations"] < 25:
            return
    sender = get_progress_sender()
    if sender is None:
        return
    tools = ", ".join((last_tools or [])[:4]) or "tools"
    text = (
        f"⏳ Long task progress: round **{iteration}/{max_iterations}** "
        f"(recent: {tools}). Still working…"
    )
    try:
        result = sender(text)
        if hasattr(result, "__await__"):
            await result  # type: ignore[misc]
        record_long_task_event("heartbeat")
    except Exception:
        logger.debug("[long_task] heartbeat send failed", exc_info=True)


# ── Continue protocol (partial summary for "Proceed") ────────────────────


def store_continue_context(
    thread_id: str,
    *,
    summary: str,
    reason: str = "budget_exhausted",
) -> None:
    """Persist salvaged progress so the next 'Proceed' turn can continue cleanly."""
    if not thread_id or not (summary or "").strip():
        return
    from kazma_core.config_store import get_config_store

    payload = {
        "summary": summary.strip()[:6000],
        "reason": reason,
        "stored_at": time.time(),
    }
    get_config_store().set(
        f"long_task.continue.{thread_id}", payload, category="agent"
    )
    record_long_task_event("continue_stored")
    logger.info(
        "[long_task] continue context stored thread=%s chars=%d reason=%s",
        thread_id,
        len(payload["summary"]),
        reason,
    )


def consume_continue_context(thread_id: str | None) -> str | None:
    """Return and clear stored continue context, or None."""
    if not thread_id:
        return None
    from kazma_core.config_store import get_config_store

    cs = get_config_store()
    key = f"long_task.continue.{thread_id}"
    raw = cs.get(key)
    if not isinstance(raw, dict):
        return None
    summary = str(raw.get("summary") or "").strip()
    try:
        cs.delete(key)
    except Exception:
        pass
    if not summary:
        return None
    record_long_task_event("continue_consumed")
    return (
        "[LONG-TASK CONTINUE CONTEXT — prior turn hit a budget limit]\n"
        "You already gathered the following. Do **not** re-do this work. "
        "Only pursue remaining gaps and produce a final report.\n\n"
        f"{summary}\n"
        "[/LONG-TASK CONTINUE CONTEXT]"
    )


# ── Anti-loop detection ──────────────────────────────────────────────────


def tool_call_signature(name: str, arguments: Any) -> str:
    """Stable short signature for a tool invocation."""
    import hashlib
    import json

    try:
        blob = json.dumps(arguments, sort_keys=True, default=str, ensure_ascii=False)
    except Exception:
        blob = str(arguments)
    digest = hashlib.sha256(f"{name}:{blob}".encode("utf-8", errors="replace")).hexdigest()[:16]
    return f"{name}:{digest}"


def detect_tool_loop(
    signatures: list[str],
    *,
    window: int = 12,
    max_repeats: int = 3,
) -> str | None:
    """If the same tool+args signature repeats too often, return that signature."""
    if not signatures:
        return None
    recent = signatures[-window:]
    counts: dict[str, int] = {}
    for sig in recent:
        counts[sig] = counts.get(sig, 0) + 1
        if counts[sig] >= max_repeats:
            return sig
    return None


# ── Metrics ──────────────────────────────────────────────────────────────


def record_long_task_event(kind: str) -> None:
    """Increment optional Prometheus counter for long-task events."""
    try:
        from kazma_core.metrics import record_long_task

        record_long_task(kind)
    except Exception:
        pass


def record_budget_exhausted(reason: str = "recursion") -> None:
    record_long_task_event(f"budget_{reason}")
