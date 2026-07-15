"""Files tab — DirectoryTree browser with Markdown preview."""

from __future__ import annotations

import asyncio
from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Button, DirectoryTree, Markdown, RichLog, Static

# Cap how much of a file we read for preview. Reading a huge (or unbounded,
# e.g. a device/pipe) file fully into memory would both block the event loop
# for a long time and risk exhausting memory before the size cap is ever
# reached, since read_text()/read() load everything before returning.
_PREVIEW_MAX_CHARS = 200_000


class FilesPanel(VerticalScroll):
    """File browser: DirectoryTree left, Markdown/RichLog preview right."""

    DEFAULT_CSS = """
    FilesPanel { height: 1fr; background: $surface; }
    FilesPanel Horizontal { height: 1fr; }
    FilesPanel DirectoryTree { width: 35%; border: solid $border; background: $panel; }
    FilesPanel .preview { width: 1fr; border: solid $border; background: $panel; }
    """

    BINDINGS = [
        Binding("e", "open_editor", "Edit", show=True),
    ]

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._selected_path = None

    def compose(self) -> ComposeResult:
        cwd = str(Path.cwd())
        yield Static(f"[bold $primary]Files[/]  ·  [dim]{cwd}[/]", classes="section-label")
        yield Button("Open in editor (e)", id="open-editor", variant="primary")
        with Horizontal():
            yield DirectoryTree(cwd, id="file-tree")
            yield Static("Select a file to preview", id="file-preview", classes="preview")

    def on_directory_tree_file_selected(self, event: DirectoryTree.FileSelected) -> None:
        path = event.path
        self._selected_path = path
        self.run_worker(self._show_preview(path), exclusive=True)

    def _rel_path_for(self, path) -> "str | None":
        """Return the workspace-relative path, or None if outside the workspace."""
        # Import lazily to avoid import cycles with kazma_core.
        from kazma_core.ide.service import get_ide_service

        root = get_ide_service().root
        try:
            return str(Path(path).resolve().relative_to(root.resolve()))
        except ValueError:
            return None

    def action_open_editor(self) -> None:
        if self._selected_path is None:
            return
        rel = self._rel_path_for(self._selected_path)
        if rel is None:
            from kazma_tui.widgets.toast import Toast

            self.app.push_screen(
                Toast("Selected file is outside the workspace root", "error", duration=2.5)
            )
            return
        # Lazy import keeps kazma_core out of the module import graph.
        from kazma_tui.editor import EditorScreen
        from kazma_core.ide.service import get_ide_service

        self.app.push_screen(
            EditorScreen(rel_path=rel, workspace_root=get_ide_service().root)
        )


    @staticmethod
    def _read_preview_text(path: Path, limit: int) -> tuple[str, bool]:
        """Read up to `limit` chars of a file. Runs in a worker thread.

        Returns (content, truncated).
        """
        with path.open("r") as f:
            content = f.read(limit + 1)
        truncated = len(content) > limit
        return content[:limit], truncated

    async def _show_preview(self, path: Path) -> None:
        container = self.query_one("#file-preview", Static)
        # Properly remove all previously mounted child widgets
        for child in list(container.children):
            child.remove()
        try:
            # Reading (and decoding) a file is blocking I/O — run it off the
            # event loop so a large file doesn't freeze the whole TUI while
            # it's loaded.
            content, truncated = await asyncio.to_thread(
                self._read_preview_text, path, _PREVIEW_MAX_CHARS
            )
            if truncated:
                content += "\n\n[dim]… (truncated preview)[/]"
            if path.suffix in (".md", ".markdown", ".MD"):
                container.mount(Markdown(content))
            else:
                log = RichLog(highlight=True, markup=True)
                container.mount(log)
                log.write(f"[bold $primary]{path.name}[/]\n")
                log.write(content)
        except UnicodeDecodeError:
            container.mount(Static("[dim](binary file)[/]"))
        except Exception as e:
            container.mount(Static(f"[#ef4444]Error: {e}[/]"))
