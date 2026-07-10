"""Files tab — DirectoryTree browser with Markdown preview."""

from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import DirectoryTree, Markdown, RichLog, Static


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
        self._show_preview(path)

    def _show_preview(self, path: Path) -> None:
        container = self.query_one("#file-preview", Static)
        # Properly remove all previously mounted child widgets
        for child in list(container.children):
            child.remove()
        try:
            content = path.read_text()
            if path.suffix in (".md", ".markdown", ".MD"):
                container.mount(Markdown(content))
            else:
                log = RichLog(highlight=True, markup=True)
                container.mount(log)
                log.write(f"[bold $primary]{path.name}[/]\n")
                log.write(content[:10000])
        except UnicodeDecodeError:
            container.mount(Static("[dim](binary file)[/]"))
        except Exception as e:
            container.mount(Static(f"[#ef4444]Error: {e}[/]"))
