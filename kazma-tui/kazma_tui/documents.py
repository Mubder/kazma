"""Documents tab — read/inspect the shared DocumentIngestionService.

The TUI is a *view* onto the same durable document platform used by the Web
UI, native tools, and chat: it lists documents, shows their processing state,
and previews paged fenced content by opaque ID. Ingestion (upload) happens
from the Web UI / chat / tools; the TUI never parses bytes itself.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Button, DataTable, Static

__all__ = ["DocumentsPanel"]

_PREVIEW_MAX_CHARS = 8_000


class DocumentsPanel(VerticalScroll):
    """List documents and preview processed content from the shared platform."""

    DEFAULT_CSS = """
    DocumentsPanel {
        height: 1fr;
        background: $surface;
        padding: 0 1;
    }
    DocumentsPanel .docs-banner {
        height: 1;
        color: $primary;
        text-style: bold;
        padding: 0 1 1 1;
    }
    DocumentsPanel #docs-refresh {
        margin: 0 1 1 0;
        width: auto;
        min-width: 18;
        height: 3;
        border: tall $primary;
        color: $primary;
        background: $primary 12%;
    }
    DocumentsPanel Horizontal {
        height: 1fr;
    }
    DocumentsPanel DataTable {
        width: 45%;
        border: tall $border;
        background: $panel;
        margin-right: 1;
    }
    DocumentsPanel .preview {
        width: 1fr;
        border: tall $border;
        background: $panel;
        padding: 1 2;
        color: $text-muted;
    }
    """

    BINDINGS = []

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._rows: dict[str, str] = {}

    def compose(self) -> ComposeResult:
        yield Static("  DOCUMENTS  ·  library · state · preview", classes="docs-banner")
        yield Button("Refresh", id="docs-refresh", variant="primary")
        with Horizontal():
            table = DataTable(id="docs-table", cursor_type="row")
            table.add_columns("Title", "State", "ID")
            yield table
            yield Static("Select a document to preview", id="docs-preview", classes="preview")

    def on_mount(self) -> None:
        self.run_worker(self._load(), exclusive=True)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if (event.button.id or "") == "docs-refresh":
            self.run_worker(self._load(), exclusive=True)
            event.stop()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        doc_id = self._rows.get(str(event.row_key.value))
        if doc_id:
            self.run_worker(self._preview(doc_id), exclusive=True)

    async def _load(self) -> None:
        table = self.query_one("#docs-table", DataTable)
        table.clear()
        self._rows.clear()
        try:
            from kazma_core.runtime.local_api import request_json_async

            payload = await request_json_async("GET", "/api/documents")
            docs = (payload or {}).get("documents") or []
        except Exception as exc:  # noqa: BLE001
            self.query_one("#docs-preview", Static).update(
                f"[red]Document platform unavailable ({type(exc).__name__}). "
                "Start kazma serve — this tab is a mouth.[/]"
            )
            return
        if not docs:
            self.query_one("#docs-preview", Static).update(
                "[dim]No documents yet. Upload from the Web UI or chat.[/]"
            )
            return
        for d in docs:
            # Defensive: a partial/quarantined record or older ingestion
            # version may omit title/document_id; a hard subscript raises
            # KeyError and blanks the whole Documents tab (audit finding).
            doc_id = d.get("document_id")
            if not doc_id:
                continue
            key = table.add_row(
                d.get("title") or "(untitled)",
                d.get("state") or "unknown",
                str(doc_id)[:8],
            )
            self._rows[str(key.value)] = doc_id

    async def _preview(self, document_id: str) -> None:
        preview = self.query_one("#docs-preview", Static)
        try:
            from kazma_core.runtime.local_api import request_json_async

            payload = await request_json_async(
                "GET",
                f"/api/documents/{document_id}/content?max_chars={_PREVIEW_MAX_CHARS}",
            )
            data = (payload or {}).get("content") or payload or {}
            if isinstance(data, dict):
                text = str(data.get("text") or "")
            else:
                text = str(data)
            preview.update(text or "[dim](empty)[/]")
        except Exception as exc:  # noqa: BLE001
            preview.update(f"[red]Preview failed ({type(exc).__name__})[/]")
