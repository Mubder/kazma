"""Optional native computer-use planners (Anthropic CUA / Gemini).

The actuator stays Playwright. Without a matching model, callers fall back
to the vision-JSON loop in ``computer_use.plan_next_action``. Kill-switch:
``KAZMA_CUA_PLANNER=0``. Not a desktop VM.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from kazma_core.tools.computer_use import ComputerAction, parse_action

logger = logging.getLogger(__name__)

__all__ = [
    "adapt_planner_payload",
    "cua_planner_enabled",
    "plan_with_native_api",
    "resolve_planner_kind",
]

_ANTHROPIC_BETA = "computer-use-2025-01-24"
_DISPLAY_W = 1280
_DISPLAY_H = 800


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


async def plan_with_native_api(
    goal: str,
    screenshot_b64: str,
    history: list[str],
    *,
    kind: str | None = None,
    http_post: Any | None = None,
    chat_fn: Any | None = None,
) -> ComputerAction | None:
    """Call Anthropic CUA or Gemini function-calling. None = fall back."""
    kind = kind or resolve_planner_kind()
    if kind == "anthropic_cua":
        return await _native_anthropic(
            goal, screenshot_b64, history, http_post=http_post
        )
    if kind == "gemini_computer_use":
        return await _native_gemini(
            goal, screenshot_b64, history, chat_fn=chat_fn
        )
    return None


def _planner_prompt(goal: str, history: list[str]) -> str:
    return (
        "Drive the computer toward this goal:\n"
        f"{(goal or '').strip()}\n\nPrior steps:\n"
        + ("\n".join(history[-8:]) if history else "(none)")
        + "\nTake ONE action."
    )


async def _native_anthropic(
    goal: str,
    screenshot_b64: str,
    history: list[str],
    *,
    http_post: Any | None = None,
) -> ComputerAction | None:
    payload = {
        "model": "",
        "max_tokens": 1024,
        "tools": [
            {
                "type": "computer_20250124",
                "name": "computer",
                "display_width_px": _DISPLAY_W,
                "display_height_px": _DISPLAY_H,
            }
        ],
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": screenshot_b64,
                        },
                    },
                    {"type": "text", "text": _planner_prompt(goal, history)},
                ],
            }
        ],
    }
    if http_post is None:
        try:
            from kazma_core.model_registry import get_model_registry
            from kazma_core.anthropic_llm import AnthropicProvider

            client = get_model_registry().get_client()
            if not isinstance(client, AnthropicProvider):
                return None
            payload["model"] = client.config.model
            http = await client._get_client()

            async def _post(_path: str, *, json: Any, headers: Any | None = None) -> Any:
                return await http.post(
                    "/messages",
                    json=json,
                    headers=headers or {"anthropic-beta": _ANTHROPIC_BETA},
                )

            http_post = _post
        except Exception:
            logger.debug("[cua] anthropic client unavailable", exc_info=True)
            return None
    try:
        resp = await http_post(
            "/messages",
            json=payload,
            headers={"anthropic-beta": _ANTHROPIC_BETA},
        )
    except Exception:
        logger.debug("[cua] anthropic POST failed", exc_info=True)
        return None
    status = int(getattr(resp, "status_code", 0) or 0)
    if status >= 400:
        logger.info("[cua] anthropic HTTP %s — falling back to vision-JSON", status)
        return None
    try:
        data = resp.json() if callable(getattr(resp, "json", None)) else {}
        if asyncio.iscoroutine(data):
            data = await data
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    for block in data.get("content") or []:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "tool_use" and isinstance(block.get("input"), dict):
            return adapt_planner_payload("anthropic_cua", block["input"])
        if block.get("type") == "text" and block.get("text"):
            adapted = adapt_planner_payload("anthropic_cua", block["text"])
            if adapted is not None:
                return adapted
    return None


async def _native_gemini(
    goal: str,
    screenshot_b64: str,
    history: list[str],
    *,
    chat_fn: Any | None = None,
) -> ComputerAction | None:
    """Gemini: force a computer_action function call on the OpenAI-compat path."""
    from kazma_core.tools.computer_use import ACTIONS, parse_action

    tool = {
        "type": "function",
        "function": {
            "name": "computer_action",
            "description": "Take one UI action on the screenshot.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": sorted(ACTIONS)},
                    "x": {"type": "integer"},
                    "y": {"type": "integer"},
                    "text": {"type": "string"},
                    "key": {"type": "string"},
                    "dy": {"type": "integer"},
                    "url": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["action"],
            },
        },
    }
    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/png;base64,{screenshot_b64}"
                    },
                },
                {"type": "text", "text": _planner_prompt(goal, history)},
            ],
        }
    ]
    if chat_fn is None:
        try:
            from kazma_core.tools.vision_analyze import _get_llm_provider

            provider, _mid, _reason = _get_llm_provider()
            if provider is None:
                return None

            async def _chat(msgs: list[dict[str, Any]]) -> Any:
                return await provider.chat(msgs, tools=[tool])

            chat_fn = _chat
        except Exception:
            return None
    try:
        result = await chat_fn(messages)
    except Exception:
        logger.debug("[cua] gemini chat failed", exc_info=True)
        return None
    if isinstance(result, ComputerAction):
        return result
    tcs = getattr(result, "tool_calls", None) or []
    if tcs:
        first = tcs[0]
        args = getattr(first, "arguments", None) or {}
        if isinstance(args, str):
            return parse_action(args)
        if isinstance(args, dict):
            return parse_action(args)
    return adapt_planner_payload("gemini_computer_use", result)


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
