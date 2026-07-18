"""Traces panel — Observability log explorer with filtering and details preview."""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widget import Widget
from textual.widgets import DataTable, Input, RichLog, Static

__all__ = ["TracesPanel"]

logger = logging.getLogger(__name__)


class TracesPanel(Vertical):
    """Traces: DataTable trace list left, RichLog trace details preview right."""

    DEFAULT_CSS = """
    TracesPanel {
        height: 1fr;
        background: $surface;
    }

    TracesPanel > .section-label {
        height: 1;
        padding: 0 2;
        color: $text-muted;
        text-style: bold;
    }

    TracesPanel > .toolbar {
        height: 3;
        layout: horizontal;
        padding: 0 2;
        background: $panel;
        border-bottom: solid $border;
        align: left middle;
    }

    TracesPanel > .toolbar > Input {
        width: 1fr;
        height: 1;
        background: $surface;
        border: solid $border;
        margin: 0 1 0 0;
    }

    TracesPanel > .toolbar > Input:focus {
        border: solid $primary;
    }

    TracesPanel > .toolbar > .stats-bar {
        width: auto;
        min-width: 30;
        height: 1;
        content-align: right middle;
        color: $text-muted;
    }

    TracesPanel Horizontal {
        height: 1fr;
    }

    TracesPanel DataTable {
        width: 55%;
        height: 1fr;
        border-right: solid $border;
        background: $surface;
    }

    TracesPanel DataTable:focus {
        border: solid $primary;
    }

    TracesPanel .details-pane {
        width: 45%;
        height: 1fr;
        background: $panel;
        padding: 1 2;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._trace_store: Any = None
        self._all_entries: list[Any] = []
        self._displayed_entries: list[Any] = []
        self._selected_entry: Any = None

    def compose(self) -> ComposeResult:
        yield Static("Traces", classes="section-label")
        with Horizontal(classes="toolbar"):
            yield Input(placeholder="🔍 Filter traces (text or regex)...", id="trace-search")
            yield Static("Traces: 0 | Tokens: 0 | Cost: $0.00", id="trace-stats", classes="stats-bar")

        with Horizontal():
            table = DataTable(id="trace-table")
            table.cursor_type = "row"
            table.zebra_stripes = True
            yield table

            yield RichLog(id="trace-details", highlight=True, wrap=True, markup=True, classes="details-pane")

    async def on_mount(self) -> None:
        """Initialize datatable and start refresh cycle."""
        table = self.query_one("#trace-table", DataTable)
        table.add_columns("Time", "Type", "Label", "Status", "Duration")
        
        await self._refresh_data()
        self.set_interval(2.0, self._refresh_data)

    def _get_trace_store(self) -> Any:
        """Resolve the trace store singleton."""
        if self._trace_store is not None:
            return self._trace_store
        try:
            from kazma_core.tracing import get_trace_store
            self._trace_store = get_trace_store()
        except ImportError:
            logger.debug("TraceStore not available in this environment")
        return self._trace_store

    async def _refresh_data(self) -> None:
        """Pull latest traces from TraceStore and update table."""
        store = self._get_trace_store()
        if store is None:
            return

        try:
            # Stats update
            stats = store.stats()
            stats_widget = self.query_one("#trace-stats", Static)
            stats_widget.update(
                f"[dim]Traces:[/] {stats.get('total_traces', 0)}  ·  "
                f"[dim]Tokens:[/] {stats.get('total_tokens', 0):,}  ·  "
                f"[dim]Cost:[/] ${stats.get('total_cost', 0.0):.4f}"
            )

            # Retrieve entries
            self._all_entries = store.recent(limit=300)
            self._apply_filter()
        except Exception as e:
            logger.debug(f"Traces refresh failed: {e}", exc_info=True)

    def on_input_changed(self, event: Input.Changed) -> None:
        """Handle real-time typing inside the search input box."""
        if event.input.id == "trace-search":
            self._apply_filter()

    def _apply_filter(self) -> None:
        """Filter self._all_entries into self._displayed_entries and render."""
        query = self.query_one("#trace-search", Input).value.strip().lower()
        
        if not query:
            self._displayed_entries = list(self._all_entries)
        else:
            filtered = []
            try:
                # Attempt regex search, fallback to simple substring
                pattern = re.compile(query, re.IGNORECASE)
                for entry in self._all_entries:
                    text_fields = f"{entry.label} {entry.trace_type} {entry.status} {entry.details}".lower()
                    if pattern.search(text_fields):
                        filtered.append(entry)
            except re.error:
                # Substring fallback on invalid regex
                for entry in self._all_entries:
                    text_fields = f"{entry.label} {entry.trace_type} {entry.status} {entry.details}".lower()
                    if query in text_fields:
                        filtered.append(entry)
            self._displayed_entries = filtered

        self._render_table()

    def _render_table(self) -> None:
        """Populate the DataTable widget with current filtered entries."""
        table = self.query_one("#trace-table", DataTable)
        
        # Save current row index & scroll position to restore
        current_cursor = table.cursor_coordinate
        
        table.clear()
        
        for idx, entry in enumerate(self._displayed_entries):
            time_str = datetime.fromtimestamp(entry.timestamp).strftime("%H:%M:%S")
            
            # Formatted status
            if entry.status == "error":
                status_markup = "[bold $error]ERROR[/bold $error]"
            elif entry.status == "warning":
                status_markup = "[bold $secondary]WARN[/bold $secondary]"
            else:
                status_markup = "[bold $success]OK[/bold $success]"

            # Formatted type
            type_markup = f"[dim]{entry.trace_type.upper()}[/dim]"
            
            # Formatted duration
            dur_str = f"{entry.duration_ms:.0f}ms" if entry.duration_ms >= 0 else "N/A"
            
            table.add_row(
                time_str,
                type_markup,
                entry.label,
                status_markup,
                dur_str,
                key=str(idx),
            )

        # Restore cursor if within range
        if current_cursor and current_cursor.row < len(self._displayed_entries):
            table.cursor_coordinate = current_cursor
            # Trigger preview update manually
            self._update_preview(current_cursor.row)
        elif len(self._displayed_entries) > 0:
            table.cursor_coordinate = (0, 0)
            self._update_preview(0)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Update details pane on row click/select."""
        row_idx = event.coordinate.row
        self._update_preview(row_idx)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        """Update details pane on keyboard navigation."""
        row_idx = event.coordinate.row
        self._update_preview(row_idx)

    def _update_preview(self, row_idx: int) -> None:
        """Render detailed trace info into details view panel."""
        if row_idx < 0 or row_idx >= len(self._displayed_entries):
            return

        entry = self._displayed_entries[row_idx]
        if self._selected_entry == entry:
            return  # Avoid redraw if selection hasn't changed
            
        self._selected_entry = entry
        details_pane = self.query_one("#trace-details", RichLog)
        details_pane.clear()

        # Format general information header
        time_str = datetime.fromtimestamp(entry.timestamp).strftime("%Y-%m-%d %H:%M:%S")
        status_color = "$error" if entry.status == "error" else ("$secondary" if entry.status == "warning" else "$success")

        details_pane.write("[bold]TRACE METRICS[/]")
        details_pane.write(f"  [dim]Timestamp:[/]  {time_str}")
        details_pane.write(f"  [dim]Type:[/]       {entry.trace_type.upper()}")
        details_pane.write(f"  [dim]Label:[/]      {entry.label}")
        details_pane.write(f"  [dim]Status:[/]     [{status_color}]{entry.status.upper()}[/{status_color}]")
        details_pane.write(f"  [dim]Duration:[/]   {entry.duration_ms:.1f} ms")

        if entry.tokens > 0:
            details_pane.write(f"  [dim]Tokens:[/]     {entry.tokens:,}")
            details_pane.write(f"  [dim]Cost:[/]       ${entry.cost:.4f}")

        details_pane.write("\n" + "─" * 40 + "\n")
        details_pane.write("[bold]TRACE BODY / DIAGNOSTIC DETAILS[/]\n")
        
        # Details text formatting (JSON, XML or raw text)
        raw_details = entry.details or ""
        if raw_details.strip().startswith("{") or raw_details.strip().startswith("["):
            try:
                import json
                parsed = json.loads(raw_details)
                pretty_details = json.dumps(parsed, indent=2)
                details_pane.write(pretty_details)
                return
            except Exception:
                pass
                
        details_pane.write(raw_details)
