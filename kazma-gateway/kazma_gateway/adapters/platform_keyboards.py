"""Cross-platform interactive control builders.

Telegram uses inline keyboards; Discord uses message components; Slack uses
Block Kit.  Action *IDs* are shared (see :mod:`platform_callbacks`) so HITL
and model pickers behave the same on every platform.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "discord_approval_components",
    "discord_model_components",
    "discord_personality_components",
    "discord_provider_components",
    "slack_approval_blocks",
    "slack_model_blocks",
    "slack_personality_blocks",
    "slack_provider_blocks",
]


# ── Discord components (v2 action rows) ───────────────────────────────────


def discord_approval_components(request_id: str) -> list[dict[str, Any]]:
    """HITL Approve / Deny buttons for Discord."""
    return [
        {
            "type": 1,
            "components": [
                {
                    "type": 2,
                    "style": 3,
                    "label": "Approve",
                    "custom_id": f"hitl:approve:{request_id}",
                },
                {
                    "type": 2,
                    "style": 4,
                    "label": "Deny",
                    "custom_id": f"hitl:deny:{request_id}",
                },
            ],
        }
    ]


def discord_personality_components(personalities: list[str]) -> list[dict[str, Any]]:
    """Personality picker (up to 5 buttons, one row each if needed)."""
    rows: list[dict[str, Any]] = []
    row: list[dict[str, Any]] = []
    for name in personalities[:5]:
        row.append(
            {
                "type": 2,
                "style": 2,
                "label": name[:80],
                "custom_id": f"personality:{name}"[:100],
            }
        )
        if len(row) == 2:
            rows.append({"type": 1, "components": row})
            row = []
    if row:
        rows.append({"type": 1, "components": row})
    return rows


def discord_provider_components(providers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    row: list[dict[str, Any]] = []
    for p in providers[:10]:
        name = p.get("name", p.get("display_name", "?"))
        display = str(p.get("display_name", name))[:80]
        row.append(
            {
                "type": 2,
                "style": 1,
                "label": display,
                "custom_id": f"model_provider:{name}"[:100],
            }
        )
        if len(row) == 2:
            rows.append({"type": 1, "components": row})
            row = []
    if row:
        rows.append({"type": 1, "components": row})
    return rows


def discord_model_components(provider_name: str, models: list[str]) -> list[dict[str, Any]]:
    del provider_name
    rows: list[dict[str, Any]] = []
    for model_id in models[:15]:
        display = model_id.split("/")[-1] if "/" in model_id else model_id
        rows.append(
            {
                "type": 1,
                "components": [
                    {
                        "type": 2,
                        "style": 2,
                        "label": display[:80],
                        "custom_id": f"model_select:{model_id}"[:100],
                    }
                ],
            }
        )
    return rows


# ── Slack Block Kit ───────────────────────────────────────────────────────


def slack_approval_blocks(request_id: str, text: str = "Approval required") -> list[dict[str, Any]]:
    return [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"⚠️ *{text}*"},
        },
        {
            "type": "actions",
            "block_id": f"hitl_actions_{request_id}",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Approve"},
                    "style": "primary",
                    "value": f"hitl:approve:{request_id}",
                    "action_id": f"hitl_approve_{request_id}",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Deny"},
                    "style": "danger",
                    "value": f"hitl:deny:{request_id}",
                    "action_id": f"hitl_deny_{request_id}",
                },
            ],
        },
    ]


def slack_personality_blocks(personalities: list[str]) -> list[dict[str, Any]]:
    elements = [
        {
            "type": "button",
            "text": {"type": "plain_text", "text": name[:75]},
            "value": f"personality:{name}",
            "action_id": f"personality_{i}",
        }
        for i, name in enumerate(personalities[:5])
    ]
    return [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "*Choose a personality*"},
        },
        {"type": "actions", "elements": elements},
    ]


def slack_provider_blocks(providers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    elements = []
    for i, p in enumerate(providers[:10]):
        name = p.get("name", p.get("display_name", "?"))
        display = str(p.get("display_name", name))[:75]
        elements.append(
            {
                "type": "button",
                "text": {"type": "plain_text", "text": display},
                "value": f"model_provider:{name}",
                "action_id": f"model_provider_{i}",
            }
        )
    return [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "*Choose a provider*"},
        },
        {"type": "actions", "elements": elements},
    ]


def slack_model_blocks(provider_name: str, models: list[str]) -> list[dict[str, Any]]:
    del provider_name
    elements = []
    for i, model_id in enumerate(models[:15]):
        display = model_id.split("/")[-1] if "/" in model_id else model_id
        elements.append(
            {
                "type": "button",
                "text": {"type": "plain_text", "text": display[:75]},
                "value": f"model_select:{model_id}",
                "action_id": f"model_select_{i}",
            }
        )
    # Slack max 5 buttons per actions block
    blocks: list[dict[str, Any]] = [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "*Choose a model*"},
        }
    ]
    for i in range(0, len(elements), 5):
        blocks.append({"type": "actions", "elements": elements[i : i + 5]})
    return blocks
