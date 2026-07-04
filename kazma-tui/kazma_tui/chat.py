"""Chat panel — RichLog + ProgressBar + Input + token-by-token streaming."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Input, ProgressBar, RichLog

logger = logging.getLogger(__name__)

ROLE_HEX: dict[str, str] = {
    "user": "#e6edf3",
    "assistant": "#a855f7",
    "tool": "#f59e0b",
    "system": "#8b949e",
    "error": "#ef4444",
    "thinking": "#22d3ee",
}


class ChatPanel(Vertical):
    """Chat: RichLog + ProgressBar + Input. Supports token-by-token streaming."""

    ALLOW_SELECT = True

    DEFAULT_CSS = """
    ChatPanel { height: 1fr; border: solid $border; background: $surface; }
    ChatPanel > RichLog { height: 1fr; background: transparent; border: none; padding: 1 2; }
    ChatPanel > ProgressBar { height: 1; margin: 0 2; }
    ChatPanel > Input {
        dock: bottom; height: 3; margin: 1 2;
        background: $panel; border: solid $border; color: $text;
    }
    ChatPanel > Input:focus { border: solid $primary; }
    """

    BINDINGS = [
        ("ctrl+a", "select_all", "Select All"),
        ("shift+enter", "insert_newline", "Newline"),
        ("ctrl+enter", "insert_newline", "Newline"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._last_response: str = ""

    def compose(self) -> ComposeResult:
        yield RichLog(id="chat-log", highlight=True, markup=True, wrap=True, auto_scroll=True)
        yield ProgressBar(id="chat-progress", total=100, show_eta=False)
        yield Input(placeholder="Type... /help for commands", id="chat-input")

    # ── Message display ────────────────────────────────────────────

    def write(self, role: str, text: str) -> None:
        """Write a message to the chat log with role prefix."""
        log = self.query_one("#chat-log", RichLog)
        ts = datetime.now().strftime("%H:%M")
        c = ROLE_HEX.get(role, "#8b949e")
        log.write(f"[dim]{ts}[/] [{c}]▌ {role.upper()}[/] {text}")

    def add_message(self, role: str, text: str) -> None:
        """Alias for write() - adds a message to the chat log."""
        self.write(role, text)

    def show_progress(self, visible: bool) -> None:
        bar = self.query_one(ProgressBar)
        bar.display = visible
        if visible:
            bar.update(progress=0)
            self._pulse_timer = self.set_interval(0.3, self._pulse_progress)

    def _pulse_progress(self) -> None:
        bar = self.query_one(ProgressBar)
        if bar.display:
            bar.advance(5)
            if bar.progress >= 100:
                bar.update(progress=0)

    # ── Streaming ──────────────────────────────────────────────────

    async def write_stream(self, prompt: str) -> None:
        """Send prompt to provider and write response to RichLog."""
        log = self.query_one("#chat-log", RichLog)
        ts = datetime.now().strftime("%H:%M")
        log.write(f"[dim]{ts}[/] [#a855f7]▌ KAZMA[/] ")
        self.show_progress(True)

        try:
            from kazma_core.model_registry import get_model_registry

            try:
                registry = get_model_registry()
                provider = registry.get_client()
            except RuntimeError:
                log.write(
                    "\n[#ef4444]Error: ModelRegistry not initialized. "
                    "Start the kazma-ui server first, or run "
                    "kazma_core.bootstrap.initialize().[/]"
                )
                return
            if provider is None:
                log.write(
                    "\n[#ef4444]Error: No LLM provider configured. "
                    "Add a provider via /models in the chat, or via kazma.yaml.[/]"
                )
                return

            messages = [{"role": "user", "content": prompt}]
            # Inject system prompt from kazma.yaml so the model knows to
            # respond in the user's language and follow Kazma's persona.
            system_prompt = self._get_system_prompt()
            if system_prompt:
                messages.insert(0, {"role": "system", "content": system_prompt})
            response = await provider.chat(messages)
            content = getattr(response, "content", "") or ""
            if content:
                self._last_response = content
                log.write(content)
            else:
                log.write("[dim](empty response)[/]")
        except Exception as e:
            log.write(f"\n[#ef4444]Error: {e}[/]")
        finally:
            self.show_progress(False)

    # ── Input handling ─────────────────────────────────────────────

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return
        event.input.clear()
        if text.startswith("/"):
            self._handle_command(text)
        else:
            self.write("user", text)
            self.app.call_later(self._generate_response, text)

    def _handle_command(self, text: str) -> None:
        cmd = text.lower().split()[0]
        if cmd == "/help":
            self.write("system", "Commands: /help /clear /model /swarm /quit | Copy: Ctrl+A then Ctrl+Shift+C, or Shift+drag mouse to select text")
        elif cmd == "/clear":
            self.query_one("#chat-log", RichLog).clear()
        elif cmd == "/quit":
            self.app.exit()
        elif cmd in ("/model", "/models"):
            try:
                from kazma_core.settings.model_registry import get_model_list_text
                self.write("system", get_model_list_text("tui"))
            except Exception as e:
                self.write("error", f"Model registry: {e}")
        elif cmd == "/swarm":
            self.app.call_later(self._handle_swarm_command, text)
        else:
            self.write("system", f"Unknown: {cmd}")

    async def _handle_swarm_command(self, text: str) -> None:
        """Handle /swarm commands in the TUI chat.

        Subcommands:
            /swarm                — show help
            /swarm status         — show swarm status
            /swarm list           — list workers
            /swarm <task>         — auto-route and dispatch
            /swarm <worker> <task>— dispatch to specific worker
            /swarm broadcast <task>— all workers
        """
        parts = text.split(None, 2)
        if len(parts) < 2:
            self.write("system",
                "Swarm Commands:\n"
                "  /swarm <task> — auto-route to best worker\n"
                "  /swarm <worker> <task> — dispatch to one worker\n"
                "  /swarm broadcast <task> — all workers\n"
                "  /swarm status — show swarm status\n"
                "  /swarm list — list workers")
            return

        try:
            from kazma_core.swarm import get_swarm_engine
        except Exception:
            self.write("error", "Swarm engine not available.")
            return

        engine = get_swarm_engine()
        if engine is None:
            self.write("error", "Swarm engine not initialized.")
            return

        sub = parts[1].lower()
        task_body = parts[2] if len(parts) > 2 else ""

        # /swarm status
        if sub == "status":
            names = engine.worker_names
            lines = [f"Swarm Status ({len(names)} workers):"]
            for name in names:
                w = engine.get_worker(name)
                model = getattr(w, "model", "") or "?"
                lines.append(f"  {name} [{model}]")
            if not names:
                lines.append("  (no workers registered)")
            self.write("system", "\n".join(lines))
            return

        # /swarm list
        if sub == "list":
            names = engine.worker_names
            if not names:
                self.write("system", "No workers registered. Add workers via the Web UI Swarm panel.")
            else:
                lines = [f"Workers ({len(names)}):"]
                for name in names:
                    w = engine.get_worker(name)
                    role = getattr(w, "role", "") or ""
                    model = getattr(w, "model", "") or ""
                    lines.append(f"  {name}" + (f" ({role})" if role else "") + (f" [{model}]" if model else ""))
                self.write("system", "\n".join(lines))
            return

        # /swarm broadcast <task>
        if sub == "broadcast":
            if not task_body:
                self.write("error", "Usage: /swarm broadcast <task>")
                return
            await self._dispatch_swarm(task_body, engine, broadcast=True)
            return

        # /swarm <worker> <task>  OR  /swarm <task>
        # Check if the first word is a worker name
        if sub in [n.lower() for n in engine.worker_names]:
            if not task_body:
                self.write("error", f"Usage: /swarm {sub} <task>")
                return
            await self._dispatch_swarm(task_body, engine, worker_name=sub)
            return

        # Otherwise treat the whole thing as a task for auto-routing
        task = text[len("/swarm "):].strip()
        if not task:
            self.write("error", "Usage: /swarm <task>")
            return
        await self._dispatch_swarm(task, engine)

    async def _dispatch_swarm(
        self,
        task: str,
        engine: Any,
        worker_name: str = "",
        broadcast: bool = False,
    ) -> None:
        """Dispatch a task to the swarm engine and show the result."""
        from kazma_core.swarm.task import SwarmTask, TaskType

        self.write("system", f"Dispatching to swarm...")
        try:
            if broadcast:
                swarm_task = SwarmTask(
                    id=f"tui-swarm-{task[:20]}",
                    type=TaskType.BROADCAST,
                    prompt=task,
                    workers=[],
                )
            elif worker_name:
                swarm_task = SwarmTask(
                    id=f"tui-swarm-{task[:20]}",
                    type=TaskType.SINGLE,
                    prompt=task,
                    workers=[worker_name],
                )
            else:
                # Auto-route: let the engine pick the best worker
                swarm_task = SwarmTask(
                    id=f"tui-swarm-{task[:20]}",
                    type=TaskType.SINGLE,
                    prompt=task,
                    workers=[],
                )

            result = await engine.dispatch(swarm_task)

            if result and result.output:
                self._last_response = result.output
                self.write("assistant", result.output)
            elif result and result.error:
                self.write("error", f"Swarm error: {result.error}")
            else:
                self.write("system", "Swarm task completed (no output).")
        except Exception as exc:
            self.write("error", f"Swarm dispatch failed: {exc}")

    async def _generate_response(self, prompt: str) -> None:
        await self.write_stream(prompt)

    @staticmethod
    def _get_system_prompt() -> str:
        """Load the system prompt from kazma.yaml or ConfigStore.

        The TUI chat is a direct LLM call (no LangGraph supervisor),
        so we must inject the system prompt ourselves to ensure the
        model follows Kazma's persona and language-matching rules.
        """
        try:
            from kazma_core.config_store import get_config_store
            cs = get_config_store()
            prompt = cs.get("system_prompt")
            if prompt:
                return str(prompt)
        except Exception:
            pass
        # Fallback: read directly from kazma.yaml
        try:
            from pathlib import Path
            import yaml
            yaml_path = Path("kazma.yaml")
            if yaml_path.exists():
                with open(yaml_path, encoding="utf-8") as fh:
                    data = yaml.safe_load(fh) or {}
                prompt = data.get("system_prompt")
                if prompt:
                    return str(prompt)
        except Exception:
            pass
        return ""

    # ── Copy ───────────────────────────────────────────────────────

    def action_select_all(self) -> None:
        """Select all text in the chat log."""
        try:
            self.query_one("#chat-log", RichLog).text_select_all()
        except Exception:
            pass

    def action_insert_newline(self) -> None:
        """Insert a newline character at the cursor in the chat input.

        Required so users can compose multi-line prompts without sending
        them prematurely on Enter.
        """
        try:
            chat_input = self.query_one("#chat-input", Input)
            chat_input.insert("\n")
        except Exception:
            pass

    def copy_to_clipboard(self) -> None:
        """Copy currently selected text or last KAZMA response to system clipboard.

        Tries screen-level text selection first (from mouse drag or
        Ctrl+A).  Falls back to the last assistant response tracked in
        _last_response, since RichLog has no .text property to read back.
        """
        try:
            selected = self.screen.get_selected_text()
            if selected:
                self.app.copy_to_clipboard(selected)
                return
        except Exception:
            pass
        # Fallback: copy the last tracked KAZMA response
        if self._last_response:
            self.app.copy_to_clipboard(self._last_response)
            return
