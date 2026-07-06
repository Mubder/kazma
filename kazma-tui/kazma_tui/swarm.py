"""Swarm panel — DataTable workers + RichLog tasks + Tree hierarchy."""

from __future__ import annotations

import logging
from datetime import datetime, UTC

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import DataTable, RichLog, Static, TabbedContent, TabPane, Tree

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

    DEFAULT_CSS = """WorkerTable { height: 1fr; background: transparent; }"""

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

    DEFAULT_CSS = """SwarmTasksTable { height: 1fr; background: transparent; }"""

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

    DEFAULT_CSS = """ActiveTasksLog { height: 1fr; background: transparent; border: none; }"""

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
                self.write(f"[#22d3ee]●[/] {t.id[:12]} [{t.status}] {t.prompt[:60]}")
        except Exception as exc:
            logger.debug("Active tasks refresh failed: %s", exc)

    def on_show(self) -> None:
        self.clear()
        self.on_mount()


class WorkerTree(Tree):
    """Tree showing worker hierarchy with capabilities."""

    def __init__(self) -> None:
        super().__init__("Workers")

    DEFAULT_CSS = """WorkerTree { height: 1fr; background: transparent; }"""

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


class SwarmTopology(Static):
    """Pulsing NOC-style ASCII/Unicode network topology diagram."""

    DEFAULT_CSS = """
    SwarmTopology {
        height: 1fr;
        background: transparent;
        padding: 1 2;
        font-family: monospace;
    }
    """

    def on_mount(self) -> None:
        self._refresh()
        self.set_interval(2.0, self._refresh)

    def _refresh(self) -> None:
        try:
            from kazma_core.swarm import get_swarm_engine
            engine = get_swarm_engine()
            if engine is None:
                self.update("[dim]No Swarm Engine initialized[/]")
                return

            workers = engine._workers
            active_tasks = engine.list_active_tasks()
            
            # Map active workers based on task participants
            busy_workers = set()
            for t in active_tasks:
                if hasattr(t, "workers") and t.workers:
                    for w in t.workers:
                        busy_workers.add(w)

            # Let's construct a beautiful diagram!
            lines = []
            lines.append("                     [bold #22d3ee]┌──────────────────────────┐[/]")
            lines.append("                     [bold #22d3ee]│        SUPERVISOR        │[/]")
            lines.append("                     [bold #22d3ee]│     (LangGraph Brain)    │[/]")
            lines.append("                     [bold #22d3ee]└─────────────┬────────────┘[/]")
            
            if not workers:
                lines.append("                                   │")
                lines.append("                          [dim](No registered workers)[/]")
                self.update("\n".join(lines))
                return

            worker_list = list(workers.items())
            num_workers = len(worker_list)

            for idx, (name, worker) in enumerate(worker_list):
                is_last = (idx == num_workers - 1)
                branch = "    └──" if is_last else "    ├──"
                
                online = _worker_is_online(worker)
                busy = name in busy_workers
                
                if busy:
                    wire_label = "[bold #10b981]═══[⚡ BUSY]═══>[/]"
                    node_style = "[bold #10b981]"
                    node_status = "BUSY"
                elif online:
                    wire_label = "[#22d3ee]───(idle)───>[/]"
                    node_style = "[#22d3ee]"
                    node_status = "ONLINE"
                else:
                    wire_label = "[dim]───x[offline]x──>[/]"
                    node_style = "[dim]"
                    node_status = "OFFLINE"
                    
                lines.append(f"                   │")
                lines.append(
                    f"{branch} [bold]{name:<12}[/] {wire_label} {node_style}┌──────────────────────┐[/]"
                )
                lines.append(
                    f"                                   {node_style}│ {node_status:<20} │[/]"
                    f" [dim]{worker.role[:20]}[/]"
                )
                lines.append(
                    f"                                   {node_style}└──────────────────────┘[/]"
                )

            self.update("\n".join(lines))
        except Exception as e:
            self.update(f"[red]Topology Render Error: {e}[/]")

    def on_show(self) -> None:
        self._refresh()


class SwarmPanel(VerticalScroll):
    """Swarm tab: sub-tabs Workers, Active, History, Tree, Topology."""

    DEFAULT_CSS = """SwarmPanel { height: 1fr; }"""

    def compose(self) -> ComposeResult:
        with TabbedContent(initial="workers"):
            with TabPane("Workers", id="workers"):
                yield Static("Registered swarm workers", classes="section-label")
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
            with TabPane("Topology", id="topology"):
                yield Static("Active Swarm Network Map", classes="section-label")
                yield SwarmTopology()
