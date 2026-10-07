"""Off-loop subprocess execution for native skill tools.

Why this module exists
----------------------
Native skill tools are ``async def`` and run on the **same event loop that
serves every SSE chat stream, every WebSocket, ``/health``, and the HITL
approval endpoint**. A bare ``subprocess.run`` inside one of them does not
yield: it pins the loop for the full duration of the child process.

The 2026-09-16 audit found twenty such calls across three skill modules,
with timeouts up to ninety seconds:

    install_python_packages / install_npm_packages   90s
    run_unit_tests                                   60s
    git push / git pull                              30s
    lint_code / format_code                          30s

Ninety seconds is not a hiccup. During it the server answers nothing — and
because ``main()`` pins ``ws_ping_interval=20.0, ws_ping_timeout=20.0``, the
protocol pings that are the server-side death certificate for black-holed
sockets cannot fire either, so live sockets get culled mid-turn by a
``pip install``. That defeats the Turn Delivery V2 ping design from inside.

``kazma_core.agent.tool_builtins.system.shell_exec`` already had this right
(``await asyncio.to_thread(...)``); the skills layer never adopted it. This
module is that pattern, in one place, so the static gate can recognise it and
new skill tools inherit it for free.

Usage::

    from kazma_skills.native._subprocess import run_off_loop

    res = await run_off_loop(cmd, cwd=cwd, capture_output=True,
                             text=True, timeout=30)

``run_off_loop`` accepts the argument-list calls used by native skills,
returns ``CompletedProcess`` and raises ``subprocess.TimeoutExpired`` on timeout.
It also enforces process-tree cleanup and combined output and memory budgets.
"""

from __future__ import annotations

import subprocess
from typing import Any

__all__ = ["run_off_loop"]


async def run_off_loop(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[Any]:
    """Run native skills off-loop under the shared process budgets.

    Timeout defaults to 90 seconds and is capped at 300. Excess output raises
    OutputLimitExceeded rather than returning success with missing bytes.
    Cancellation signals the worker to terminate its process tree.

    The child's environment defaults to the server's WITHOUT its secrets
    (:func:`kazma_core.security.child_env.tool_child_env`): pytest, pip and
    npm installs and git all run code nobody reviewed (a repository's
    conftest, an install script, a hook), and they used to inherit the vault
    key and the database password. A caller that passes ``env=`` builds it
    from ``tool_child_env`` too; ``tests/test_child_env.py`` holds both.
    """
    if kwargs.get("env") is None:
        from kazma_core.security.child_env import tool_child_env

        kwargs["env"] = tool_child_env()
    from kazma_core.security.process_budget import run_bounded_async

    kwargs["timeout"] = kwargs.get("timeout") or 90
    return await run_bounded_async(*args, **kwargs)
