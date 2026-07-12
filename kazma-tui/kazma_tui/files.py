"""Files tab — DirectoryTree browser with Markdown preview."""

from __future__ import annotations

import asyncio
from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import DirectoryTree, Markdown, RichLog, Static

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

    def compose(self) -> ComposeResult:
        cwd = str(Path.cwd())
        yield Static(f"[bold $primary]Files[/]  ·  [dim]{cwd}[/]", classes="section-label")
        with Horizontal():
            yield DirectoryTree(cwd, id="file-tree")
            yield Static("Select a file to preview", id="file-preview", classes="preview")

    def on_directory_tree_file_selected(self, event: DirectoryTree.FileSelected) -> None:
        path = event.path
        self.run_worker(self._show_preview(path), exclusive=True)

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
