"""Swarm CLI commands — manage multi-worker AI agent orchestration.

Talks to the running WebUI server's REST API (``/api/swarm/*``) using
httpx.  The server is expected at ``http://localhost:9090`` by default;
override with ``--port`` or the ``KAZMA_PORT`` env var.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from typing import Any

import httpx
from rich.console import Console
from rich.table import Table

from kazma_cli.gateway import (
    DEFAULT_TIMEOUT,
    ServerNotRunningError,
    _request,
    extract_port,
    resolve_base_url,
)

logger = logging.getLogger(__name__)

console = Console()

__all__ = [
    "cmd_approve",
    "cmd_broadcast",
    "cmd_circuit_breaker",
    "cmd_consult",
    "cmd_dispatch",
    "cmd_fanout",
    "cmd_history",
    "cmd_metrics",
    "cmd_pipeline",
    "cmd_reject",
    "cmd_start",
    "cmd_stop",
    "cmd_status",
    "cmd_task",
    "cmd_workers",
    "cmd_worker_add",
    "cmd_worker_remove",
    "cmd_worker_spawn",
    "parse_flags",
    "print_help",
    "run",
]


# ---------------------------------------------------------------------------
# Flag parsing
# ---------------------------------------------------------------------------

# Flags that consume the following argument as their value.
_VALUE_FLAGS = {
    "--model",
    "--provider",
    "--type",
    "--role",
    "--context",
    "--workers",
    "--aggregation",
    "--page",
    "--page-size",
    "--status",
    "--port",
    "--worker",
}

# Flags that are boolean toggles (no value).
_BOOL_FLAGS = {"--reset"}


def parse_flags(args: list[str]) -> tuple[list[str], dict[str, str], set[str]]:
    """Split *args* into (positionals, value_flags, bool_flags).

    Unknown ``--foo`` flags are collected into *value_flags* with an empty
    string value so they are not silently dropped.
    """
    positionals: list[str] = []
    values: dict[str, str] = {}
    bools: set[str] = set()
    i = 0
    while i < len(args):
        token = args[i]
        if token in _BOOL_FLAGS:
            bools.add(token)
            i += 1
            continue
        if token in _VALUE_FLAGS and i + 1 < len(args):
            values[token] = args[i + 1]
            i += 2
            continue
        if token.startswith("--") and "=" in token:
            key, _, val = token.partition("=")
            values[key] = val
            i += 1
            continue
        if token.startswith("--"):
            # Unknown flag with no value: record as bool to avoid dropping.
            bools.add(token)
            i += 1
            continue
        positionals.append(token)
        i += 1
    return positionals, values, bools


def _split_workers(raw: str) -> list[str]:
    """Split a comma-separated worker list into trimmed names."""
    return [name.strip() for name in raw.split(",") if name.strip()]


# ---------------------------------------------------------------------------
# Registry-aware defaults
# ---------------------------------------------------------------------------

def _default_model() -> str:
    """Return the active model from ModelRegistry, or a hardcoded fallback."""
    try:
        from kazma_core.model_registry import get_model_registry

        profile = get_model_registry().get_active_profile()
        return profile.get("model", "deepseek-chat")
    except (RuntimeError, ImportError):
        return "deepseek-chat"


def _default_provider() -> str:
    """Return the active provider from ModelRegistry, or a hardcoded fallback."""
    try:
        from kazma_core.model_registry import get_model_registry

        profile = get_model_registry().get_active_profile()
        return profile.get("provider", "deepseek")
    except (RuntimeError, ImportError):
        return "deepseek"


# ---------------------------------------------------------------------------
# Command implementations
# ---------------------------------------------------------------------------

async def cmd_status(base_url: str) -> int:
    """GET /api/swarm/status and render a worker table."""
    async with httpx.AsyncClient(base_url=base_url, timeout=DEFAULT_TIMEOUT) as client:
        try:
            data = await _request(client, "GET", "/api/swarm/status")
        except ServerNotRunningError:
            _server_not_running()
            return 1

    workers = data.get("workers", []) or []
    _print_worker_table(workers, title="Swarm Status")
    console.print(
        f"\nWorkers: [bold]{data.get('count', len(workers))}[/bold]   "
        f"Started: {data.get('started', False)}"
    )
    return 0


async def cmd_workers(base_url: str) -> int:
    """GET /api/swarm/status and list workers."""
    async with httpx.AsyncClient(base_url=base_url, timeout=DEFAULT_TIMEOUT) as client:
        try:
            data = await _request(client, "GET", "/api/swarm/status")
        except ServerNotRunningError:
            _server_not_running()
            return 1

    workers = data.get("workers", []) or []
    _print_worker_table(workers, title="Swarm Workers")
    return 0


async def cmd_worker_add(base_url: str, positionals: list[str], flags: dict[str, str]) -> int:
    """POST /api/swarm/workers."""
    if not positionals:
        console.print("[red]Usage:[/red] kazma swarm worker add <name> [--model M] [--provider P] [--type T] [--role R]")
        return 1
    name = positionals[0]
    payload: dict[str, Any] = {
        "name": name,
        "model": flags.get("--model", _default_model()),
        "provider": flags.get("--provider", _default_provider()),
        "type": flags.get("--type", "in-process"),
        "role": flags.get("--role", ""),
    }
    async with httpx.AsyncClient(base_url=base_url, timeout=DEFAULT_TIMEOUT) as client:
        try:
            data = await _request(client, "POST", "/api/swarm/workers", json=payload)
        except ServerNotRunningError:
            _server_not_running()
            return 1

    if data.get("status") == "ok":
        worker = data.get("worker", {})
        console.print(f"[green]Worker added:[/green] {worker.get('name', name)}")
    else:
        console.print(f"[red]Failed to add worker:[/red] {data.get('message', data)}")
        return 1
    return 0


async def cmd_worker_spawn(base_url: str, positionals: list[str], flags: dict[str, str]) -> int:
    """POST /api/swarm/workers/spawn."""
    if len(positionals) < 2:
        console.print("[red]Usage:[/red] kazma swarm worker spawn <name> <role> [--model M] [--provider P] [--type T]")
        return 1
    name, role = positionals[0], positionals[1]
    payload: dict[str, Any] = {
        "name": name,
        "role": role,
        "capabilities": {"role": role},
        "model": flags.get("--model", _default_model()),
        "provider": flags.get("--provider", _default_provider()),
        "worker_type": flags.get("--type", "in_process"),
    }
    async with httpx.AsyncClient(base_url=base_url, timeout=DEFAULT_TIMEOUT) as client:
        try:
            data = await _request(client, "POST", "/api/swarm/workers/spawn", json=payload)
        except ServerNotRunningError:
            _server_not_running()
            return 1

    if data.get("status") == "ok":
        worker = data.get("worker", {})
        console.print(f"[green]Worker spawned:[/green] {worker.get('name', name)} ({role})")
    else:
        console.print(f"[red]Failed to spawn worker:[/red] {data.get('message', data)}")
        return 1
    return 0


async def cmd_worker_remove(base_url: str, positionals: list[str]) -> int:
    """DELETE /api/swarm/workers/{name}."""
    if not positionals:
        console.print("[red]Usage:[/red] kazma swarm worker remove <name>")
        return 1
    name = positionals[0]
    async with httpx.AsyncClient(base_url=base_url, timeout=DEFAULT_TIMEOUT) as client:
        try:
            data = await _request(client, "DELETE", f"/api/swarm/workers/{name}")
        except ServerNotRunningError:
            _server_not_running()
            return 1

    if data.get("status") == "ok":
        console.print(f"[green]Worker removed:[/green] {name}")
    else:
        console.print(f"[red]Failed to remove worker:[/red] {data.get('message', data)}")
        return 1
    return 0


async def cmd_dispatch(base_url: str, positionals: list[str], flags: dict[str, str]) -> int:
    """POST /api/swarm/dispatch with type=dispatch."""
    if len(positionals) < 2:
        console.print("[red]Usage:[/red] kazma swarm dispatch <worker> <prompt> [--context C]")
        return 1
    worker, prompt = positionals[0], positionals[1]
    return await _do_dispatch(
        base_url,
        workers=[worker],
        prompt=prompt,
        context=flags.get("--context", ""),
        task_type="dispatch",
    )


async def cmd_broadcast(base_url: str, positionals: list[str], flags: dict[str, str]) -> int:
    """POST /api/swarm/dispatch with type=broadcast, workers=all."""
    if not positionals:
        console.print("[red]Usage:[/red] kazma swarm broadcast <prompt> [--context C]")
        return 1
    prompt = positionals[0]
    return await _do_dispatch(
        base_url,
        workers=["all"],
        prompt=prompt,
        context=flags.get("--context", ""),
        task_type="broadcast",
    )


async def cmd_consult(base_url: str, positionals: list[str], flags: dict[str, str]) -> int:
    """POST /api/swarm/dispatch with type=consult."""
    workers_raw = flags.get("--workers", "")
    if not workers_raw:
        console.print("[red]Usage:[/red] kazma swarm consult <prompt> --workers a,b [--context C]")
        return 1
    if not positionals:
        console.print("[red]Usage:[/red] kazma swarm consult <prompt> --workers a,b [--context C]")
        return 1
    prompt = positionals[0]
    return await _do_dispatch(
        base_url,
        workers=_split_workers(workers_raw),
        prompt=prompt,
        context=flags.get("--context", ""),
        task_type="consult",
    )


async def cmd_pipeline(base_url: str, positionals: list[str], flags: dict[str, str]) -> int:
    """POST /api/swarm/dispatch with type=pipeline."""
    workers_raw = flags.get("--workers", "")
    if not workers_raw or not positionals:
        console.print("[red]Usage:[/red] kazma swarm pipeline --workers a,b,c <prompt>")
        return 1
    prompt = positionals[0]
    return await _do_dispatch(
        base_url,
        workers=_split_workers(workers_raw),
        prompt=prompt,
        context=flags.get("--context", ""),
        task_type="pipeline",
    )


async def cmd_fanout(base_url: str, positionals: list[str], flags: dict[str, str]) -> int:
    """POST /api/swarm/dispatch with type=fan_out."""
    workers_raw = flags.get("--workers", "")
    if not workers_raw or not positionals:
        console.print("[red]Usage:[/red] kazma swarm fanout --workers a,b <prompt> [--aggregation strategy]")
        return 1
    prompt = positionals[0]
    return await _do_dispatch(
        base_url,
        workers=_split_workers(workers_raw),
        prompt=prompt,
        context=flags.get("--context", ""),
        task_type="fan_out",
        aggregation=flags.get("--aggregation", "collect"),
    )


async def _do_dispatch(
    base_url: str,
    workers: list[str],
    prompt: str,
    context: str,
    task_type: str,
    aggregation: str | None = None,
) -> int:
    """Shared dispatch helper that POSTs to /api/swarm/dispatch."""
    payload: dict[str, Any] = {
        "workers": workers,
        "task": prompt,
        "context": context,
        "type": task_type,
    }
    if aggregation is not None:
        payload["aggregation"] = aggregation

    async with httpx.AsyncClient(base_url=base_url, timeout=DEFAULT_TIMEOUT) as client:
        try:
            data = await _request(client, "POST", "/api/swarm/dispatch", json=payload)
        except ServerNotRunningError:
            _server_not_running()
            return 1

    status = data.get("status")
    if status == "ok":
        console.print(f"[green]Task dispatched[/green] to {', '.join(data.get('dispatched', workers))}")
        task_id = data.get("task_id")
        if task_id:
            console.print(f"Task ID: [cyan]{task_id}[/cyan]")
        results = data.get("results", []) or []
        for result in results:
            _print_worker_result(result)
        aggregated = data.get("aggregated_output")
        if aggregated:
            console.print(f"\n[bold]Aggregated output:[/bold]\n{aggregated}")
    elif status == "warning":
        console.print(f"[yellow]Warning:[/yellow] {data.get('message', '')}")
        missing = data.get("missing", []) or []
        if missing:
            console.print(f"Missing workers: {', '.join(missing)}")
    else:
        console.print(f"[red]Dispatch failed:[/red] {data.get('message', data)}")
        return 1
    return 0


async def cmd_history(base_url: str, flags: dict[str, str]) -> int:
    """GET /api/swarm/tasks with optional filters and pagination."""
    params: dict[str, Any] = {}
    if flags.get("--type"):
        params["type"] = flags["--type"]
    if flags.get("--status"):
        params["status"] = flags["--status"]
    if flags.get("--page"):
        params["page"] = flags["--page"]
    if flags.get("--page-size"):
        params["pageSize"] = flags["--page-size"]

    async with httpx.AsyncClient(base_url=base_url, timeout=DEFAULT_TIMEOUT) as client:
        try:
            data = await _request(client, "GET", "/api/swarm/tasks", params=params)
        except ServerNotRunningError:
            _server_not_running()
            return 1

    tasks = data.get("tasks", []) or []
    if not tasks:
        console.print("[yellow]No tasks found.[/yellow]")
        return 0

    table = Table(title="Swarm Task History")
    table.add_column("ID", style="cyan", overflow="fold")
    table.add_column("Type", style="magenta")
    table.add_column("Status", style="green")
    table.add_column("Workers", overflow="fold")
    table.add_column("Prompt", overflow="fold")

    for task in tasks:
        task_id = str(task.get("task_id", task.get("id", "?")))
        table.add_row(
            task_id,
            str(task.get("type", "?")),
            str(task.get("status", "?")),
            ", ".join(task.get("workers", []) or []),
            str(task.get("prompt", ""))[:60],
        )

    console.print(table)
    total = data.get("total", data.get("count", len(tasks)))
    page = data.get("page", flags.get("--page", "1"))
    console.print(f"\nTotal: [bold]{total}[/bold]   Page: {page}")
    return 0


async def cmd_task(base_url: str, positionals: list[str]) -> int:
    """GET /api/swarm/tasks/{id}."""
    if not positionals:
        console.print("[red]Usage:[/red] kazma swarm task <id>")
        return 1
    task_id = positionals[0]
    async with httpx.AsyncClient(base_url=base_url, timeout=DEFAULT_TIMEOUT) as client:
        try:
            data = await _request(client, "GET", f"/api/swarm/tasks/{task_id}")
        except ServerNotRunningError:
            _server_not_running()
            return 1

    task = data.get("task", data)
    if data.get("status") == "error":
        console.print(f"[red]Error:[/red] {data.get('message', 'task not found')}")
        return 1

    console.print(f"[bold]Task:[/bold] {task.get('task_id', task_id)}")
    console.print(f"Type:    {task.get('type', '?')}")
    console.print(f"Status:  {task.get('status', '?')}")
    console.print(f"Workers: {', '.join(task.get('workers', []) or [])}")
    console.print(f"Prompt:  {task.get('prompt', '')}")
    if task.get("aggregated_output"):
        console.print(f"\n[bold]Aggregated output:[/bold]\n{task['aggregated_output']}")
    results = task.get("worker_results", []) or []
    for result in results:
        _print_worker_result(result)
    return 0


async def cmd_metrics(base_url: str, flags: dict[str, str]) -> int:
    """GET worker or all-worker metrics."""
    worker = flags.get("--worker")
    async with httpx.AsyncClient(base_url=base_url, timeout=DEFAULT_TIMEOUT) as client:
        try:
            if worker:
                data = await _request(client, "GET", f"/api/swarm/workers/{worker}/metrics")
            else:
                data = await _request(client, "GET", "/api/swarm/workers/metrics/all")
        except ServerNotRunningError:
            _server_not_running()
            return 1

    metrics = data.get("metrics", []) or []
    if not metrics:
        label = f"worker '{worker}'" if worker else "all workers"
        console.print(f"[yellow]No metrics found for {label}.[/yellow]")
        return 0

    table = Table(title="Swarm Metrics")
    table.add_column("Worker", style="cyan")
    table.add_column("Date", style="magenta")
    table.add_column("Tasks", justify="right")
    table.add_column("Success", justify="right")
    table.add_column("Failed", justify="right")
    table.add_column("Avg (s)", justify="right")

    for metric in metrics:
        table.add_row(
            str(metric.get("worker", data.get("worker", "?"))),
            str(metric.get("date", metric.get("day", "?"))),
            str(metric.get("total", metric.get("tasks", "?"))),
            str(metric.get("success", metric.get("succeeded", "?"))),
            str(metric.get("failed", "?")),
            str(metric.get("avg_duration", metric.get("avg_duration_seconds", "?"))),
        )

    console.print(table)
    return 0


async def cmd_start(base_url: str) -> int:
    """POST /api/swarm/start."""
    async with httpx.AsyncClient(base_url=base_url, timeout=DEFAULT_TIMEOUT) as client:
        try:
            data = await _request(client, "POST", "/api/swarm/start")
        except ServerNotRunningError:
            _server_not_running()
            return 1

    if data.get("status") == "ok":
        console.print(f"[green]Swarm started.[/green] {data.get('message', '')}")
    else:
        console.print(f"[red]Failed to start swarm:[/red] {data.get('message', data)}")
        return 1
    return 0


async def cmd_stop(base_url: str) -> int:
    """POST /api/swarm/stop."""
    async with httpx.AsyncClient(base_url=base_url, timeout=DEFAULT_TIMEOUT) as client:
        try:
            data = await _request(client, "POST", "/api/swarm/stop")
        except ServerNotRunningError:
            _server_not_running()
            return 1

    if data.get("status") == "ok":
        console.print(f"[green]Swarm stopped.[/green] {data.get('message', '')}")
    else:
        console.print(f"[red]Failed to stop swarm:[/red] {data.get('message', data)}")
        return 1
    return 0


async def cmd_approve(base_url: str, positionals: list[str]) -> int:
    """POST /api/swarm/tasks/{id}/approve."""
    if not positionals:
        console.print("[red]Usage:[/red] kazma swarm approve <task_id>")
        return 1
    task_id = positionals[0]
    async with httpx.AsyncClient(base_url=base_url, timeout=DEFAULT_TIMEOUT) as client:
        try:
            data = await _request(client, "POST", f"/api/swarm/tasks/{task_id}/approve")
        except ServerNotRunningError:
            _server_not_running()
            return 1

    if data.get("status") in ("ok", "success", "completed"):
        console.print(f"[green]Checkpoint approved:[/green] {task_id}")
        console.print(data.get("message", ""))
    else:
        console.print(f"[red]Approve failed:[/red] {data.get('message', data)}")
        return 1
    return 0


async def cmd_reject(base_url: str, positionals: list[str]) -> int:
    """POST /api/swarm/tasks/{id}/reject."""
    if not positionals:
        console.print("[red]Usage:[/red] kazma swarm reject <task_id>")
        return 1
    task_id = positionals[0]
    async with httpx.AsyncClient(base_url=base_url, timeout=DEFAULT_TIMEOUT) as client:
        try:
            data = await _request(client, "POST", f"/api/swarm/tasks/{task_id}/reject")
        except ServerNotRunningError:
            _server_not_running()
            return 1

    if data.get("status") in ("ok", "rejected", "aborted"):
        console.print(f"[green]Checkpoint rejected:[/green] {task_id}")
        console.print(data.get("message", ""))
    else:
        console.print(f"[red]Reject failed:[/red] {data.get('message', data)}")
        return 1
    return 0


async def cmd_circuit_breaker(
    base_url: str, positionals: list[str], bools: set[str]
) -> int:
    """GET/POST circuit-breaker status.

    - no worker           → GET /api/swarm/circuit-breakers (all)
    - worker, no --reset  → GET /api/swarm/workers/{name}/circuit-breaker
    - worker, --reset     → POST /api/swarm/workers/{name}/circuit-breaker/reset
    """
    worker = positionals[0] if positionals else None
    reset = "--reset" in bools

    async with httpx.AsyncClient(base_url=base_url, timeout=DEFAULT_TIMEOUT) as client:
        try:
            if worker is None:
                data = await _request(client, "GET", "/api/swarm/circuit-breakers")
            elif reset:
                data = await _request(
                    client, "POST", f"/api/swarm/workers/{worker}/circuit-breaker/reset"
                )
            else:
                data = await _request(
                    client, "GET", f"/api/swarm/workers/{worker}/circuit-breaker"
                )
        except ServerNotRunningError:
            _server_not_running()
            return 1

    if data.get("status") == "error":
        console.print(f"[red]Error:[/red] {data.get('message', data)}")
        return 1

    if worker is None:
        breakers = data.get("breakers", {}) or {}
        if not breakers:
            console.print("[yellow]No circuit-breaker data available.[/yellow]")
            return 0
        table = Table(title="Circuit Breakers")
        table.add_column("Worker", style="cyan")
        table.add_column("State", style="magenta")
        table.add_column("Failures", justify="right")
        for name, info in breakers.items():
            if isinstance(info, dict):
                table.add_row(
                    str(name),
                    str(info.get("state", "?")),
                    str(info.get("failure_count", info.get("failures", "?"))),
                )
            else:
                table.add_row(str(name), str(info), "—")
        console.print(table)
    else:
        info = data.get("circuit_breaker", data)
        if reset:
            console.print(f"[green]Circuit breaker reset for[/green] {worker}")
        console.print(f"Worker: {worker}")
        if isinstance(info, dict):
            console.print(f"State:    {info.get('state', '?')}")
            console.print(f"Failures: {info.get('failure_count', info.get('failures', '?'))}")
        else:
            console.print(f"State: {info}")
    return 0


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

def _server_not_running() -> None:
    """Print the canonical 'server not running' message."""
    console.print("[red]Server not running.[/red] Start with: [cyan]kazma serve[/cyan]")


def _print_worker_table(workers: list[dict[str, Any]], title: str) -> None:
    """Render a list of worker dicts as a rich table."""
    table = Table(title=title)
    table.add_column("Name", style="cyan")
    table.add_column("Model", style="magenta")
    table.add_column("Provider", style="blue")
    table.add_column("Type")
    table.add_column("Role")
    table.add_column("Status", style="green")

    if workers:
        for worker in workers:
            table.add_row(
                str(worker.get("name", "?")),
                str(worker.get("model", "?")),
                str(worker.get("provider", "?")),
                str(worker.get("type", "?")),
                str(worker.get("role", "")),
                str(worker.get("status", "?")),
            )
    else:
        table.add_row("(none)", "—", "—", "—", "—", "—")

    console.print(table)


def _print_worker_result(result: dict[str, Any]) -> None:
    """Print a single worker result dict from a dispatch response."""
    name = result.get("worker", "?")
    status = result.get("status", "?")
    output = result.get("output", "")
    error = result.get("error", "")
    console.print(f"\n[bold cyan]{name}[/bold cyan] [{status}]")
    if output:
        console.print(output)
    if error:
        console.print(f"[red]error:[/red] {error}")


def print_help() -> None:
    """Print swarm subcommand help."""
    console.print("Kazma Swarm — multi-worker AI agent orchestration")
    console.print()
    console.print("Usage: kazma swarm <command> [options] [--port N]")
    console.print()
    console.print("Commands:")
    console.print("  status            Show swarm & worker status")
    console.print("  workers           List registered workers")
    console.print("  worker add <name> [--model M --provider P --type T --role R]")
    console.print("                    Add a worker to the registry")
    console.print("  worker spawn <name> <role> [--model M --provider P --type T]")
    console.print("                    Dynamically spawn a worker")
    console.print("  worker remove <name>")
    console.print("                    Remove a worker")
    console.print("  dispatch <worker> <prompt> [--context C]")
    console.print("                    Dispatch a task to a single worker")
    console.print("  broadcast <prompt> [--context C]")
    console.print("                    Broadcast a task to all workers")
    console.print("  consult <prompt> --workers a,b [--context C]")
    console.print("                    Consult a subset of workers")
    console.print("  pipeline --workers a,b,c <prompt>")
    console.print("                    Run a pipeline across workers")
    console.print("  fanout --workers a,b <prompt> [--aggregation strategy]")
    console.print("                    Fan out a task to workers")
    console.print("  history [--type T --status S --page N --page-size N]")
    console.print("                    Show task history")
    console.print("  task <id>         Show details for a single task")
    console.print("  metrics [--worker W]")
    console.print("                    Show worker metrics")
    console.print("  start             Start all workers")
    console.print("  stop              Stop all workers")
    console.print("  approve <task_id> Approve an HITL checkpoint")
    console.print("  reject <task_id>  Reject an HITL checkpoint")
    console.print("  circuit-breaker [worker] [--reset]")
    console.print("                    Show or reset circuit breakers")
    console.print()
    console.print("Options:")
    console.print("  --port N          Server port (default: 9090, or KAZMA_PORT env var)")


# ---------------------------------------------------------------------------
# Sync entry point
# ---------------------------------------------------------------------------

def run(args: list[str]) -> None:
    """Dispatch swarm subcommands (sync bridge over async handlers)."""
    port, remaining = extract_port(args)

    if not remaining:
        print_help()
        return

    subcmd = remaining[0]
    rest = remaining[1:]
    base_url = resolve_base_url(port)

    if subcmd in ("--help", "-h", "help"):
        print_help()
        return

    if subcmd == "worker":
        _run_worker(base_url, rest)
        return

    positionals, flags, bools = parse_flags(rest)

    handlers: dict[str, Any] = {
        "status": cmd_status,
        "workers": cmd_workers,
        "dispatch": cmd_dispatch,
        "broadcast": cmd_broadcast,
        "consult": cmd_consult,
        "pipeline": cmd_pipeline,
        "fanout": cmd_fanout,
        "history": cmd_history,
        "task": cmd_task,
        "metrics": cmd_metrics,
        "start": cmd_start,
        "stop": cmd_stop,
        "approve": cmd_approve,
        "reject": cmd_reject,
        "circuit-breaker": cmd_circuit_breaker,
    }

    handler = handlers.get(subcmd)
    if handler is None:
        console.print(f"[red]Unknown swarm command:[/red] {subcmd}")
        print_help()
        sys.exit(1)

    # Dispatch with the signature each handler expects.
    if subcmd in {"dispatch", "broadcast", "consult", "pipeline", "fanout"}:
        rc = asyncio.run(handler(base_url, positionals, flags))
    elif subcmd in {"history", "metrics"}:
        rc = asyncio.run(handler(base_url, flags))
    elif subcmd in {"task", "approve", "reject"}:
        rc = asyncio.run(handler(base_url, positionals))
    elif subcmd == "circuit-breaker":
        rc = asyncio.run(handler(base_url, positionals, bools))
    else:
        rc = asyncio.run(handler(base_url))

    if rc:
        sys.exit(rc)


def _run_worker(base_url: str, rest: list[str]) -> None:
    """Dispatch the nested 'kazma swarm worker <sub>' commands."""
    if not rest:
        console.print("Usage: kazma swarm worker <add|spawn|remove> ...")
        return

    sub = rest[0]
    sub_rest = rest[1:]

    if sub in ("--help", "-h", "help"):
        console.print("Usage: kazma swarm worker <add|spawn|remove> ...")
        return

    if sub == "add":
        positionals, flags, _bools = parse_flags(sub_rest)
        rc = asyncio.run(cmd_worker_add(base_url, positionals, flags))
    elif sub == "spawn":
        positionals, flags, _bools = parse_flags(sub_rest)
        rc = asyncio.run(cmd_worker_spawn(base_url, positionals, flags))
    elif sub == "remove":
        positionals, _flags, _bools = parse_flags(sub_rest)
        rc = asyncio.run(cmd_worker_remove(base_url, positionals))
    else:
        console.print(f"[red]Unknown worker command:[/red] {sub}")
        console.print("Available: add, spawn, remove")
        sys.exit(1)
        return  # pragma: no cover

    if rc:
        sys.exit(rc)
