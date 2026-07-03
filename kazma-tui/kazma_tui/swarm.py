"""Swarm panel — worker registry DataTable + task history."""

from __future__ import annotations

import logging
from typing import Any

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import DataTable, RichLog, Static, TabbedContent, TabPane

logger = logging.getLogger(__name__)


class WorkerTable(DataTable):
    """DataTable showing registered swarm workers."""

    DEFAULT_CSS = """
    WorkerTable { height: 1fr; background: transparent; }
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
                status = "● online" if getattr(worker, "_running", False) else "○ offline"
                self.add_row(name, worker.role, status, worker.model or "?")
        except Exception:
            self.add_row("(unavailable)", "", "", "")

    def on_show(self) -> None:
        self._refresh()


class SwarmTasksTable(DataTable):
    """DataTable showing recent task history."""

    DEFAULT_CSS = """
    SwarmTasksTable { height: 1fr; background: transparent; }
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
                dur = f"{t.duration_seconds:.1f}s" if getattr(t, "duration_seconds", None) else "—"
                self.add_row(
                    t.id[:16],
                    getattr(t, "type", "?"),
                    t.status,
                    ", ".join(t.workers[:3]) if hasattr(t, "workers") else "",
                    dur,
                )
        except Exception:
            pass

    def on_show(self) -> None:
        self._refresh()


class ActiveTasksLog(RichLog):
    """Log stream showing active/in-flight tasks."""

    DEFAULT_CSS = """
    ActiveTasksLog { height: 1fr; background: transparent; border: none; }
    """

    def on_mount(self) -> None:
        self.write("[bold $primary]Active Tasks[/]")
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
        except Exception:
            pass

    def on_show(self) -> None:
        self.clear()
        self.on_mount()


class SwarmPanel(VerticalScroll):
    """Swarm tab: sub-tabs for Workers, Active Tasks, Task History."""

    DEFAULT_CSS = """
    SwarmPanel { height: 1fr; }
    """

    def compose(self) -> ComposeResult:
        with TabbedContent(initial="workers"):
            with TabPane("Workers", id="workers"):
                yield Static("Registered swarm workers", classes="section-label")
                yield WorkerTable()
            with TabPane("Active", id="active"):
                yield Static("In-flight tasks", classes="section-label")
                yield ActiveTasksLog()
            with TabPane("History", id="history"):
                yield Static("Recent task history (last 20)", classes="section-label")
                yield SwarmTasksTable()
