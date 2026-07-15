"""Editor screen — in-TUI code editor backed by the transport-agnostic IDE service.

The editor is fully additive: it opens as a pushed Screen on top of the Files
tab and never touches the supervisor graph, swarm engine, or safety/HITL code.
All mutating/executing operations go through ``get_ide_service()`` so the same
HITL danger-tool gate the agent/swarm use is enforced here too.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Optional

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, Input, RichLog, Static, TextArea

logger = logging.getLogger(__name__)

# Languages Textual can highlight. Unknown ones are set defensively and
# silently skipped (Textual raises if a language is not registered).
_KNOWN_LANGS = {
    "python", "json", "toml", "yaml", "markdown", "bash", "xml", "ini",
    "javascript", "typescript", "html", "css", "sql", "rust", "go", "java",
    "c", "cpp", "ruby", "php", "sh",
}


class PromptScreen(ModalScreen[str]):
    """Generic single-line prompt modal (used for Grep pattern / run command)."""

    def __init__(self, title: str, placeholder: str = "", initial: str = "") -> None:
        super().__init__()
        self._title = title
        self._placeholder = placeholder
        self._initial = initial

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static(self._title, classes="prompt-title"),
            Input(placeholder=self._placeholder, value=self._initial, id="prompt-input"),
            Horizontal(
                Button("OK", variant="primary", id="prompt-ok"),
                Button("Cancel", id="prompt-cancel"),
                classes="prompt-buttons",
            ),
            classes="prompt-box",
        )

    def on_mount(self) -> None:
        self.query_one("#prompt-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip())

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "prompt-ok":
            value = self.query_one("#prompt-input", Input).value.strip()
            self.dismiss(value)
        else:
            self.dismiss("")


class EditorScreen(Screen[None]):
    """Full-screen code editor for a single workspace file.

    Args:
        rel_path: Path relative to the IDE workspace root (as understood by
            ``get_ide_service()`` — ``""`` means the workspace root itself).
        workspace_root: Absolute workspace root, used to render an absolute
            path in the title. The backend is the source of truth for scoping;
            this is display-only.
    """

    DEFAULT_CSS = """
    EditorScreen { background: $surface; }
    EditorScreen .editor-header {
        height: auto;
        padding: 0 1;
        background: $primary-darken-1;
        color: $text;
    }
    EditorScreen TextArea { height: 1fr; border: solid $border; }
    EditorScreen .editor-toolbar { height: auto; padding: 0 1; }
    EditorScreen .editor-toolbar Button { margin: 0 1 0 0; }
    EditorScreen .editor-status {
        height: auto;
        padding: 0 1;
        background: $boost;
        color: $text;
    }
    EditorScreen RichLog#editor-output {
        height: 12;
        border: solid $border;
        background: $panel;
    }
    EditorScreen .prompt-box {
        width: 60%;
        height: auto;
        border: solid $border;
        background: $surface;
        padding: 1 2;
    }
    EditorScreen .prompt-title { margin-bottom: 1; }
    EditorScreen .prompt-buttons { height: auto; margin-top: 1; }
    """

    BINDINGS = [
        Binding("ctrl+s", "save", "Save"),
        Binding("ctrl+r", "run_file", "Run File"),
        Binding("ctrl+t", "git_status", "Git Status"),
        Binding("ctrl+d", "git_diff", "Git Diff"),
        Binding("ctrl+g", "show_diff", "Diff vs Saved"),
        Binding("ctrl+f", "grep", "Grep"),
        Binding("ctrl+w", "send_swarm", "Swarm"),
        Binding("escape", "close", "Close", priority=True),
    ]

    def __init__(self, rel_path: str, workspace_root: Path) -> None:
        super().__init__()
        self.rel_path = rel_path
        self.workspace_root = workspace_root
        self._original: str = ""

    def compose(self) -> ComposeResult:
        abs_path = (self.workspace_root / self.rel_path).as_posix()
        yield Static(f"[bold]Editing:[/] {self.rel_path or '(workspace root)'}  [dim]{abs_path}[/]", classes="editor-header")
        yield TextArea(id="editor-text", language="plaintext")
        with Horizontal(classes="editor-toolbar"):
            yield Button("Save (^S)", id="btn-save", variant="primary")
            yield Button("Run (^R)", id="btn-run")
            yield Button("Cmd", id="btn-cmd")
            yield Button("Diff (^G)", id="btn-diff")
            yield Button("Git diff (^D)", id="btn-gitdiff")
            yield Button("Status (^T)", id="btn-status")
            yield Button("Grep (^F)", id="btn-grep")
            yield Button("Swarm (^W)", id="btn-swarm", variant="success")
            yield Button("Close", id="btn-close")
        yield Static("Ready", id="editor-status", classes="editor-status")
        yield RichLog(id="editor-output", markup=True, highlight=True, max_lines=2000)

    async def on_mount(self) -> None:
        self.run_worker(self._load(), exclusive=True)

    # ── Loading ──────────────────────────────────────────────────────────

    async def _load(self) -> None:
        # Import lazily to avoid import cycles with kazma_core / the TUI app.
        from kazma_core.ide.service import get_ide_service

        self._set_status(f"Loading {self.rel_path or '(root)'}…")
        try:
            result = await get_ide_service().read_file(self.rel_path)
        except Exception as exc:  # backend raised unexpectedly
            logger.exception("IDE read_file failed")
            self._set_status(f"[#ef4444]Error: {exc}[/]")
            self._write_output(f"[#ef4444]Failed to read file: {exc}[/]")
            return

        if not result.get("ok"):
            self._set_status(f"[#ef4444]{result.get('error', 'read failed')}[/]")
            self._write_output(f"[#ef4444]{result.get('error', 'read failed')}[/]")
            return

        content = result.get("content", "")
        self._original = content
        text_area = self.query_one("#editor-text", TextArea)
        text_area.load_text(content)

        lang = result.get("lang", "plaintext")
        if lang in _KNOWN_LANGS:
            try:
                text_area.language = lang
            except Exception as exc:  # language not registered in this build
                logger.debug("TextArea language unsupported: %s (%s)", lang, exc)
        self._set_status(f"Loaded {result.get('lines', 0)} lines · lang={lang}")

    # ── Actions ──────────────────────────────────────────────────────────

    def action_save(self) -> None:
        self.run_worker(self._save(), exclusive=False)

    async def _save(self) -> None:
        from kazma_core.ide.service import get_ide_service

        text = self.query_one("#editor-text", TextArea).text
        self._set_status("Saving…")
        try:
            result = await get_ide_service().write_file(self.rel_path, text)
        except Exception as exc:
            logger.exception("IDE write_file failed")
            self._set_status(f"[#ef4444]Error: {exc}[/]")
            self._write_output(f"[#ef4444]Save failed: {exc}[/]")
            return
        self._original = text
        if result.get("ok"):
            self._set_status(f"[#22c55e]Saved[/] · {result.get('output', '')}")
            self._write_output(f"[#22c55e]Saved {self.rel_path}[/]")
        else:
            self._set_status(f"[#ef4444]{result.get('error', 'save failed')}[/]")
            self._write_output(f"[#ef4444]{result.get('error', 'save failed')}[/]")

    def action_run_file(self) -> None:
        self.run_worker(self._run_file(), exclusive=False)

    async def _run_file(self) -> None:
        from kazma_core.ide.service import get_ide_service

        self._set_status("Running file…")
        try:
            result = await get_ide_service().run_file(self.rel_path)
        except Exception as exc:
            logger.exception("IDE run_file failed")
            self._set_status(f"[#ef4444]Error: {exc}[/]")
            self._write_output(f"[#ef4444]Run failed: {exc}[/]")
            return
        self._show_run_result(result)

    def action_git_status(self) -> None:
        self.run_worker(self._git("status --short"), exclusive=False)

    def action_git_diff(self) -> None:
        self.run_worker(self._git("diff"), exclusive=False)

    def action_show_diff(self) -> None:
        """Show a unified diff of the editor vs the last-saved content.

        Uses ``IdeService.diff`` (pure, no tool/HITL call). This is distinct
        from ``action_git_diff`` (``Ctrl+D``), which runs ``git diff``.
        """
        self.run_worker(self._show_diff(), exclusive=False)

    async def _show_diff(self) -> None:
        from kazma_core.ide.service import get_ide_service

        current = self.query_one("#editor-text", TextArea).text
        self._set_status("Diffing…")
        try:
            result = await get_ide_service().diff(self.rel_path, self._original, current)
        except Exception as exc:
            logger.exception("IDE diff failed")
            self._set_status(f"[#ef4444]Error: {exc}[/]")
            self._write_output(f"[#ef4444]Diff failed: {exc}[/]")
            return
        # Adapt the diff() result shape ({diff, changed}) to the output
        # shape _show_run_result expects ({output}).
        adapted = {
            "ok": bool(result.get("ok")),
            "output": result.get("diff") or ("(no changes)" if not result.get("changed") else ""),
            "error": result.get("error"),
        }
        self._show_run_result(adapted, title=f"diff: {self.rel_path}")

    async def _git(self, subcommand: str) -> None:
        from kazma_core.ide.service import get_ide_service

        self._set_status(f"git {subcommand}…")
        try:
            result = await get_ide_service().git(subcommand)
        except Exception as exc:
            logger.exception("IDE git failed")
            self._set_status(f"[#ef4444]Error: {exc}[/]")
            self._write_output(f"[#ef4444]git {subcommand} failed: {exc}[/]")
            return
        self._show_run_result(result, title=f"git {subcommand}")

    def action_grep(self) -> None:
        async def _on_pattern(pattern: str) -> None:
            if not pattern:
                return
            await self._grep(pattern)

        self.app.push_screen(
            PromptScreen("Grep pattern (regex):", placeholder="e.g. def .*\\("),
            lambda pattern: self.run_worker(_on_pattern(pattern), exclusive=False),
        )

    async def _grep(self, pattern: str) -> None:
        from kazma_core.ide.service import get_ide_service

        self._set_status(f"grep {pattern}…")
        try:
            result = await get_ide_service().search(pattern, glob="*", limit=100)
        except Exception as exc:
            logger.exception("IDE search failed")
            self._set_status(f"[#ef4444]Error: {exc}[/]")
            self._write_output(f"[#ef4444]grep failed: {exc}[/]")
            return
        if not result.get("ok"):
            self._set_status(f"[#ef4444]{result.get('error', 'grep failed')}[/]")
            self._write_output(f"[#ef4444]{result.get('error')}[/]")
            return
        matches = result.get("matches", [])
        self._set_status(f"[#22c55e]{len(matches)} match(es)[/]")
        self._write_output(f"[bold $primary]grep: {pattern}[/]")
        if matches:
            for line in matches[:100]:
                self._write_output(line)
        else:
            self._write_output("[dim]no matches[/]")

    def action_send_swarm(self) -> None:
        self.run_worker(self._send_swarm(), exclusive=False)

    async def _send_swarm(self) -> None:
        from kazma_core.ide.service import get_ide_service

        instruction = f"Edit and improve {self.rel_path or 'the workspace'}"
        self._set_status("Dispatching to swarm…")
        try:
            result = await get_ide_service().send_to_swarm(instruction, pattern="auto")
        except Exception as exc:
            logger.exception("IDE send_to_swarm failed")
            self._set_status(f"[#ef4444]Error: {exc}[/]")
            self._write_output(f"[#ef4444]Swarm dispatch failed: {exc}[/]")
            return
        if result.get("ok"):
            task_id = result.get("task_id")
            self._set_status(f"[#22c55e]Swarm task {task_id}[/]")
            self._write_output(f"[#22c55e]Dispatched swarm task {task_id}[/]")
        else:
            self._set_status(f"[#ef4444]{result.get('error', 'swarm failed')}[/]")
            self._write_output(f"[#ef4444]{result.get('error')}[/]")

    def action_close(self) -> None:
        text = self.query_one("#editor-text", TextArea).text
        if text != self._original and not getattr(self, "_confirm_close", False):
            # First Esc with unsaved changes — warn, require a second Esc.
            self._confirm_close = True  # type: ignore[attr-defined]
            self._set_status("[#f59e0b]Unsaved changes — press Esc again to discard & close[/]")
            return
        self.dismiss()

    def action_run_cmd(self) -> None:
        async def _on_cmd(cmd: str) -> None:
            if not cmd:
                return
            await self._run(cmd)

        self.app.push_screen(
            PromptScreen("Run command:", placeholder="e.g. pytest -q"),
            lambda cmd: self.run_worker(_on_cmd(cmd), exclusive=False),
        )

    async def _run(self, command: str) -> None:
        from kazma_core.ide.service import get_ide_service

        self._set_status(f"running: {command}…")
        try:
            result = await get_ide_service().run(command)
        except Exception as exc:
            logger.exception("IDE run failed")
            self._set_status(f"[#ef4444]Error: {exc}[/]")
            self._write_output(f"[#ef4444]Run failed: {exc}[/]")
            return
        self._show_run_result(result, title=command)

    # ── Button wiring ────────────────────────────────────────────────────

    def on_button_pressed(self, event: Button.Pressed) -> None:
        mapping = {
            "btn-save": self.action_save,
            "btn-run": self.action_run_file,
            "btn-cmd": self.action_run_cmd,
            "btn-diff": self.action_show_diff,
            "btn-gitdiff": self.action_git_diff,
            "btn-status": self.action_git_status,
            "btn-grep": self.action_grep,
            "btn-swarm": self.action_send_swarm,
            "btn-close": self.action_close,
        }
        handler = mapping.get(event.button.id)
        if handler:
            handler()

    # ── Helpers ──────────────────────────────────────────────────────────

    def _set_status(self, message: str) -> None:
        try:
            self.query_one("#editor-status", Static).update(message)
        except Exception:
            logger.debug("status widget missing")

    def _write_output(self, message: str) -> None:
        try:
            self.query_one("#editor-output", RichLog).write(message)
        except Exception:
            logger.debug("output log missing")

    def _show_run_result(self, result: dict, title: str = "output") -> None:
        ok = result.get("ok")
        output = result.get("output", "")
        error = result.get("error")
        if ok:
            self._set_status(f"[#22c55e]{title}: ok[/]")
            self._write_output(f"[bold $primary]{title}[/]")
            self._write_output(output or "[dim](no output)[/]")
        else:
            self._set_status(f"[#ef4444]{title}: {error}[/]")
            self._write_output(f"[#ef4444]{title} failed[/]")
            self._write_output(error or output or "[dim](no output)[/]")
