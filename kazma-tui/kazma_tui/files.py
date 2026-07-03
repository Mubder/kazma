"""Files tab — DirectoryTree browser for project files."""

from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import DirectoryTree, RichLog, Static


class FilesPanel(VerticalScroll):
    """File browser: DirectoryTree on left, file preview on right."""

    DEFAULT_CSS = """
    FilesPanel { height: 1fr; background: $surface; }
    FilesPanel Horizontal { height: 1fr; }
    FilesPanel DirectoryTree {
        width: 35%;
        border: solid $border;
        background: $panel;
    }
    FilesPanel RichLog {
        width: 1fr;
        border: solid $border;
        background: $panel;
    }
    """

    def compose(self) -> ComposeResult:
        cwd = str(Path.cwd())
        yield Static(f"[bold $primary]Files[/]  ·  [dim]{cwd}[/]", classes="section-label")
        with Horizontal():
            yield DirectoryTree(cwd, id="file-tree")
            yield RichLog(id="file-preview", highlight=True, markup=True)

    def on_directory_tree_file_selected(self, event: DirectoryTree.FileSelected) -> None:
        path = event.path
        preview = self.query_one("#file-preview", RichLog)
        preview.clear()
        preview.write(f"[bold #22d3ee]{path.name}[/]\n")
        try:
            text = path.read_text()[:5000]
            preview.write(text)
        except UnicodeDecodeError:
            preview.write("[dim](binary file)[/]")
        except Exception as e:
            preview.write(f"[#ef4444]Error: {e}[/]")
