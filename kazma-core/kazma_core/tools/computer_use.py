"""Screenshot → action computer-use loop.

Playwright is the actuator; a vision model picks the next click/type/key.
This is a separate family from selector-based ``browser_*`` tools.

HITL danger-tier. Kill-switch: ``KAZMA_COMPUTER_USE=0``.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "ACTIONS",
    "ComputerAction",
    "HARD_CAP",
    "computer_use",
    "computer_use_enabled",
    "parse_action",
    "plan_next_action",
]

# Native CUA / Gemini planners are optional adapters in
# ``computer_use_planners`` — this module keeps the vision-JSON fallback.

ACTIONS = frozenset(
    {"click", "type", "key", "scroll", "wait", "navigate", "done", "fail"}
)
DEFAULT_MAX_STEPS = 8
HARD_CAP = 15

_ACTION_SCHEMA: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "computer_action",
        "schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": sorted(ACTIONS),
                },
                "x": {"type": "integer"},
                "y": {"type": "integer"},
                "text": {"type": "string"},
                "key": {"type": "string"},
                "dy": {"type": "integer"},
                "url": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["action"],
            "additionalProperties": False,
        },
    },
}


@dataclass
class ComputerAction:
    action: str
    x: int = 0
    y: int = 0
    text: str = ""
    key: str = ""
    dy: int = 0
    url: str = ""
    reason: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


def computer_use_enabled() -> bool:
    return os.environ.get("KAZMA_COMPUTER_USE", "1").strip().lower() not in (
        "0",
        "false",
        "off",
        "no",
    )


def parse_action(raw: Any) -> ComputerAction:
    """Parse a model JSON blob (or dict) into a :class:`ComputerAction`."""
    data: Any = raw
    if isinstance(raw, str):
        text = raw.strip()
        fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
        if fence:
            text = fence.group(1)
        else:
            start, end = text.find("{"), text.rfind("}")
            if start >= 0 and end > start:
                text = text[start : end + 1]
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return ComputerAction(action="fail", reason="unparseable action JSON")
    if not isinstance(data, dict):
        return ComputerAction(action="fail", reason="action is not an object")
    action = str(data.get("action") or "fail").strip().lower()
    if action not in ACTIONS:
        return ComputerAction(action="fail", reason=f"unknown action {action!r}", raw=data)

    def _int(key: str) -> int:
        try:
            return int(data.get(key) or 0)
        except (TypeError, ValueError):
            return 0

    return ComputerAction(
        action=action,
        x=_int("x"),
        y=_int("y"),
        text=str(data.get("text") or ""),
        key=str(data.get("key") or ""),
        dy=_int("dy"),
        url=str(data.get("url") or ""),
        reason=str(data.get("reason") or ""),
        raw=data,
    )


async def plan_next_action(
    goal: str,
    screenshot_b64: str,
    history: list[str],
    *,
    chat_fn: Any | None = None,
) -> ComputerAction:
    """Ask a vision model for the next action. *chat_fn* is injectable in tests."""
    prompt = (
        "You are driving a computer for the user. Goal:\n"
        f"{goal.strip()}\n\n"
        "Prior steps:\n"
        + ("\n".join(history[-8:]) if history else "(none)")
        + "\n\nReturn ONE JSON object: "
        '{"action":"click|type|key|scroll|wait|navigate|done|fail",'
        '"x":0,"y":0,"text":"","key":"","dy":0,"url":"","reason":""}. '
        "click uses screenshot pixel coordinates. done when the goal is met."
    )
    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{screenshot_b64}"},
                },
                {"type": "text", "text": prompt},
            ],
        }
    ]
    if chat_fn is None:
        chat_fn = _default_chat
    try:
        result = await chat_fn(messages)
    except Exception as exc:
        logger.warning("[computer_use] plan failed: %s", exc)
        return ComputerAction(action="fail", reason=str(exc)[:400])
    if isinstance(result, ComputerAction):
        return result
    try:
        from kazma_core.tools.computer_use_planners import (
            adapt_planner_payload,
            resolve_planner_kind,
        )

        native = adapt_planner_payload(resolve_planner_kind(), result)
        if native is not None:
            return native
    except Exception:
        logger.debug("[computer_use] native planner adapt skipped", exc_info=True)
    if isinstance(result, dict) and "action" in result:
        return parse_action(result)
    text = ""
    if hasattr(result, "content"):
        text = str(getattr(result, "content") or "")
    elif isinstance(result, dict):
        text = str(result.get("content") or result.get("text") or "")
    else:
        text = str(result or "")
    return parse_action(text)


async def _default_chat(messages: list[dict[str, Any]]) -> Any:
    from kazma_core.tools.vision_analyze import _get_llm_provider

    provider, model_id, reason = _get_llm_provider()
    if provider is None:
        raise RuntimeError(
            "No vision-capable model is configured for computer_use "
            f"({reason}). Set a GPT-4o / Claude / Gemini vision model, "
            "or KAZMA_VISION_MODELS."
        )
    try:
        return await provider.chat(
            messages,
            response_format=_ACTION_SCHEMA,
        )
    except Exception:
        return await provider.chat(messages)


async def computer_use(
    goal: str,
    url: str = "",
    max_steps: int = DEFAULT_MAX_STEPS,
) -> str:
    """Run a bounded screenshot→action loop toward *goal*.

    Optional *url* is opened first. Danger-tier (HITL). Does not start a
    desktop session — the actuator is the existing Playwright page.
    """
    if not computer_use_enabled():
        return "Error: computer_use is disabled (KAZMA_COMPUTER_USE=0)."
    text = (goal or "").strip()
    if not text:
        return "Error: goal is required."
    try:
        steps = max(1, min(int(max_steps or DEFAULT_MAX_STEPS), HARD_CAP))
    except (TypeError, ValueError):
        steps = DEFAULT_MAX_STEPS

    transcript: list[str] = []
    start = (url or "").strip()
    if start:
        nav = await _act(ComputerAction(action="navigate", url=start, reason="start url"))
        transcript.append(f"navigate {start}: {nav}")

    for i in range(1, steps + 1):
        shot = await _screenshot_b64()
        if shot.startswith("Error:"):
            return shot if not transcript else "\n".join(transcript) + "\n" + shot
        action = await plan_next_action(text, shot, transcript)
        if action.action == "done":
            transcript.append(f"step {i}: done ({action.reason or 'goal met'})")
            break
        if action.action == "fail":
            transcript.append(f"step {i}: fail ({action.reason or 'model stop'})")
            break
        result = await _act(action)
        transcript.append(f"step {i}: {action.action} → {result[:500]}")
        if result.startswith("Error:") and "Playwright" in result:
            break
    else:
        transcript.append(f"stopped after {steps} steps (cap)")

    return "\n".join(transcript) if transcript else "No computer-use steps ran."


async def _screenshot_b64() -> str:
    try:
        from kazma_skills.native.browser_automation.tools import (
            _run_sync,
            _screenshot_sync,
        )
    except Exception as exc:
        return f"Error: browser automation unavailable ({exc}). pip install playwright && playwright install chromium"
    try:
        dest = await _run_sync(_screenshot_sync, False)
    except RuntimeError as exc:
        return str(exc)
    except Exception as exc:
        return f"Error: screenshot failed — {type(exc).__name__}: {exc}"
    try:
        from pathlib import Path

        from kazma_core.tools.vision_analyze import _build_data_uri, _resize_image

        raw = Path(str(dest)).read_bytes()
        raw = _resize_image(raw)
        uri = _build_data_uri(raw, "image/png")
        return uri.split(",", 1)[-1]
    except Exception as exc:
        return f"Error: could not encode screenshot ({exc})"


async def _act(action: ComputerAction) -> str:
    try:
        from kazma_skills.native.browser_automation.tools import (
            _key_press_sync,
            _mouse_click_sync,
            _navigate_sync,
            _run_sync,
            _scroll_sync,
            _type_text_sync,
            _wait_sync,
        )
    except Exception as exc:
        return f"Error: Playwright actuator unavailable ({exc})"

    try:
        if action.action == "navigate":
            target = (action.url or "").strip()
            if not target:
                return "Error: navigate needs url"
            if not target.startswith(("http://", "https://")):
                target = "https://" + target
            title, body = await _run_sync(_navigate_sync, target)
            return f"title={title} text={(body or '')[:400]}"
        if action.action == "click":
            return await _run_sync(_mouse_click_sync, action.x, action.y)
        if action.action == "type":
            return await _run_sync(_type_text_sync, action.text)
        if action.action == "key":
            return await _run_sync(_key_press_sync, action.key or "Enter")
        if action.action == "scroll":
            return await _run_sync(_scroll_sync, action.dy or 400)
        if action.action == "wait":
            return await _run_sync(_wait_sync, 0.8)
        return f"unhandled {action.action}"
    except RuntimeError as exc:
        return str(exc)
    except Exception as exc:
        return f"Error: {action.action} failed — {type(exc).__name__}: {exc}"
