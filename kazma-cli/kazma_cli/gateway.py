"""Gateway CLI commands — manage the Kazma omnichannel message-bus gateway.

Talks to the running WebUI server's REST API (``/api/gateway/*``) using
httpx.  The server is expected to be reachable at ``http://localhost:9090``
by default; override with ``--port`` or the ``KAZMA_PORT`` env var.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from typing import Any

import httpx
from rich.console import Console
from rich.table import Table

logger = logging.getLogger(__name__)

console = Console()

DEFAULT_TIMEOUT = 10.0
DEFAULT_PORT = 9090

__all__ = [
    "DEFAULT_PORT",
    "DEFAULT_TIMEOUT",
    "ServerNotRunningError",
    "cmd_refresh",
    "cmd_restart",
    "cmd_start",
    "cmd_status",
    "cmd_stop",
    "extract_port",
    "print_help",
    "resolve_base_url",
    "resolve_port",
    "run",
]


# ---------------------------------------------------------------------------
# URL / arg helpers
# ---------------------------------------------------------------------------

def resolve_port(port: int | None = None) -> int:
    """Resolve the server port from *port*, then ``KAZMA_PORT`` env, then default."""
    if port is not None:
        return port
    env_port = os.environ.get("KAZMA_PORT")
    if env_port:
        try:
            return int(env_port)
        except ValueError:
            logger.warning("Invalid KAZMA_PORT value '%s', using default %d", env_port, DEFAULT_PORT)
    return DEFAULT_PORT


def resolve_base_url(port: int | None = None) -> str:
    """Return the base URL for the Kazma server."""
    return f"http://localhost:{resolve_port(port)}"


def extract_port(args: list[str]) -> tuple[int | None, list[str]]:
    """Pull a ``--port N`` flag out of *args*, returning (port, remaining_args)."""
    port: int | None = None
    remaining: list[str] = []
    i = 0
    while i < len(args):
        if args[i] == "--port" and i + 1 < len(args):
            try:
                port = int(args[i + 1])
            except ValueError:
                console.print(f"[red]Invalid --port value: {args[i + 1]}[/red]")
                sys.exit(1)
            i += 2
            continue
        remaining.append(args[i])
        i += 1
    return port, remaining


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

class ServerNotRunningError(Exception):
    """Raised when the Kazma server cannot be reached."""


async def _request(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    **kwargs: Any,
) -> dict[str, Any]:
    """Perform an async HTTP request and return the JSON body.

    Raises ``ServerNotRunningError`` on connection failures.
    """
    try:
        response = await client.request(method, path, **kwargs)
    except httpx.ConnectError as exc:
        raise ServerNotRunningError(str(exc)) from exc
    except httpx.TimeoutException as exc:
        raise ServerNotRunningError(str(exc)) from exc

    try:
        return response.json()
    except ValueError:
        return {"status": "error", "message": response.text}


# ---------------------------------------------------------------------------
# Command implementations
# ---------------------------------------------------------------------------

async def cmd_status(base_url: str) -> int:
    """GET /api/gateway/status and render an adapter table."""
    async with httpx.AsyncClient(base_url=base_url, timeout=DEFAULT_TIMEOUT) as client:
        try:
            data = await _request(client, "GET", "/api/gateway/status")
        except ServerNotRunningError:
            _server_not_running()
            return 1

    adapters = data.get("adapters", []) or []
    persistence = data.get("persistence", {}) or {}
    threads = data.get("threads", []) or []

    table = Table(title="Gateway Status")
    table.add_column("Platform", style="cyan")
    table.add_column("Status", style="green")
    table.add_column("Uptime (s)", justify="right")

    if adapters:
        for adapter in adapters:
            table.add_row(
                str(adapter.get("platform", "?")),
                str(adapter.get("status", "?")),
                str(adapter.get("uptime_seconds", "?")),
            )
    else:
        table.add_row("(none)", "—", "—")

    console.print(table)

    active_threads = persistence.get("active_threads", len(threads))
    console.print(
        f"\nActive threads: [bold]{active_threads}[/bold]   "
        f"Session store: {persistence.get('session_store', 'n/a')}"
    )
    return 0


async def cmd_start(base_url: str) -> int:
    """POST /api/gateway/start."""
    async with httpx.AsyncClient(base_url=base_url, timeout=DEFAULT_TIMEOUT) as client:
        try:
            data = await _request(client, "POST", "/api/gateway/start")
        except ServerNotRunningError:
            _server_not_running()
            return 1

    if data.get("status") == "started":
        console.print("[green]Gateway started.[/green]")
    else:
        console.print(f"[yellow]Gateway start response:[/yellow] {data}")
    return 0


async def cmd_stop(base_url: str) -> int:
    """POST /api/gateway/stop."""
    async with httpx.AsyncClient(base_url=base_url, timeout=DEFAULT_TIMEOUT) as client:
        try:
            data = await _request(client, "POST", "/api/gateway/stop")
        except ServerNotRunningError:
            _server_not_running()
            return 1

    if data.get("status") == "stopped":
        console.print("[green]Gateway stopped.[/green]")
    else:
        console.print(f"[yellow]Gateway stop response:[/yellow] {data}")
    return 0


async def cmd_restart(base_url: str) -> int:
    """POST stop then POST start."""
    async with httpx.AsyncClient(base_url=base_url, timeout=DEFAULT_TIMEOUT) as client:
        try:
            await _request(client, "POST", "/api/gateway/stop")
        except ServerNotRunningError:
            _server_not_running()
            return 1
        # Brief pause so the stop settles before start.
        await asyncio.sleep(0.5)
        try:
            data = await _request(client, "POST", "/api/gateway/start")
        except ServerNotRunningError:
            console.print("[red]Server went away during restart.[/red]")
            return 1

    if data.get("status") == "started":
        console.print("[green]Gateway restarted.[/green]")
    else:
        console.print(f"[yellow]Gateway restart response:[/yellow] {data}")
    return 0


async def cmd_refresh(base_url: str) -> int:
    """POST /api/gateway/refresh-adapters."""
    async with httpx.AsyncClient(base_url=base_url, timeout=DEFAULT_TIMEOUT) as client:
        try:
            data = await _request(client, "POST", "/api/gateway/refresh-adapters")
        except ServerNotRunningError:
            _server_not_running()
            return 1

    console.print("[green]Adapters refreshed.[/green]")
    if data.get("status") and data.get("status") != "ok":
        console.print(f"[yellow]Response:[/yellow] {data}")
    return 0


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

def _server_not_running() -> None:
    """Print the canonical 'server not running' message."""
    console.print("[red]Server not running.[/red] Start with: [cyan]kazma serve[/cyan]")


def print_help() -> None:
    """Print gateway subcommand help."""
    console.print("Kazma Gateway — omnichannel message-bus control")
    console.print()
    console.print("Usage: kazma gateway <command> [--port N]")
    console.print()
    console.print("Commands:")
    console.print("  status    Show gateway & adapter status")
    console.print("  start     Start the gateway and all adapters")
    console.print("  stop      Stop the gateway and all adapters")
    console.print("  restart   Stop then start the gateway")
    console.print("  refresh   Hot-reload adapters from config")
    console.print()
    console.print("Options:")
    console.print("  --port N  Server port (default: 9090, or KAZMA_PORT env var)")


# ---------------------------------------------------------------------------
# Sync entry point
# ---------------------------------------------------------------------------

def run(args: list[str]) -> None:
    """Dispatch gateway subcommands (sync bridge over async handlers)."""
    port, remaining = extract_port(args)

    if not remaining:
        print_help()
        return

    subcmd = remaining[0]
    base_url = resolve_base_url(port)

    handlers: dict[str, Any] = {
        "status": cmd_status,
        "start": cmd_start,
        "stop": cmd_stop,
        "restart": cmd_restart,
        "refresh": cmd_refresh,
    }

    if subcmd in ("--help", "-h", "help"):
        print_help()
        return

    handler = handlers.get(subcmd)
    if handler is None:
        console.print(f"[red]Unknown gateway command:[/red] {subcmd}")
        print_help()
        sys.exit(1)

    rc = asyncio.run(handler(base_url))
    if rc:
        sys.exit(rc)
