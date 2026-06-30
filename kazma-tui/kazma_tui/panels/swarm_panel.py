"""Swarm Panel — live TUI visualization for WorkerRegistry + MessageBus.

Left panel: worker table (Name, Expertise, Role, Model, Status).
Right panel: real-time bus log stream with color-coded levels.

Uses Textual's DataTable for workers and RichLog for the bus stream.
Read-only consumers of WorkerRegistry and SwarmMessageBus.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.reactive import reactive
from textual.widgets import DataTable, Static
from textual.widget import Widget

from kazma_tui.widgets.log_stream import LogStream

logger = logging.getLogger(__name__)

# Refresh interval for worker status (seconds).
_REFRESH_INTERVAL = 2.0


class WorkerTable(DataTable):
    """DataTable showing swarm workers from the WorkerRegistry.

    Columns: Name, Expertise, Role, Model, Provider, Status
    """

    DEFAULT_CSS = """
    WorkerTable {
        height: 1fr;
        border: solid $primary;
    }
    """

    def on_mount(self) -> None:
        self.cursor_type = "row"
        self.add_columns("Name", "Expertise", "Role", "Model", "Provider", "Status")

    def refresh_workers(self) -> None:
        """Pull worker data from the WorkerRegistry and update the table."""
        try:
            from kazma_core.swarm.registry import WorkerRegistry

            registry = WorkerRegistry()
            workers = registry.list_all()

            self.clear()
            for w in workers:
                status = "Enabled" if w.enabled else "Disabled"
                self.add_row(
                    w.name,
                    ", ".join(w.expertise),
                    ", ".join(w.roles),
                    w.model or "—",
                    w.provider or "—",
                    status,
                )

            self._add_footer(len(workers))
        except Exception as exc:
            logger.debug("[WorkerTable] refresh failed: %s", exc)
            self.clear()
            self.add_row("—", "Registry unavailable", "", "", "", str(exc)[:40])

    def _add_footer(self, count: int) -> None:
        """Add a footer row with worker count."""
        self.add_row(f"{count} workers", "", "", "", "", "")


class SwarmPanel(Widget):
    """Split-pane swarm observability panel.

    Left: WorkerTable from WorkerRegistry.
    Right: LogStream from SwarmMessageBus.

    Auto-refreshes worker status every 2 seconds.
    """

    DEFAULT_CSS = """
    SwarmPanel {
        layout: horizontal;
        height: 1fr;
    }

    SwarmPanel > Horizontal {
        height: 1fr;
    }

    SwarmPanel WorkerTable {
        width: 45%;
        margin: 1;
    }

    SwarmPanel LogStream {
        width: 55%;
        margin: 1;
    }
    """

    def __init__(
        self,
        name: str | None = None,
        id: str | None = None,
    ) -> None:
        super().__init__(name=name, id=id)

    def compose(self) -> ComposeResult:
        with Horizontal():
            yield WorkerTable(id="swarm-worker-table")
            yield LogStream(id="swarm-bus-log")

    def on_mount(self) -> None:
        # Initial worker load
        table = self.query_one(WorkerTable)
        table.refresh_workers()

        # Subscribe bus log to SwarmMessageBus
        log = self.query_one(LogStream)
        try:
            from kazma_core.swarm.bus import get_message_bus

            bus = get_message_bus()
            bus.subscribe(log.handle_bus_event)
        except Exception:
            pass

        # Periodic worker status refresh
        self.set_interval(_REFRESH_INTERVAL, self._refresh_worker_table)

    def _refresh_worker_table(self) -> None:
        """Periodic callback to refresh the worker table."""
        table = self.query_one(WorkerTable)
        table.refresh_workers()
