"""Documents tab — read/inspect the shared DocumentIngestionService.

The TUI is a *view* onto the same durable document platform used by the Web
UI, native tools, and chat: it lists documents, shows their processing state,
and previews paged fenced content by opaque ID. Ingestion (upload) happens
from the Web UI / chat / tools; the TUI never parses bytes itself.
"""

from __future__ import annotations

import asyncio

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

    def _tenant(self) -> str:
        try:
            from kazma_core.tenant_context import get_current_tenant_id

            return (get_current_tenant_id() or "default").strip() or "default"
        except Exception:
            return "default"

    async def _load(self) -> None:
        table = self.query_one("#docs-table", DataTable)
        table.clear()
        self._rows.clear()
        try:
            from kazma_core.documents.ingestion import get_ingestion_service

            svc = get_ingestion_service()
            docs = await asyncio.to_thread(
                svc.list_documents, tenant_id=self._tenant(), actor_id="agent"
            )
        except Exception as exc:  # noqa: BLE001
            self.query_one("#docs-preview", Static).update(
                f"[red]Document platform unavailable ({type(exc).__name__})[/]"
            )
            return
        if not docs:
            self.query_one("#docs-preview", Static).update(
                "[dim]No documents yet. Upload from the Web UI or chat.[/]"
            )
            return
        for d in docs:
            key = table.add_row(
                d["title"], d.get("state") or "unknown", d["document_id"][:8]
            )
            self._rows[str(key.value)] = d["document_id"]

    async def _preview(self, document_id: str) -> None:
        preview = self.query_one("#docs-preview", Static)
        try:
            from kazma_core.documents.ingestion import (
                DocumentIngestionError,
                get_ingestion_service,
            )

            svc = get_ingestion_service()
            data = await asyncio.to_thread(
                svc.get_content,
                tenant_id=self._tenant(),
                actor_id="agent",
                document_id=document_id,
                max_chars=_PREVIEW_MAX_CHARS,
            )
            preview.update(data["text"] or "[dim](empty)[/]")
        except DocumentIngestionError as exc:
            preview.update(f"[yellow]{exc.safe_message}[/]")
        except Exception as exc:  # noqa: BLE001
            preview.update(f"[red]Preview failed ({type(exc).__name__})[/]")
