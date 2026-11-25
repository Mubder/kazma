"""Calls that cannot run are refused before their approval card.

A danger tool's card asks the operator to approve a call; asking for one the
policy or the host will refuse anyway wastes the approval and then fails the
turn. Live 2026-10-18, twice on Telegram: an approved ``shell_exec`` the
host-shell policy refused (``safety/shell_preflight``), then an approved
``browser_navigate`` this host cannot launch (strict browser network mode on
Windows, ``web_acquire.browser_isolation.launch_refusal``). And 2026-11-25:
an approved ``x_post`` while publishing to X was paused
(``x_api.policy.publishing_refusal``).

``call_refusal`` is the one question both gates ask before a card: the
graph's (``graph_tool_worker._refuse_unrunnable_calls``) and the swarm bus's
(``LocalToolRegistry.execute``). The tools ask the same question themselves
and raise ``RefusedBeforeEffect``, so a direct call is refused the same way.

An always-ask action or a folder grant (:data:`ALWAYS_ASK_TOOLS`) is not
refused here: its card is made, and the one rule of who answers it decides
(``spaces.approvals``; for a person other than the admin who holds
Development in a shared space with a project folder, the admin answers it,
decision N21). Until S2-05 such a call was refused here.
"""

from __future__ import annotations

import asyncio
from typing import Any

from kazma_core.safety.hitl import ALWAYS_HITL_TOOLS
from kazma_core.spaces.install_paid import INSTALL_PAID_TOOLS

#: The tools that start the native browser on their first call
#: (``computer_use`` drives the same page).
BROWSER_TOOLS = frozenset({
    "browser_navigate", "browser_click", "browser_extract_text",
    "browser_screenshot", "browser_fill_form", "browser_eval_js",
    "computer_use",
})

#: The tools that publish to X or delete from it, and the booking of a post.
X_PUBLISH_TOOLS = frozenset({"x_post", "x_delete_post", "x_schedule_post"})

__all__ = ["ALWAYS_ASK_TOOLS", "BROWSER_TOOLS", "PREFLIGHT_TOOLS", "X_PUBLISH_TOOLS", "call_refusal",
           "browser_refusal"]


async def browser_refusal() -> str | None:
    """Why the browser would be refused on this host, or None (off the loop)."""
    from kazma_core.web_acquire.browser_isolation import launch_refusal

    return await asyncio.to_thread(launch_refusal)


async def call_refusal(name: str, arguments: Any, *, user_text: str | None = None) -> str | None:
    """Why this call would be refused before it acts, or None. *user_text*:
    the person's latest message: a call that would pay an API with the
    install's key in a turn that runs on the person's own key only
    (``spaces.install_paid``) is refused in their language."""
    args = arguments if isinstance(arguments, dict) else {}
    if name in INSTALL_PAID_TOOLS:
        from kazma_core.spaces.install_paid import own_key_refusal

        return own_key_refusal(name, args, user_text)
    if name == "shell_exec":
        from kazma_core.safety.shell_paths import shell_target_refusal
        from kazma_core.safety.shell_preflight import shell_refusal

        command = str(args.get("command") or "")
        # The policy and the command's text, then what its values and
        # folders resolve to (Kazma's own files, the workspace boundary,
        # options that start a program): the tool refuses each of these.
        return await shell_refusal(command) or await shell_target_refusal(command)
    if name in BROWSER_TOOLS:
        return await browser_refusal()
    if name in OUTBOUND_CHECKED:
        return await asyncio.to_thread(_outbound_refusal, name, args)
    if name in X_PUBLISH_TOOLS:
        from kazma_core.x_api.policy import publishing_refusal

        return await asyncio.to_thread(publishing_refusal)
    return None


#: The tools that name a recipient (``side_effects.OUTBOUND_TARGET_FIELDS``)
#: whose send is not Kazma's chat-app bot's, and why: never checked by
#: :func:`_outbound_refusal` (the stage 3 review, M7).
OUTBOUND_NOT_CHECKED = {
    "email_send": "the space's own connected mailbox sends it (S1-17a), not Kazma's bot: "
                  "whom a person mails is their mailbox's",
    "send_file": "names no recipient: it goes to this conversation",
}


def _outbound_checked() -> frozenset[str]:
    from kazma_core.safety.side_effects import OUTBOUND_TARGET_FIELDS

    return frozenset(name for name in OUTBOUND_TARGET_FIELDS if name not in OUTBOUND_NOT_CHECKED)


#: The tools whose chat-app recipient :func:`_outbound_refusal` checks.
OUTBOUND_CHECKED = _outbound_checked()


def _outbound_refusal(name: str, args: dict[str, Any]) -> str | None:
    """Why a message *name* would send to a chat-app recipient is refused, or
    None. Once Spaces is enforced, a person other than the admin sends only
    to their own address (``chat_links.is_their_address``: one of their
    links, or a group bound to the space they are a member of) or this
    conversation; work that names no person sends to no one named. The
    admin keeps today's rule. Before Spaces nothing is asked (the stage 3
    review, M7: a member approved their own send_message to anyone and
    Kazma's bot messaged them). Blocking (the identity store)."""
    from kazma_core.spaces.scope import current_scope, spaces_enforced

    if not spaces_enforced():
        return None
    from kazma_core.safety.side_effects import OUTBOUND_TARGET_FIELDS

    channel = str(args.get("channel") or args.get("backend") or "telegram").strip().lower()
    targets = []
    for field in OUTBOUND_TARGET_FIELDS.get(name, ()):
        raw = str(args.get(field) or "").strip()
        if raw:
            targets.append(raw if ":" in raw else f"{channel}:{raw}")
    if not targets:
        return None
    bound = current_scope()
    person = bound.asker if bound is not None else ""
    if not person:
        return ("Spaces is on and this work names no person: Kazma's chat apps send it to no one "
                "named. Nothing was sent.")
    from kazma_core.spaces import chat_links, identity

    try:
        if person in identity.admin_accounts(identity.read_registry()):
            return None
        from kazma_core.tools.send_message import get_current_delivery_target

        here = str(get_current_delivery_target() or "").strip()
        for target in targets:
            if target == here or chat_links.is_their_address(person, target, bound.space_id):
                continue
            return (f"Kazma's chat apps send a person's messages only to their own linked chat or "
                    f"this conversation, never to {target}. Nothing was sent.")
    except identity.IdentityUnavailable:
        return "Whose chat that is cannot be read right now. Nothing was sent; try again."
    return None


#: The always-ask actions and folder grants: the cards the admin answers for
#: a person other than the admin who holds Development, in a shared space
#: with a project folder (N21, ``spaces.approvals``).
ALWAYS_ASK_TOOLS = frozenset({*ALWAYS_HITL_TOOLS, "request_path_access"})

#: Every tool ``call_refusal`` answers for.
PREFLIGHT_TOOLS = frozenset({
    "shell_exec", *BROWSER_TOOLS, *INSTALL_PAID_TOOLS, *OUTBOUND_CHECKED, *X_PUBLISH_TOOLS,
})
