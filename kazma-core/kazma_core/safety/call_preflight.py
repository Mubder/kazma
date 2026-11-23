"""Calls that cannot run are refused before their approval card.

A danger tool's card asks the operator to approve a call; asking for one the
policy or the host will refuse anyway wastes the approval and then fails the
turn. Live 2026-10-18, twice on Telegram: an approved ``shell_exec`` the
host-shell policy refused (``safety/shell_preflight``), then an approved
``browser_navigate`` this host cannot launch (strict browser network mode on
Windows, ``web_acquire.browser_isolation.launch_refusal``).

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

__all__ = ["ALWAYS_ASK_TOOLS", "BROWSER_TOOLS", "PREFLIGHT_TOOLS", "call_refusal", "browser_refusal"]


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
    return None


#: The always-ask actions and folder grants: the cards the admin answers for
#: a person other than the admin who holds Development, in a shared space
#: with a project folder (N21, ``spaces.approvals``).
ALWAYS_ASK_TOOLS = frozenset({*ALWAYS_HITL_TOOLS, "request_path_access"})

#: Every tool ``call_refusal`` answers for.
PREFLIGHT_TOOLS = frozenset({"shell_exec", *BROWSER_TOOLS, *INSTALL_PAID_TOOLS})
