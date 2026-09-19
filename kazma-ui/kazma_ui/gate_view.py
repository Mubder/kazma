"""HITL display state — one function, at the wire.

The client paints ``view``. It does not re-derive. Plan:
``docs/plans/HITL_VIEW_MODEL.md``.

Interactivity is never taken from a part stamp. The stamp may label a
gate that is already known not-interactive (historical Approved/Denied).
"""

from __future__ import annotations

import logging
from typing import Any, Mapping, Sequence, TypedDict

from kazma_ui.turn_document import hitl_parts_of

logger = logging.getLogger(__name__)

__all__ = [
    "GateView",
    "apply_views_to_parts",
    "attach_view_to_hitl_frame",
    "interrupt_id_of",
    "resolve_gate_views",
    "stamp_parts_for_read",
    "view_for_interrupt",
    "live_snapshot",
    "live_snapshot_async",
]

_TERMINAL_PART_STATES = frozenset({
    "approved",
    "denied",
    "timeout",
    "error",
    "settled",
    "done",
    "inflight",
})
_INFLIGHT_ROW_STATES = frozenset({"claimed", "resuming"})
_TERMINAL_ROW_STATES = frozenset({
    "settled",
    "timeout",
    "superseded",
    "error",
})


class GateView(TypedDict):
    gate_id: str
    interrupt_id: str
    tool: str
    kind: str
    state: str
    interactive: bool
    slot: str


def interrupt_id_of(part: Mapping[str, Any] | None) -> str:
    """Interrupt / gate id stamped on a HITL part (or its payload)."""
    if not isinstance(part, Mapping):
        return ""
    raw = part.get("interrupt_id") or part.get("gate_id")
    if raw:
        return str(raw).strip()
    payload = part.get("payload")
    if isinstance(payload, Mapping):
        return str(
            payload.get("interrupt_id") or payload.get("gate_id") or ""
        ).strip()
    return ""


def _part_state(part: Mapping[str, Any]) -> str:
    return str(part.get("state") or "pending").strip().lower() or "pending"


def _part_tool(part: Mapping[str, Any]) -> str:
    raw = str(part.get("tool") or part.get("tool_name") or "").strip()
    if raw:
        return raw
    payload = part.get("payload")
    if isinstance(payload, Mapping):
        return str(payload.get("tool") or payload.get("tool_name") or "").strip()
    return ""


def _part_kind(part: Mapping[str, Any]) -> str:
    payload = part.get("payload")
    if isinstance(payload, Mapping):
        kind = str(payload.get("kind") or "").strip()
        if kind:
            return kind
    return str(part.get("kind") or "security").strip() or "security"


def _row_id(row: Any) -> str:
    return str(getattr(row, "gate_id", "") or "").strip()


def _row_alias(row: Any) -> str:
    return str(getattr(row, "alias_id", "") or "").strip()


def _row_state(row: Any) -> str:
    return str(getattr(row, "state", "") or "").strip().lower()


def _row_tool(row: Any) -> str:
    return str(getattr(row, "tool", "") or "").strip()


def _row_kind(row: Any) -> str:
    return str(getattr(row, "kind", "") or "security").strip() or "security"


def _index_rows(live_rows: Sequence[Any] | None) -> dict[str, Any]:
    by: dict[str, Any] = {}
    for row in live_rows or ():
        gid = _row_id(row)
        if gid and gid not in by:
            by[gid] = row
        alias = _row_alias(row)
        if alias and alias not in by:
            by[alias] = row
    return by


def _view(
    *,
    gate_id: str,
    interrupt_id: str,
    tool: str,
    kind: str,
    state: str,
    interactive: bool,
    slot: str,
) -> GateView:
    return {
        "gate_id": gate_id,
        "interrupt_id": interrupt_id or gate_id,
        "tool": tool,
        "kind": kind or "security",
        "state": state,
        "interactive": bool(interactive),
        "slot": slot,
    }


def _view_from_row(row: Any, *, part: Mapping[str, Any] | None = None) -> GateView:
    iid = interrupt_id_of(part) if part else ""
    gid = _row_id(row)
    st = _row_state(row)
    tool = _part_tool(part) if part else ""
    kind = _part_kind(part) if part else ""
    if st == "pending":
        out = _view(
            gate_id=gid,
            interrupt_id=iid or gid,
            tool=tool or _row_tool(row),
            kind=kind or _row_kind(row),
            state="pending",
            interactive=True,
            slot="pending",
        )
        try:
            pl = row.payload() if hasattr(row, "payload") else None
            if isinstance(pl, dict) and pl:
                out["payload"] = pl  # type: ignore[typeddict-unknown-key]
                if not out["tool"]:
                    out["tool"] = str(pl.get("tool") or pl.get("tool_name") or "")
        except Exception:
            pass
        return out
    if st in _INFLIGHT_ROW_STATES:
        return _view(
            gate_id=gid,
            interrupt_id=iid or gid,
            tool=tool or _row_tool(row),
            kind=kind or _row_kind(row),
            state="inflight",
            interactive=False,
            slot="settled",
        )
    outcome = st
    if st == "settled":
        decision = str(getattr(row, "decision", "") or "").strip().lower()
        if decision in ("approve", "approved"):
            outcome = "approved"
        elif decision in ("deny", "denied", "aborted"):
            outcome = "denied"
        elif part is not None:
            ps = _part_state(part)
            if ps in _TERMINAL_PART_STATES:
                outcome = ps
    return _view(
        gate_id=gid,
        interrupt_id=iid or gid,
        tool=tool or _row_tool(row),
        kind=kind or _row_kind(row),
        state=outcome,
        interactive=False,
        slot="settled",
    )


def _view_from_part_alone(part: Mapping[str, Any]) -> GateView:
    iid = interrupt_id_of(part)
    ps = _part_state(part)
    if ps == "pending":
        state = "error"
    elif ps in _TERMINAL_PART_STATES:
        state = ps
    else:
        state = ps or "error"
    return _view(
        gate_id=iid,
        interrupt_id=iid,
        tool=_part_tool(part),
        kind=_part_kind(part),
        state=state,
        interactive=False,
        slot="settled",
    )


def resolve_gate_views(
    parts: Sequence[Mapping[str, Any]] | None,
    live_rows: Sequence[Any] | None,
    *,
    authoritative: bool,
) -> list[GateView]:
    """Return the display state for every HITL part (and uncovered live rows).

    ``authoritative=False`` (registry unread / kill-switch / missing list)
    omits every gate — honest empty, not a lying card.

    ``live_rows`` is the ``LIVE_STATES`` snapshot (pending / claimed /
    resuming). Historical settled gates have no covering row; their label
    comes from the part stamp inside this function, never from the client.
    """
    if not authoritative:
        return []

    by_id = _index_rows(live_rows)
    seen_row_ids: set[str] = set()
    out: list[GateView] = []

    for part in hitl_parts_of(list(parts) if parts is not None else None):
        iid = interrupt_id_of(part)
        row = by_id.get(iid) if iid else None
        if row is not None:
            view = _view_from_row(row, part=part)
            seen_row_ids.add(_row_id(row))
        else:
            view = _view_from_part_alone(part)
        out.append(view)

    for row in live_rows or ():
        gid = _row_id(row)
        if not gid or gid in seen_row_ids:
            continue
        out.append(_view_from_row(row))
        seen_row_ids.add(gid)
    return out


def apply_views_to_parts(
    parts: Sequence[Mapping[str, Any]] | None,
    views: Sequence[Mapping[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Shallow-copy *parts* and stamp matching HITL entries with ``view``.

    Does not mutate the input. Parts without a matching view are copied
    unchanged (no ``view`` key) so a client that consumes ``part.view``
    omits chrome rather than inventing it.
    """
    by: dict[str, Mapping[str, Any]] = {}
    for view in views or ():
        if not isinstance(view, Mapping):
            continue
        for key in (view.get("interrupt_id"), view.get("gate_id")):
            kid = str(key or "").strip()
            if kid and kid not in by:
                by[kid] = view
    out: list[dict[str, Any]] = []
    for part in parts or ():
        if not isinstance(part, Mapping):
            continue
        copied = dict(part)
        if str(copied.get("type") or "") == "hitl":
            iid = interrupt_id_of(copied)
            view = by.get(iid) if iid else None
            if view is not None:
                copied["view"] = dict(view)
        out.append(copied)
    return out


def stamp_parts_for_read(
    parts: Sequence[Mapping[str, Any]] | None,
    live_rows: Sequence[Any] | None,
    *,
    authoritative: bool,
) -> list[dict[str, Any]] | None:
    """Read-side stamp used by ``GET /messages``. None in, None out."""
    if parts is None:
        return None
    views = resolve_gate_views(parts, live_rows, authoritative=authoritative)
    return apply_views_to_parts(parts, views)


def view_for_interrupt(
    thread_id: str,
    interrupt_id: str,
    *,
    tool: str = "",
    state_hint: str = "",
) -> GateView | None:
    """Display view for one interrupt after a claim/409. Never raises.

    After approve CAS the live row is claimed/resuming, so the view is
    inflight (not pending). If the snapshot misses, return a non-interactive
    inflight view so the client does not jump the card back under the answer.
    """
    iid = str(interrupt_id or "").strip()
    if not iid:
        return None
    try:
        rows, auth = live_snapshot(thread_id)
        part = {
            "type": "hitl",
            "interrupt_id": iid,
            "tool": tool,
            "state": state_hint or "pending",
        }
        views = resolve_gate_views([part], rows, authoritative=bool(auth) or bool(rows))
        if views:
            return views[0]
    except Exception:
        logger.debug("[gate_view] view_for_interrupt failed", exc_info=True)
    hint = str(state_hint or "inflight").strip().lower() or "inflight"
    if hint == "pending":
        hint = "inflight"
    interactive = hint == "pending"
    return {
        "gate_id": iid,
        "interrupt_id": iid,
        "tool": str(tool or ""),
        "kind": "security",
        "state": hint,
        "interactive": interactive,
        "slot": "pending" if interactive else "settled",
    }


def attach_view_to_hitl_frame(frame: dict[str, Any], thread_id: str) -> dict[str, Any]:
    """Best-effort: stamp ``view`` on a journaled HITL frame. Never raises."""
    try:
        from kazma_ui.hitl_gate_bridge import registry_on
        from kazma_core.safety.hitl_gates import live_gates

        if not thread_id or not registry_on():
            return frame
        data = frame.get("data") if isinstance(frame.get("data"), dict) else None
        src = data if data is not None else frame
        part = {
            "type": "hitl",
            "state": str(src.get("state") or src.get("hitl_state") or "pending"),
            "interrupt_id": str(
                src.get("interrupt_id") or src.get("gate_id") or ""
            ),
            "tool": str(src.get("tool") or ""),
            "payload": src,
        }
        rows = live_gates(thread_id)
        views = resolve_gate_views([part], rows, authoritative=True)
        if not views:
            return frame
        view = dict(views[0])
        out = dict(frame)
        out["view"] = view
        if data is not None:
            payload = dict(data)
            payload["view"] = view
            out["data"] = payload
        return out
    except Exception:
        logger.debug("[gate_view] hitl frame stamp skipped", exc_info=True)
        return frame


def live_snapshot(thread_id: str) -> tuple[list[Any], bool]:
    """``(rows, authoritative)``. Failures are ``([], False)`` — omit chrome."""
    if not thread_id:
        return [], False
    try:
        from kazma_ui.hitl_gate_bridge import registry_on
        from kazma_core.safety.hitl_gates import live_gates

        if not registry_on():
            return [], False
        return list(live_gates(thread_id)), True
    except Exception:
        logger.debug("[gate_view] live snapshot failed", exc_info=True)
        return [], False


async def live_snapshot_async(thread_id: str) -> tuple[list[Any], bool]:
    if not thread_id:
        return [], False
    try:
        from kazma_ui.hitl_gate_bridge import registry_on
        from kazma_core.safety.hitl_gates import live_gates_async

        if not registry_on():
            return [], False
        return list(await live_gates_async(thread_id)), True
    except Exception:
        logger.debug("[gate_view] live snapshot failed", exc_info=True)
        return [], False
