"""Swarm panel — DataTable workers + RichLog tasks + Tree hierarchy."""

from __future__ import annotations

import logging
from datetime import datetime, UTC

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import DataTable, RichLog, Static, TabbedContent, TabPane, Tree

from kazma_tui.widgets.log_stream import LogStream
from kazma_tui.widgets.sparkline import Sparkline

__all__ = ["ActiveTasksLog", "SwarmPanel", "SwarmTasksTable", "WorkerTable", "WorkerTree"]

logger = logging.getLogger(__name__)

_HEARTBEAT_STALE_SECONDS = 60


def _live_json(path: str, timeout: float = 2.0) -> dict:
    """GET JSON from the running Kazma server (TUI is a mouth)."""
    from kazma_core.runtime.local_api import request_json

    try:
        data = request_json("GET", path, timeout=timeout)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


async def _live_json_async(path: str, timeout: float = 2.0) -> dict:
    """Async GET JSON from the running Kazma server (TUI is a mouth)."""
    from kazma_core.runtime.local_api import request_json_async

    try:
        data = await request_json_async("GET", path, timeout=timeout)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None


def _worker_is_online(worker) -> bool:
    """Treat worker as online if explicitly running OR its last heartbeat is recent.

    Some registration paths never call ``InProcessWorker.start()`` so
    ``_running`` stays False even though the worker is dispatching
    tasks; falling back to heartbeat recency avoids marking them as
    offline while they are still active.
    """
    if getattr(worker, "_running", False):
        return True
    last_heartbeat = _parse_iso(getattr(worker, "last_heartbeat", None))
    if last_heartbeat is None:
        return False
    delta = (datetime.now(UTC) - last_heartbeat).total_seconds()
    return 0 <= delta <= _HEARTBEAT_STALE_SECONDS


class WorkerTable(DataTable):
    """DataTable showing registered swarm workers."""

    DEFAULT_CSS = """
    WorkerTable {
        height: 1fr;
        background: $surface;
        border: tall $border;
        padding: 0 1;
    }
    """

    async def on_mount(self) -> None:
        self.cursor_type = "row"
        self.add_columns("Name", "Role", "Status", "Model")
        await self._refresh()

    async def _refresh(self) -> None:
        self.clear()
        try:
            data = await _live_json_async("/api/swarm/status")
            workers = data.get("workers") or []
            if not workers:
                self.add_row("(no workers on server)", "", "", "")
                return
            for w in workers:
                st = str(w.get("status") or "")
                online = st in {"online", "busy", "running"} or bool(w.get("_running"))
                mark = "● online" if online else (st or "○ offline")
                self.add_row(
                    str(w.get("name") or "?"),
                    str(w.get("role") or ""),
                    mark,
                    str(w.get("model") or "?"),
                )
        except Exception:
            self.add_row("(server unreachable)", "", "", "")

    async def on_show(self) -> None:
        await self._refresh()


class SwarmTasksTable(DataTable):
    """DataTable showing recent task history."""

    DEFAULT_CSS = """
    SwarmTasksTable {
        height: 1fr;
        background: $surface;
        border: tall $border;
        padding: 0 1;
    }
    """

    async def on_mount(self) -> None:
        self.cursor_type = "row"
        self.add_columns("Task ID", "Type", "Status", "Workers", "Duration")
        await self._refresh()

    async def _refresh(self) -> None:
        self.clear()
        try:
            data = await _live_json_async("/api/swarm/tasks?pageSize=20")
            tasks = data.get("tasks") or []
            for t in tasks:
                tid = str(t.get("id") or t.get("task_id") or "")
                workers = t.get("workers") or []
                if not isinstance(workers, list):
                    workers = []
                dur = t.get("duration_seconds")
                dur_s = f"{float(dur):.1f}s" if dur is not None else "—"
                self.add_row(
                    tid[:16],
                    str(t.get("type") or ""),
                    str(t.get("status") or ""),
                    ", ".join(str(w) for w in workers[:3]),
                    dur_s,
                )
        except Exception as exc:
            logger.debug("Task history refresh failed: %s", exc)

    @staticmethod
    def _task_duration(t: object) -> str:
        """Compute task duration from created_at/completed_at timestamps."""
        created = getattr(t, "created_at", None)
        completed = getattr(t, "completed_at", None)
        if created and completed:
            try:
                from datetime import datetime
                start = datetime.fromisoformat(created)
                end = datetime.fromisoformat(completed)
                secs = (end - start).total_seconds()
                return f"{secs:.1f}s"
            except Exception as exc:
                logger.debug("Task duration calc failed: %s", exc)
        return "—"

    def on_show(self) -> None:
        self._refresh()


class ActiveTasksLog(RichLog):
    """Log stream showing active/in-flight tasks."""

    DEFAULT_CSS = """
    ActiveTasksLog {
        height: 1fr;
        background: $surface;
        border: tall $border;
        padding: 1 1;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(markup=True, **kwargs)

    async def on_mount(self) -> None:
        self.write("[bold #22d3ee]Active Tasks[/]")
        await self._refresh()

    async def _refresh(self) -> None:
        try:
            data = await _live_json_async("/api/swarm/tasks/active")
            active = data.get("tasks") or []
            if not active:
                self.write("[dim]No active tasks on server[/]")
                return
            for t in active:
                tid = str(t.get("id") or t.get("task_id") or "")
                prompt = str(t.get("prompt") or "")
                self.write(
                    f"[$primary]●[/] {tid[:12]} [{t.get('status')}] {prompt[:60]}"
                )
        except Exception as exc:
            logger.debug("Active tasks refresh failed: %s", exc)

    async def on_show(self) -> None:
        self.clear()
        await self.on_mount()


class WorkerTree(Tree):
    """Tree showing worker hierarchy with capabilities."""

    def __init__(self) -> None:
        super().__init__("Workers")

    DEFAULT_CSS = """
    WorkerTree {
        height: 1fr;
        background: $surface;
        border: tall $border;
        padding: 0 1;
    }
    """

    async def on_mount(self) -> None:
        self.show_root = False
        root = self.root
        try:
            data = await _live_json_async("/api/swarm/status")
            workers = data.get("workers") or []
            if not workers:
                root.add_leaf("(no workers on server)")
                return
            for w in workers:
                name = str(w.get("name") or "?")
                node = root.add(name, expand=True)
                node.add_leaf(f"Role: {w.get('role') or ''}")
                node.add_leaf(f"Model: {w.get('model') or '?'}")
                node.add_leaf(f"Status: {w.get('status') or '?'}")
                caps = w.get("capabilities") or {}
                expertise = []
                if isinstance(caps, dict):
                    expertise = caps.get("expertise") or []
                if expertise:
                    exp_node = node.add("Expertise", expand=True)
                    for e in expertise:
                        exp_node.add_leaf(str(e))
        except Exception:
            root.add_leaf("(server unreachable)")

    async def on_show(self) -> None:
        self.clear()
        await self.on_mount()


class SwarmPanel(VerticalScroll):
    """Swarm tab: live metrics + sub-tabs Workers, Active, History, Tree."""

    DEFAULT_CSS = """
    SwarmPanel {
        height: 1fr;
        background: $surface;
        padding: 0 1;
    }
    SwarmPanel .section-label {
        height: 1;
        color: $text-muted;
        text-style: bold;
        padding: 0 0 1 0;
    }
    SwarmPanel .swarm-banner {
        height: 1;
        color: $primary;
        text-style: bold;
        padding: 0 1 1 1;
    }
    SwarmPanel .swarm-metrics {
        height: auto;
        margin: 0 0 1 0;
        padding: 1 1;
        background: $panel;
        border: tall $border;
    }
    SwarmPanel .swarm-metric-col {
        width: 1fr;
        height: auto;
        padding: 0 1;
    }
    SwarmPanel .swarm-metric-label {
        color: $text-muted;
        text-style: bold;
        height: 1;
    }
    SwarmPanel .swarm-metric-value {
        color: $primary;
        text-style: bold;
        height: 1;
    }
    SwarmPanel TabbedContent {
        height: 1fr;
        background: $surface;
    }
    SwarmPanel ContentTabs {
        background: $panel;
        border-bottom: solid $border;
        height: 3;
    }
    SwarmPanel WorkerTable,
    SwarmPanel SwarmTasksTable,
    SwarmPanel ActiveTasksLog,
    SwarmPanel WorkerTree {
        border: tall $border;
        background: $panel;
    }
    """

    REFRESH_INTERVAL = 2.0

    def compose(self) -> ComposeResult:
        yield Static("  SWARM  ·  workers · tasks · topology", classes="swarm-banner")
        with Horizontal(classes="swarm-metrics", id="swarm-metrics"):
            with Vertical(classes="swarm-metric-col"):
                yield Static("Workers online", classes="swarm-metric-label")
                yield Static("—", id="swarm-workers-val", classes="swarm-metric-value")
                yield Sparkline(max_points=24, id="swarm-workers-spark")
            with Vertical(classes="swarm-metric-col"):
                yield Static("Active tasks", classes="swarm-metric-label")
                yield Static("—", id="swarm-active-val", classes="swarm-metric-value")
                yield Sparkline(max_points=24, id="swarm-active-spark")
            with Vertical(classes="swarm-metric-col"):
                yield Static("Recent tasks", classes="swarm-metric-label")
                yield Static("—", id="swarm-recent-val", classes="swarm-metric-value")
                yield Sparkline(max_points=24, id="swarm-recent-spark")
        with TabbedContent(initial="workers"):
            with TabPane("Workers", id="workers"):
                yield Static("Registered workers", classes="section-label")
                yield WorkerTable()
            with TabPane("Active", id="active"):
                yield Static("In-flight tasks", classes="section-label")
                yield ActiveTasksLog()
            with TabPane("History", id="history"):
                yield Static("Recent task history", classes="section-label")
                yield SwarmTasksTable()
            with TabPane("Tree", id="tree"):
                yield Static("Worker capability hierarchy", classes="section-label")
                yield WorkerTree()
            with TabPane("Log", id="log"):
                yield Static("Live SwarmMessageBus stream", classes="section-label")
                yield LogStream(id="swarm-log-stream")

    def on_mount(self) -> None:
        self.app.call_later(self._refresh_metrics)
        self.set_interval(self.REFRESH_INTERVAL, self._refresh_metrics)
        self._subscribe_bus_log()

    def _subscribe_bus_log(self) -> None:
        """Wire the Log tab's LogStream to the SwarmMessageBus.

        The widget existed but was never mounted anywhere — the advertised
        "live swarm log stream" did not exist. The closure checks is_mounted
        so a replaced panel drops events instead of writing to a dead
        widget (the bus has no unsubscribe — see the audit hygiene note).
        """
        try:
            from kazma_core.swarm.bus import get_message_bus
        except Exception:
            logger.debug("[swarm-panel] bus unavailable — Log tab idle", exc_info=True)
            return
        try:
            widget = self.query_one("#swarm-log-stream", LogStream)

            def _on_bus_event(event_type: str, data: dict) -> None:
                try:
                    if self.is_mounted:
                        widget.handle_bus_event(event_type, data)
                except Exception:
                    pass

            get_message_bus().subscribe(_on_bus_event)
        except Exception:
            logger.debug("[swarm-panel] bus subscribe failed", exc_info=True)

    def on_show(self) -> None:
        self.app.call_later(self._refresh_metrics)

    async def _refresh_metrics(self) -> None:
        workers_online = 0
        workers_total = 0
        active_n = 0
        recent_n = 0
        try:
            status = await _live_json_async("/api/swarm/status")
            workers = status.get("workers") or []
            workers_total = len(workers)
            workers_online = sum(
                1
                for w in workers
                if str(w.get("status") or "") in {"online", "busy", "running"}
            )
            try:
                active_payload = await _live_json_async("/api/swarm/tasks/active")
                active_n = int(active_payload.get("count") or 0)
            except Exception:
                active_n = 0
            try:
                recent_payload = await _live_json_async("/api/swarm/tasks?pageSize=50")
                recent_n = len(recent_payload.get("tasks") or [])
            except Exception:
                recent_n = 0
        except Exception as exc:
            logger.debug("swarm metrics failed: %s", exc)

        try:
            self.query_one("#swarm-workers-val", Static).update(
                f"{workers_online}/{workers_total}"
            )
            self.query_one("#swarm-active-val", Static).update(str(active_n))
            self.query_one("#swarm-recent-val", Static).update(str(recent_n))
            self.query_one("#swarm-workers-spark", Sparkline).add_point(float(workers_online))
            self.query_one("#swarm-active-spark", Sparkline).add_point(float(active_n))
            self.query_one("#swarm-recent-spark", Sparkline).add_point(float(recent_n))
        except Exception:
            pass
