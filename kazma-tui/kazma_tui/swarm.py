"""Swarm panel — DataTable workers + RichLog tasks + Tree hierarchy."""

from __future__ import annotations

import logging

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import DataTable, RichLog, Static, TabbedContent, TabPane, Tree

logger = logging.getLogger(__name__)


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
                status = "● online" if getattr(worker, "_running", False) else "○ offline"
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
                dur = f"{t.duration_seconds:.1f}s" if getattr(t, "duration_seconds", None) else "—"
                self.add_row(
                    t.id[:16], getattr(t, "type", "?"), t.status,
                    ", ".join(t.workers[:3]) if hasattr(t, "workers") else "", dur,
                )
        except Exception:
            pass

    def on_show(self) -> None:
        self._refresh()


class ActiveTasksLog(RichLog):
    """Log stream showing active/in-flight tasks."""

    DEFAULT_CSS = """ActiveTasksLog { height: 1fr; background: transparent; border: none; }"""

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
        except Exception:
            pass

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
                node.add_leaf(f"Status: {'online' if getattr(worker, '_running', False) else 'offline'}")
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
    """Swarm tab: sub-tabs Workers, Active, History, Tree."""

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
