"""The TUI's Traces tab works when there are traces.

Both of its DataTable handlers read ``event.coordinate``, which Textual's
``RowHighlighted`` and ``RowSelected`` do not have: the first row the table
put its cursor on raised AttributeError and took the whole app down. With
no traces nothing fired, which is how every TUI test missed it; the full suite
found it on 2026-10-01, when earlier tests had left traces in the process's
store. ``tests/test_tui_event_attributes.py`` checks every handler's event
attributes against Textual.
"""

from __future__ import annotations

import time

import pytest
from kazma_core.tracing import TraceEntry, TraceStore


def _store(n: int) -> TraceStore:
    store = TraceStore()
    for i in range(n):
        store.add(TraceEntry(timestamp=time.time() + i, trace_type="llm",
                             label=f"call {i}", status="success", duration_ms=5.0,
                             details=f"details {i}"))
    return store


async def _host(monkeypatch, store: TraceStore):
    from kazma_tui.traces import TracesPanel
    from textual.app import App

    monkeypatch.setattr("kazma_core.tracing.get_trace_store", lambda: store)
    panel = TracesPanel()

    class _Host(App):
        def compose(self):
            yield panel

    return _Host(), panel


@pytest.mark.asyncio
async def test_moving_through_the_traces_shows_each_ones_details(monkeypatch):
    from textual.widgets import DataTable

    app, panel = await _host(monkeypatch, _store(3))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        table = panel.query_one("#trace-table", DataTable)
        assert table.row_count == 3
        assert panel._selected_entry is panel._displayed_entries[0]

        table.focus()
        await pilot.press("down")
        await pilot.pause()
        assert panel._selected_entry is panel._displayed_entries[1]

        await pilot.press("down", "enter")
        await pilot.pause()
        assert panel._selected_entry is panel._displayed_entries[2]


@pytest.mark.asyncio
async def test_a_traces_text_is_shown_as_it_is(monkeypatch):
    """Table cells and the details pane are Rich markup. The status used
    Textual's "$success" (MarkupError in the details pane), and a label or
    details holding brackets -- tool output does -- were read as markup:
    "list[str]" lost its "[str]", "a [/b] b" raised MarkupError."""
    from rich.text import Text
    from textual.widgets import DataTable, RichLog
    from textual.widgets._data_table import default_cell_formatter

    store = TraceStore()
    labels = ["list[str] parse", "a [/b] b", "[bold]not bold[/bold]"]
    for i, (label, status) in enumerate(zip(labels, ["success", "warning", "error"])):
        store.add(TraceEntry(timestamp=time.time() + i, trace_type="tool", label=label,
                             status=status, duration_ms=1.0, details='["x", "[/y]"]'))
    app, panel = await _host(monkeypatch, store)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        table = panel.query_one("#trace-table", DataTable)
        shown = [default_cell_formatter(table.get_row_at(r)[2]).plain for r in range(3)]
        assert shown == labels
        for r in range(3):
            assert isinstance(default_cell_formatter(table.get_row_at(r)[3]), Text)
        table.focus()
        await pilot.press("down", "down")
        await pilot.pause()
        details = panel.query_one("#trace-details", RichLog)
        text = "\n".join(strip.text for strip in details.lines)
        assert "[bold]not bold[/bold]" in text
        assert '"[/y]"' in text


@pytest.mark.asyncio
async def test_no_traces_shows_an_empty_table(monkeypatch):
    from textual.widgets import DataTable

    app, panel = await _host(monkeypatch, _store(0))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        assert panel.query_one("#trace-table", DataTable).row_count == 0
        assert panel._selected_entry is None
