"""SSE slash fast-path: /long /mission /unrestricted /plan /yolo.

Extracted from ``sse_chat/__init__.py`` so the stream handler stays a
router, not a command catalog. Behaviour is unchanged: journal a
``capacity`` frame + persist an instant turn; ``/plan go`` rewrites the
user text and falls through to the graph.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from fastapi.responses import StreamingResponse

from kazma_ui.sse_chat._persistence import _persist_instant_turn
from kazma_ui.sse_chat._streaming import _journal_fast_path

__all__ = ["intercept_capacity_fast_path"]

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
}


def _stream(gen: AsyncGenerator[str, None]) -> StreamingResponse:
    return StreamingResponse(
        gen,
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


def intercept_capacity_fast_path(
    *,
    session: Any,
    thread_id: str,
    session_id: str,
    raw_msg: str,
) -> tuple[StreamingResponse | None, str | None]:
    """Handle capacity/plan/yolo slashes without the graph.

    Returns ``(response, rewrite)``. A non-None *response* should be
    returned immediately. A non-None *rewrite* replaces the user text
    and continues into the graph (``/plan go``).
    """
    actor = f"web:{session_id[:12]}"

    from kazma_core.agent.capacity_commands import (
        apply_capacity_command,
        is_capacity_command,
    )

    if is_capacity_command(raw_msg, require_slash=True):
        _cap = apply_capacity_command(thread_id, raw_msg, actor=actor)
        _persist_instant_turn(
            session, thread_id, raw_msg, _cap.reply, kind="capacity"
        )

        async def _long_generator() -> AsyncGenerator[str, None]:
            yield await _journal_fast_path(thread_id, "capacity", {
                "long_active": _cap.long_active,
                "yolo_active": _cap.yolo_active,
                "action": _cap.action,
                "reply": _cap.reply,
            })
            yield await _journal_fast_path(thread_id, "done", {
                "tokens": 1, "cost": 0.0, "duration_ms": 100,
            })

        return _stream(_long_generator()), None

    from kazma_core.agent.plan_mode import apply_plan_command, is_plan_command

    if is_plan_command(raw_msg, require_slash=True):
        _pl = apply_plan_command(thread_id, raw_msg, actor=actor)
        if _pl.rewrite_user_text:
            return None, _pl.rewrite_user_text
        if _pl.handled:
            _persist_instant_turn(
                session, thread_id, raw_msg, _pl.reply, kind="capacity"
            )

            async def _plan_generator() -> AsyncGenerator[str, None]:
                yield await _journal_fast_path(thread_id, "capacity", {
                    "plan_active": _pl.plan_active,
                    "action": _pl.action,
                    "reply": _pl.reply,
                })
                yield await _journal_fast_path(thread_id, "done", {
                    "tokens": 1, "cost": 0.0, "duration_ms": 50,
                })

            return _stream(_plan_generator()), None

    if raw_msg.lower() in ("/yolo", "/yolo on", "/yolo off", "/yolo status"):
        confirmation = _yolo_confirmation(thread_id, session_id, raw_msg)
        _persist_instant_turn(
            session, thread_id, raw_msg, confirmation, kind="capacity"
        )

        async def _yolo_generator() -> AsyncGenerator[str, None]:
            yield await _journal_fast_path(
                thread_id, "capacity", {"action": "yolo", "reply": confirmation}
            )
            yield await _journal_fast_path(thread_id, "done", {
                "tokens": 1,
                "cost": 0.0,
                "duration_ms": 100,
            })

        return _stream(_yolo_generator()), None

    return None, None


def _yolo_confirmation(thread_id: str, session_id: str, raw_msg: str) -> str:
    from kazma_core.safety.yolo import (
        YoloDisabledError,
        disable_yolo,
        enable_yolo,
        yolo_allowed,
        yolo_status,
    )

    cmd = raw_msg.lower().strip()
    actor = f"web:{session_id[:12]}"
    if cmd == "/yolo status":
        st = yolo_status(thread_id)
        grant_note = ""
        try:
            from kazma_core.safety.hitl_grants import list_grants

            grants = list_grants(thread_id)
            if grants:
                names = ", ".join(g["tool"] for g in grants)
                grant_note = f"\nPer-tool grants active: `{names}`"
        except Exception:
            pass
        if st.get("active"):
            rem = st.get("remaining_seconds")
            ttl_note = (
                f"Expires in ~{rem // 60}m." if rem is not None
                else "No auto-expiry."
            )
            return (
                f"🚀 YOLO is **ON** for this session. {ttl_note}\n"
                f"Disable: `/yolo off`{grant_note}"
            )
        prod_note = ""
        if not yolo_allowed():
            prod_note = (
                "\nProduction mode blocks YOLO "
                "(set `KAZMA_ALLOW_YOLO=1` to opt in)."
            )
        return (
            "🛡️ YOLO is **OFF**. HITL approvals are required for danger tools."
            f"{grant_note}{prod_note}\n"
            "Tip: on an approval card use **Allow tool (session)** to stop "
            "repeat prompts for one tool without full YOLO."
        )
    if cmd == "/yolo off":
        disable_yolo(thread_id, actor=actor)
        return "🛡️ YOLO deactivated. Safety gates and tool grants are cleared."
    try:
        st = enable_yolo(thread_id, actor=actor)
        rem = st.get("remaining_seconds")
        ttl_note = (
            f"Auto-expires in ~{rem // 60} minutes "
            f"(set KAZMA_YOLO_TTL_SECONDS to change; 0 = no expiry)."
            if rem is not None
            else "No auto-expiry (KAZMA_YOLO_TTL_SECONDS=0)."
        )
        return (
            "🚀 **YOLO ON** for this session only.\n"
            "All danger tools run **without** approval until you `/yolo off` "
            f"or TTL ends.\n{ttl_note}\n"
            "⚠️ Use only when you fully trust this session."
        )
    except YoloDisabledError as yde:
        return f"🛡️ {yde}"
