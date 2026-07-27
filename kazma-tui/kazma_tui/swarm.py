"""Swarm panel — DataTable workers + RichLog tasks + Tree hierarchy."""

from __future__ import annotations

import logging
from datetime import datetime, UTC

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import DataTable, RichLog, Static, TabbedContent, TabPane, Tree

from kazma_tui.widgets.sparkline import Sparkline

__all__ = ["ActiveTasksLog", "SwarmPanel", "SwarmTasksTable", "WorkerTable", "WorkerTree"]

logger = logging.getLogger(__name__)

_HEARTBEAT_STALE_SECONDS = 60


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

    def on_mount(self) -> None:
        self.cursor_type = "row"
        self.add_columns("Name", "Role", "Status", "Model")
        self._refresh()

    def _refresh(self) -> None:
        self.clear()
        try:
            from kazma_core.swarm import get_swarm_engine
            engine = get_swarm_engine()
            if engine is None:
                self.add_row("(no engine)", "", "", "")
                return
            for name, worker in engine._workers.items():
                status = "● online" if _worker_is_online(worker) else "○ offline"
                self.add_row(name, worker.role, status, worker.model or "?")
        except Exception:
            self.add_row("(unavailable)", "", "", "")

    def on_show(self) -> None:
        self._refresh()


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

    def on_mount(self) -> None:
        self.cursor_type = "row"
        self.add_columns("Task ID", "Type", "Status", "Workers", "Duration")
        self._refresh()

    def _refresh(self) -> None:
        self.clear()
        try:
            from kazma_core.swarm import get_swarm_engine
            engine = get_swarm_engine()
            if engine is None:
                return
            tasks = engine.list_tasks()[:20]
            for t in tasks:
                dur = self._task_duration(t)
                self.add_row(
                    t.id[:16],
                    t.type.value if hasattr(t.type, "value") else str(t.type),
                    t.status.value if hasattr(t.status, "value") else str(t.status),
                    ", ".join(t.workers[:3]) if t.workers else "",
                    dur,
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

    def on_mount(self) -> None:
        self.write("[bold #22d3ee]Active Tasks[/]")
        self._refresh()

    def _refresh(self) -> None:
        try:
            from kazma_core.swarm import get_swarm_engine
            engine = get_swarm_engine()
            if engine is None:
                return
            active = engine.list_active_tasks()
            if not active:
                self.write("[dim]No active tasks[/]")
                return
            for t in active:
                self.write(f"[$primary]●[/] {t.id[:12]} [{t.status}] {t.prompt[:60]}")
        except Exception as exc:
            logger.debug("Active tasks refresh failed: %s", exc)

    def on_show(self) -> None:
        self.clear()
        self.on_mount()


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

    def on_mount(self) -> None:
        self.show_root = False
        root = self.root
        try:
            from kazma_core.swarm import get_swarm_engine
            engine = get_swarm_engine()
            if engine is None:
                root.add_leaf("(no engine)")
                return
            for name, worker in engine._workers.items():
                node = root.add(name, expand=True)
                node.add_leaf(f"Role: {worker.role}")
                node.add_leaf(f"Model: {worker.model or '?'}")
                node.add_leaf(f"Status: {'online' if _worker_is_online(worker) else 'offline'}")
                caps = getattr(worker, "capabilities", None)
                if caps:
                    expertise = getattr(caps, "expertise", [])
                    if expertise:
                        exp_node = node.add("Expertise", expand=True)
                        for e in expertise:
                            exp_node.add_leaf(e)
        except Exception:
            root.add_leaf("(unavailable)")

    def on_show(self) -> None:
        self.clear()
        self.on_mount()


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

    def on_mount(self) -> None:
        self._refresh_metrics()
        self.set_interval(self.REFRESH_INTERVAL, self._refresh_metrics)

    def on_show(self) -> None:
        self._refresh_metrics()

    def _refresh_metrics(self) -> None:
        workers_online = 0
        workers_total = 0
        active_n = 0
        recent_n = 0
        try:
            from kazma_core.swarm import get_swarm_engine

            engine = get_swarm_engine()
            if engine is not None:
                workers = getattr(engine, "_workers", {}) or {}
                workers_total = len(workers)
                for w in workers.values():
                    if _worker_is_online(w):
                        workers_online += 1
                try:
                    active_n = len(engine.list_active_tasks() or [])
                except Exception:
                    active_n = 0
                try:
                    recent_n = len(engine.list_tasks()[:50] or [])
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
