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
"""

from __future__ import annotations

import asyncio
from typing import Any

#: The tools that start the native browser on their first call
#: (``computer_use`` drives the same page).
BROWSER_TOOLS = frozenset({
    "browser_navigate", "browser_click", "browser_extract_text",
    "browser_screenshot", "browser_fill_form", "browser_eval_js",
    "computer_use",
})

__all__ = ["BROWSER_TOOLS", "PREFLIGHT_TOOLS", "call_refusal", "browser_refusal"]


async def browser_refusal() -> str | None:
    """Why the browser would be refused on this host, or None (off the loop)."""
    from kazma_core.web_acquire.browser_isolation import launch_refusal

    return await asyncio.to_thread(launch_refusal)


async def call_refusal(name: str, arguments: Any) -> str | None:
    """Why this call would be refused before it acts, or None."""
    args = arguments if isinstance(arguments, dict) else {}
    if name == "shell_exec":
        from kazma_core.safety.shell_preflight import shell_refusal

        return await shell_refusal(str(args.get("command") or ""))
    if name in BROWSER_TOOLS:
        return await browser_refusal()
    return None


#: Every tool ``call_refusal`` answers for.
PREFLIGHT_TOOLS = frozenset({"shell_exec", *BROWSER_TOOLS})
