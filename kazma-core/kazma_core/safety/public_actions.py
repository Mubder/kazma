"""What an always-ask card says it does.

The always-ask tools (``safety.hitl.ALWAYS_HITL_TOOLS``: X writes) act in
public and cannot be undone. Their approval card used to look like every other
card -- "Approval required", a tool name, "Approve" -- and on 2026-10-16 the
owner approved an ``x_post`` card believing it was part of reading the
metrics. Each card now names the public action in its header and on the
button that does it ("Post now", never "Approve").

This table is the chat apps' wording (they answer in English). The web card
and the approvals panel read the same words from the catalog
(``chat.hitl_public_<tool>`` and ``chat.hitl_public_<tool>_btn``, English and
Arabic). ``tests/test_public_action_cards.py`` holds the three to one list.
"""

from __future__ import annotations

__all__ = ["PUBLIC_ACTIONS", "public_action"]

#: tool -> (card header, the approving button's label)
PUBLIC_ACTIONS: dict[str, tuple[str, str]] = {
    "x_post": ("Publish to X — public, can't be undone", "Post now"),
    "x_schedule_post": ("Schedule a public post on X", "Schedule post"),
    "x_delete_post": ("Delete a post from X — can't be undone", "Delete post"),
    "x_cancel_scheduled_post": ("Cancel a scheduled X post", "Cancel scheduled post"),
}


def public_action(*tools: str) -> tuple[str, str] | None:
    """The header and button of the first public action among *tools*."""
    for tool in tools:
        found = PUBLIC_ACTIONS.get(str(tool or "").strip())
        if found:
            return found
    return None
