"""Slash-command SoT for /long, /mission, /unrestricted, and /long yolo.

YOLO (HITL bypass) and long-task (iteration budget) stay independent
primitives. This module is the one place that *combines* them so the
user can say “finish this, don’t ask, don’t stop early” without a
third flag that later drifts.

Surfaces (Web WS, SSE, Telegram/gateway) must call ``apply_capacity_command``
instead of re-implementing the parser.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

_LONG_CMDS = frozenset({"/long", "long"})
_MISSION_CMDS = frozenset({"/mission", "mission"})
_UNRESTRICTED_CMDS = frozenset({"/unrestricted", "unrestricted"})
_OFF = frozenset({"off", "disable", "0"})
_STATUS = frozenset({"status", "?", "info"})
_ON = frozenset({"on", "enable", "1"})
_MISSION_ALIASES = frozenset({"mission", "unlimited", "unbounded", "full", "auto"})
_PRESETS = frozenset({"research", "deep", "chat"})


@dataclass
class CapacityCommandResult:
    """Outcome of a capacity slash command."""

    handled: bool
    reply: str
    action: str = ""
    long_active: bool = False
    yolo_active: bool = False
    yolo_blocked: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


def is_capacity_command(text: str, *, require_slash: bool = True) -> bool:
    """True when *text* is /long, /mission, or /unrestricted (not sent to the LLM)."""
    parts = (text or "").strip().split()
    if not parts:
        return False
    cmd = parts[0].lower()
    if require_slash and not cmd.startswith("/"):
        return False
    bare = cmd.lstrip("/")
    return bare in ("long", "mission", "unrestricted")


def apply_capacity_command(
    thread_id: str,
    text: str,
    *,
    actor: str = "unknown",
    require_slash: bool = True,
) -> CapacityCommandResult:
    """Parse and apply a capacity command. Never raises to the caller."""
    if not thread_id or not is_capacity_command(text, require_slash=require_slash):
        return CapacityCommandResult(handled=False, reply="")

    parsed = _parse(text)
    if parsed is None:
        return CapacityCommandResult(
            handled=True,
            action="help",
            reply=_help_text(),
        )

    action = parsed["action"]
    try:
        if action == "status":
            return CapacityCommandResult(
                handled=True,
                action="status",
                reply=format_capacity_status(thread_id),
                **_flags(thread_id),
            )
        if action == "off_long":
            from kazma_core.agent.long_task import disable_long_task

            disable_long_task(thread_id, actor=actor)
            return CapacityCommandResult(
                handled=True,
                action="off_long",
                reply=(
                    "📋 Long-task / mission **OFF**. Chat baseline restored "
                    "(Settings → Max tool rounds).\n"
                    "HITL unchanged — `/yolo off` if you also want approvals back.\n"
                    "Re-enable: `/long on` · `/long mission` · `/long yolo`"
                ),
                **_flags(thread_id),
            )
        if action == "off_both":
            from kazma_core.agent.long_task import disable_long_task
            from kazma_core.safety.yolo import disable_yolo

            disable_long_task(thread_id, actor=actor)
            disable_yolo(thread_id, actor=actor)
            return CapacityCommandResult(
                handled=True,
                action="off_both",
                reply=(
                    "🛡️ **Restricted again.** Budget back to Settings baseline. "
                    "HITL approvals are required for danger tools.\n"
                    "Power modes: `/long on` · `/long yolo` · `/unrestricted`"
                ),
                **_flags(thread_id),
            )
        if action == "on":
            return _enable(
                thread_id,
                actor=actor,
                mode=str(parsed.get("mode") or "budget"),
                preset=str(parsed.get("preset") or "research"),
                max_iterations=parsed.get("max_iterations"),
                with_yolo=bool(parsed.get("yolo")),
                remaining_turns=int(parsed.get("remaining_turns") or 1),
            )
    except Exception:
        logger.exception("[capacity] apply failed thread=%s action=%s", thread_id[:12], action)
        return CapacityCommandResult(
            handled=True,
            action="error",
            reply="⚠️ Could not change capacity. Try `/long status`.",
        )
    return CapacityCommandResult(handled=True, action="help", reply=_help_text())


def format_capacity_status(thread_id: str) -> str:
    """User-facing combined budget + HITL status."""
    from kazma_core.agent.long_task import format_status_message, long_task_status
    from kazma_core.safety.yolo import yolo_allowed, yolo_status

    long_st = long_task_status(thread_id)
    yolo_st = yolo_status(thread_id)

    long_block = format_status_message(thread_id)
    if yolo_st.get("active"):
        rem = yolo_st.get("remaining_seconds")
        ttl = f"expires in ~{int(rem) // 60}m" if rem is not None else "no auto-expiry"
        yolo_block = f"🚀 **HITL: YOLO ON** ({ttl}). Danger tools auto-approve. `/yolo off` restores the gate."
    else:
        prod = ""
        if not yolo_allowed():
            prod = " Production blocks YOLO (`KAZMA_ALLOW_YOLO=1` to opt in)."
        yolo_block = (
            "🛡️ **HITL: ON** — danger tools need Approve / Allow tool / YOLO."
            f"{prod}"
        )

    follow = long_st.get("remaining_turns")
    follow_note = ""
    if long_st.get("active") and follow is not None:
        left = int(follow)
        follow_note = (
            "\nThis is the **last** turn on this raised budget (next message returns to Settings)."
            if left <= 0
            else f"\nFollow-up turns left on this budget: **{left}** (then back to Settings)."
        )

    return (
        f"{long_block}{follow_note}\n\n{yolo_block}\n\n"
        "One-shot power: `/long yolo` (research + YOLO) · `/unrestricted` (mission + YOLO)\n"
        "Independent offs: `/long off` · `/yolo off` · both: `/unrestricted off`"
    )


def snapshot_capacity(thread_id: str) -> dict[str, Any]:
    """JSON snapshot for the Web capacity bar / GET /api/chat/capacity."""
    from kazma_core.agent.long_task import long_task_status, resolve_turn_budgets
    from kazma_core.safety.yolo import yolo_allowed, yolo_status

    budgets = resolve_turn_budgets(thread_id)
    long_st = long_task_status(thread_id)
    yolo_st = yolo_status(thread_id)
    return {
        "thread_id": thread_id,
        "max_iterations": int(budgets.get("max_iterations") or 15),
        "recursion_limit": int(budgets.get("recursion_limit") or 100),
        "mode": str(budgets.get("mode") or "budget"),
        "mission_hard_rounds": int(budgets.get("mission_hard_rounds") or 0),
        "long_active": bool(long_st.get("active")),
        "preset": str(long_st.get("preset") or ""),
        "remaining_turns": long_st.get("remaining_turns"),
        "remaining_seconds": long_st.get("remaining_seconds"),
        "yolo_active": bool(yolo_st.get("active")),
        "yolo_allowed": bool(yolo_allowed()),
        "yolo_remaining_seconds": yolo_st.get("remaining_seconds"),
    }


# ── internals ──────────────────────────────────────────────────────────


def _flags(thread_id: str) -> dict[str, Any]:
    from kazma_core.agent.long_task import is_long_task_active
    from kazma_core.safety.yolo import is_yolo_active

    return {
        "long_active": is_long_task_active(thread_id),
        "yolo_active": is_yolo_active(thread_id),
    }


def _parse(text: str) -> dict[str, Any] | None:
    parts = (text or "").strip().lower().split()
    if not parts:
        return None
    cmd = parts[0]
    rest = parts[1:]

    if cmd in _UNRESTRICTED_CMDS or cmd.lstrip("/") == "unrestricted":
        sub = rest[0] if rest else "on"
        if sub in _OFF:
            return {"action": "off_both"}
        if sub in _STATUS:
            return {"action": "status"}
        # Default unrestricted = mission budget + YOLO (the power preset).
        return {
            "action": "on",
            "mode": "mission",
            "preset": "mission",
            "yolo": True,
            "remaining_turns": 3,
        }

    if cmd in _MISSION_CMDS or cmd.lstrip("/") == "mission":
        sub = rest[0] if rest else "on"
        if sub in _STATUS:
            return {"action": "status"}
        if sub in _OFF:
            return {"action": "off_long"}
        return {
            "action": "on",
            "mode": "mission",
            "preset": "mission",
            "yolo": False,
            "remaining_turns": 3,
        }

    if cmd not in _LONG_CMDS and cmd.lstrip("/") != "long":
        return None

    with_yolo = False
    if rest and rest[0] == "yolo":
        with_yolo = True
        rest = rest[1:]

    sub = rest[0] if rest else ("on" if with_yolo else "status")
    if sub in _STATUS:
        return {"action": "status"}
    if sub in _OFF:
        return {"action": "off_both" if with_yolo else "off_long"}
    if sub in _MISSION_ALIASES:
        return {
            "action": "on",
            "mode": "mission",
            "preset": "mission",
            "yolo": with_yolo,
            "remaining_turns": 3,
        }
    if sub in _ON:
        return {
            "action": "on",
            "mode": "budget",
            "preset": "research",
            "yolo": with_yolo,
            "remaining_turns": 1 if not with_yolo else 2,
        }
    if sub in _PRESETS:
        return {
            "action": "on",
            "mode": "budget",
            "preset": sub,
            "yolo": with_yolo,
            "remaining_turns": 1 if not with_yolo else 2,
        }
    if sub.isdigit():
        return {
            "action": "on",
            "mode": "budget",
            "preset": "custom",
            "max_iterations": int(sub),
            "yolo": with_yolo,
            "remaining_turns": 1 if not with_yolo else 2,
        }
    return None


def _enable(
    thread_id: str,
    *,
    actor: str,
    mode: str,
    preset: str,
    max_iterations: int | None,
    with_yolo: bool,
    remaining_turns: int,
) -> CapacityCommandResult:
    from kazma_core.agent.long_task import enable_long_task
    from kazma_core.safety.yolo import YoloDisabledError, enable_yolo

    st = enable_long_task(
        thread_id,
        actor=actor,
        preset=preset,
        max_iterations=max_iterations,
        mode=mode,
        remaining_turns=remaining_turns,
    )
    rem = st.get("remaining_seconds")
    ttl_note = (
        f"Budget auto-expires in ~{int(rem) // 60}m."
        if rem is not None
        else "Budget has no time expiry."
    )
    yolo_blocked = False
    yolo_note = ""
    if with_yolo:
        try:
            yst = enable_yolo(thread_id, actor=actor)
            yrem = yst.get("remaining_seconds")
            yttl = (
                f"YOLO expires in ~{int(yrem) // 60}m."
                if yrem is not None
                else "YOLO has no time expiry."
            )
            yolo_note = (
                f"\n🚀 **YOLO ON** — danger tools run without approval. {yttl}\n"
                "`/yolo off` restores HITL without touching the budget."
            )
        except YoloDisabledError as yde:
            yolo_blocked = True
            yolo_note = (
                f"\n🛡️ YOLO blocked: {yde}\n"
                "Budget is still ON. Approvals remain required."
            )
        except Exception:
            logger.exception("[capacity] enable_yolo failed thread=%s", thread_id[:12])
            yolo_note = "\n⚠️ Could not enable YOLO; budget is still ON."

    if mode == "mission":
        head = (
            "🚀 **MISSION ON** — run until done "
            f"(hard wall **{st.get('mission_hard_rounds', st.get('max_iterations'))}** "
            f"tool rounds · ~**{st.get('recursion_limit')}** graph steps)."
        )
    else:
        head = (
            f"🧠 **Long-task BUDGET ON** ({st.get('preset', preset)}).\n"
            f"Soft ceiling: **{st.get('max_iterations')}** tool rounds · "
            f"**~{st.get('recursion_limit')}** graph steps "
            "(may PARTIAL — then **Proceed** or `/long mission`)."
        )

    combo = " `/long yolo` / `/unrestricted` already include YOLO." if not with_yolo else ""
    hitl_hint = (
        ""
        if with_yolo
        else f"\nHITL still on.{combo} `/yolo` to skip danger-tool cards."
    )

    reply = (
        f"{head}\n{ttl_note}\n"
        f"This budget covers **{remaining_turns}** user turn(s) (plus the one that just armed it).\n"
        f"Disable budget: `/long off`.{hitl_hint}{yolo_note}"
    )
    flags = _flags(thread_id)
    return CapacityCommandResult(
        handled=True,
        action="on",
        reply=reply,
        yolo_blocked=yolo_blocked,
        extra={"long": st},
        **flags,
    )


def _help_text() -> str:
    return (
        "Usage — **capacity** (rounds) and **YOLO** (HITL) are independent:\n"
        "  `/long` · `/long on` · `/long deep` · `/long research` · `/long 50`\n"
        "  `/long mission` or `/mission on` — run-until-done (hard wall ~500)\n"
        "  `/long yolo` — research budget **and** skip danger-tool approvals\n"
        "  `/unrestricted` — mission **and** YOLO (full power this chat)\n"
        "  `/long off` — budget only · `/yolo off` — HITL only\n"
        "  `/unrestricted off` or `/long yolo off` — both off\n"
        "  `/long status` — show both knobs"
    )
