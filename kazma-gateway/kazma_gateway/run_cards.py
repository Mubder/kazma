"""Incident cards on Telegram, Discord and Slack: one button per action.

The run supervisor (``kazma_core.runs.notify``) sends an incident through
``send_message(..., run_card={"incident_id", "actions", "labels"})``; the
gateway's send backend turns that into the platform's own buttons here. A
button carries ``run:<action>:<incident_id>``; pressing it becomes the
command ``/runs act <incident_id> <action>`` from the person who pressed
(``platform_callbacks.parse_callback_data``), handled like any slash command
-- admin-only (``slash_commands.changes_global_config``) -- by
``agent_handler.runs_command``.
"""

from __future__ import annotations

from typing import Any

from kazma_gateway.adapters.callback_store import encode_callback_data

__all__ = ["apply_run_card_markup"]

_DANGER = frozenset({"cancel", "close"})


def _run_callback(action: str, incident_id: str) -> str:
    return f"run:{action}:{incident_id}"


def _buttons(card: dict[str, Any]) -> list[tuple[str, str, str]]:
    """``(action, label, callback)`` for each action the card offers."""
    incident = str(card.get("incident_id") or "")
    labels = card.get("labels") or {}
    return [(a, str(labels.get(a) or a), _run_callback(a, incident))
            for a in card.get("actions") or [] if incident]


def _telegram(card: dict[str, Any]) -> dict[str, Any]:
    rows = [[{"text": label[:60], "callback_data": encode_callback_data(data)}]
            for _a, label, data in _buttons(card)]
    return {"inline_keyboard": rows}


def _discord(card: dict[str, Any]) -> list[dict[str, Any]]:
    buttons = [{"type": 2, "style": 4 if action in _DANGER else 1, "label": label[:80],
                "custom_id": encode_callback_data(data, max_bytes=100)}
               for action, label, data in _buttons(card)]
    return [{"type": 1, "components": buttons[i:i + 5]} for i in range(0, len(buttons), 5)]


def _slack(card: dict[str, Any], text: str) -> list[dict[str, Any]]:
    elements = []
    for action, label, data in _buttons(card):
        button: dict[str, Any] = {"type": "button", "text": {"type": "plain_text", "text": label[:75]},
                                  "value": data, "action_id": f"run_{action}_{card['incident_id']}"[:255]}
        if action in _DANGER:
            button["style"] = "danger"
        elements.append(button)
    blocks: list[dict[str, Any]] = [{"type": "section", "text": {"type": "mrkdwn", "text": text[:2900]}}]
    if elements:
        blocks.append({"type": "actions", "block_id": f"run_actions_{card['incident_id']}",
                       "elements": elements})
    return blocks


def apply_run_card_markup(ctx: dict[str, Any], *, platform: str, run_card: dict[str, Any] | None,
                          text: str = "") -> dict[str, Any]:
    """Attach an incident's buttons to a send context (no buttons: unchanged)."""
    out = dict(ctx or {})
    if not isinstance(run_card, dict) or not _buttons(run_card):
        return out
    plat = (platform or "telegram").lower()
    if plat == "telegram":
        out["reply_markup"] = _telegram(run_card)
    elif plat == "discord":
        out["components"] = _discord(run_card)
    elif plat == "slack":
        out["blocks"] = _slack(run_card, text)
    return out
