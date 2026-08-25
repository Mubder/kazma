"""Optional native computer-use planners (Anthropic CUA / Gemini).

The actuator stays Playwright. Without a matching model, callers fall back
to the vision-JSON loop in ``computer_use.plan_next_action``. Kill-switch:
``KAZMA_CUA_PLANNER=0``. Not a desktop VM.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from kazma_core.tools.computer_use import ComputerAction, parse_action

logger = logging.getLogger(__name__)

__all__ = [
    "adapt_planner_payload",
    "cua_planner_enabled",
    "resolve_planner_kind",
]


def cua_planner_enabled() -> bool:
    return os.environ.get("KAZMA_CUA_PLANNER", "1").strip().lower() not in (
        "0",
        "false",
        "off",
        "no",
    )


def resolve_planner_kind(model_id: str | None = None) -> str:
    """``anthropic_cua`` | ``gemini_computer_use`` | ``vision_json``."""
    if not cua_planner_enabled():
        return "vision_json"
    mid = (model_id or "").strip().lower()
    if not mid:
        try:
            from kazma_core.model_registry import get_model_registry

            mid = str(
                (get_model_registry().get_active_profile() or {}).get("model") or ""
            ).lower()
        except Exception:
            mid = ""
    if "claude" in mid:
        return "anthropic_cua"
    if "gemini" in mid:
        return "gemini_computer_use"
    return "vision_json"


def adapt_planner_payload(kind: str, raw: Any) -> ComputerAction | None:
    """Map a native CUA / Gemini payload to :class:`ComputerAction`.

    Returns ``None`` when the payload is not native-shaped so the caller
    can fall back to vision-JSON ``parse_action``.
    """
    if kind in ("", "vision_json"):
        return None
    data = raw
    if hasattr(raw, "content") and not isinstance(raw, dict):
        data = getattr(raw, "content")
    if kind == "anthropic_cua":
        action = _from_anthropic_cua(data)
        if action is not None:
            return action
    if kind == "gemini_computer_use":
        action = _from_gemini(data)
        if action is not None:
            return action
    return None


def _scroll_dy(data: dict[str, Any], mapped: str) -> int:
    if mapped != "scroll":
        try:
            return int(data.get("dy") or 0)
        except (TypeError, ValueError):
            return 0
    direction = str(data.get("scroll_direction") or data.get("direction") or "").lower()
    if direction == "down":
        return 400
    if direction == "up":
        return -400
    try:
        return int(data.get("dy") or 0)
    except (TypeError, ValueError):
        return 0


def _as_dict(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        parsed = parse_action(raw)
        if parsed.action != "fail" or parsed.raw:
            return parsed.raw or None
    return None


def _from_anthropic_cua(raw: Any) -> ComputerAction | None:
    data = _as_dict(raw)
    if data is None:
        return None
    # Native computer_20250124: {action, coordinate, text, ...}
    action = str(data.get("action") or data.get("type") or "").strip().lower()
    if not action:
        return None
    native = {
        "left_click": "click",
        "right_click": "click",
        "double_click": "click",
        "triple_click": "click",
        "click": "click",
        "type": "type",
        "key": "key",
        "scroll": "scroll",
        "wait": "wait",
        "screenshot": "wait",
        "mouse_move": "click",
        "left_click_drag": "click",
        "navigate": "navigate",
        "done": "done",
        "fail": "fail",
    }
    mapped = native.get(action)
    if mapped is None:
        return None
    coord = data.get("coordinate") or data.get("coordinates") or []
    x, y = 0, 0
    if isinstance(coord, (list, tuple)) and len(coord) >= 2:
        try:
            x, y = int(coord[0]), int(coord[1])
        except (TypeError, ValueError):
            x, y = 0, 0
    else:
        try:
            x = int(data.get("x") or 0)
            y = int(data.get("y") or 0)
        except (TypeError, ValueError):
            x, y = 0, 0
    return ComputerAction(
        action=mapped,
        x=x,
        y=y,
        text=str(data.get("text") or ""),
        key=str(data.get("key") or data.get("text") or "") if mapped == "key" else str(data.get("key") or ""),
        dy=_scroll_dy(data, mapped),
        url=str(data.get("url") or ""),
        reason=str(data.get("reason") or action),
        raw=data,
    )


def _from_gemini(raw: Any) -> ComputerAction | None:
    data = _as_dict(raw)
    if data is None:
        return None
    # Function-call style: {name: click_at, args: {x, y}} or {action: ...}
    name = str(data.get("name") or data.get("action") or "").strip().lower()
    args = data.get("args") if isinstance(data.get("args"), dict) else data
    if not name:
        return None
    native = {
        "click_at": "click",
        "click": "click",
        "type_text": "type",
        "type": "type",
        "key_press": "key",
        "key": "key",
        "scroll": "scroll",
        "navigate": "navigate",
        "open_web_browser": "navigate",
        "wait": "wait",
        "go_back": "key",
        "done": "done",
        "fail": "fail",
    }
    mapped = native.get(name)
    if mapped is None:
        return None
    try:
        x = int(args.get("x") or args.get("x_pixel") or 0)
        y = int(args.get("y") or args.get("y_pixel") or 0)
    except (TypeError, ValueError):
        x, y = 0, 0
    key = str(args.get("key") or "")
    if name == "go_back":
        key = key or "Alt+ArrowLeft"
    return ComputerAction(
        action=mapped,
        x=x,
        y=y,
        text=str(args.get("text") or ""),
        key=key,
        dy=int(args.get("dy") or 0),
        url=str(args.get("url") or args.get("uri") or ""),
        reason=str(args.get("reason") or name),
        raw=data,
    )
