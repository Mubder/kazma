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
        self._pulse_timer = None
        self._busy: bool = False

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
            # Stop any existing timer before creating a new one
            if self._pulse_timer is not None:
                self._pulse_timer.stop()
            self._pulse_timer = self.set_interval(0.3, self._pulse_progress)
        else:
            # Stop the timer when hiding the progress bar
            if self._pulse_timer is not None:
                self._pulse_timer.stop()
                self._pulse_timer = None

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
            self._busy = False
            # Re-enable input
            try:
                self.query_one("#chat-input", Input).disabled = False
            except Exception:
                pass

    # ── Input handling ─────────────────────────────────────────────

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return
        # Block input while a response is being generated
        if self._busy:
            return
        event.input.clear()
        if text.startswith("/"):
            self._handle_command(text)
        elif self._is_swarm_mention(text):
            self.write("user", text)
            self._busy = True
            event.input.disabled = True
            self.app.call_later(self._handle_swarm_command, text)
        else:
            self.write("user", text)
            self._busy = True
            event.input.disabled = True
            self.app.call_later(self._generate_response, text)

    @staticmethod
    def _is_swarm_mention(text: str) -> bool:
        """Detect bare 'swarm' mentions (not just /swarm commands).

        Matches patterns like:
            "swarm: do X"
            "use the swarm to do X"
            "swarm analyze Y"
        But NOT words that contain "swarm" as a substring (e.g. "swarmitude").
        """
        import re
        return bool(re.search(r'\bswarm\b', text, re.IGNORECASE))

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
        """Handle /swarm commands and bare 'swarm' mentions in the TUI chat.

        Subcommands (with /swarm prefix):
            /swarm                — show help
            /swarm status         — show swarm status
            /swarm list           — list workers
            /swarm <task>         — auto-route and dispatch
            /swarm <worker> <task>— dispatch to specific worker
            /swarm broadcast <task>— all workers

        Bare mentions (no /swarm prefix):
            "swarm: do X"         — dispatch as task
            "use the swarm to Y"  — dispatch as task
            "swarm analyze Z"     — dispatch as task
        """
        import re

        # Determine if this is a /swarm command or a bare mention
        is_slash = text.lower().startswith("/swarm")

        if is_slash:
            parts = text.split(None, 2)  # ["/swarm", sub, rest]
            if len(parts) < 2:
                self.write("system",
                    "Swarm Commands:\n"
                    "  /swarm <task> — auto-route to best worker\n"
                    "  /swarm <worker> <task> — dispatch to one worker\n"
                    "  /swarm broadcast <task> — all workers\n"
                    "  /swarm status — show swarm status\n"
                    "  /swarm list — list workers")
                return
            sub = parts[1].lower()
            task_body = parts[2] if len(parts) > 2 else ""
        else:
            # Bare mention: strip "swarm" keyword and treat the rest as a task
            sub = ""
            # Remove "swarm" (and optional colon) from the start or middle
            task_body = re.sub(r'\bswarm\b\s*:?\s*', '', text, count=1, flags=re.IGNORECASE).strip()
            if not task_body:
                self.write("system",
                    "Swarm Commands:\n"
                    "  /swarm <task> — auto-route to best worker\n"
                    "  /swarm <worker> <task> — dispatch to one worker\n"
                    "  /swarm broadcast <task> — all workers\n"
                    "  /swarm status — show swarm status\n"
                    "  /swarm list — list workers\n"
                    "  Or just say: swarm <task>")
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

        # sub and task_body are already set above (in the is_slash / else block)

        # ── Known subcommands (only for /swarm prefix) ──────────────
        if is_slash:
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

            # /swarm <worker> <task>
            if sub in [n.lower() for n in engine.worker_names]:
                if not task_body:
                    self.write("error", f"Usage: /swarm {sub} <task>")
                    return
                await self._dispatch_swarm(task_body, engine, worker_name=sub)
                return

            # /swarm <task> — auto-route
            task = text[len("/swarm "):].strip()
            if not task:
                self.write("error", "Usage: /swarm <task>")
                return
            await self._dispatch_swarm(task, engine)
            return

        # Bare mention: dispatch the extracted task body
        await self._dispatch_swarm(task_body, engine)

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
            from kazma_core.swarm.router import NoCapableWorkersError

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
                    type=TaskType.DISPATCH,
                    prompt=task,
                    workers=[worker_name],
                )
            else:
                # Auto-route: let the engine pick the best worker.
                # Must use ["auto"] not [] — the engine checks for
                # ["auto"] to trigger CapabilityRouter.
                swarm_task = SwarmTask(
                    id=f"tui-swarm-{task[:20]}",
                    type=TaskType.DISPATCH,
                    prompt=task,
                    workers=["auto"],
                )

            try:
                result = await engine.dispatch(swarm_task)
            except NoCapableWorkersError:
                # No worker's capabilities matched the task keywords.
                # Fall back to the first available worker.
                names = engine.worker_names
                if not names:
                    self.write("error", "No workers registered. Add workers via the Web UI Swarm panel.")
                    return
                self.write("system", f"No keyword match — falling back to '{names[0]}'.")
                swarm_task.workers = [names[0]]
                result = await engine.dispatch(swarm_task)

            # The engine catches NoCapableWorkersError internally and returns
            # a TaskResult with status="failed" and error="No capable workers..."
            # So also check the result for that error and retry with first worker.
            if (not broadcast and not worker_name
                    and getattr(result, "status", "") == "failed"
                    and "No capable workers" in (getattr(result, "error", "") or "")):
                names = engine.worker_names
                if names:
                    self.write("system", f"No keyword match — falling back to '{names[0]}'.")
                    swarm_task.workers = [names[0]]
                    result = await engine.dispatch(swarm_task)

            # TaskResult uses aggregated_output/synthesized_output, not output
            output = (
                getattr(result, "aggregated_output", None)
                or getattr(result, "synthesized_output", None)
                or ""
            )
            if not output and result and getattr(result, "worker_results", None):
                # Fall back to first worker's output
                output = getattr(result.worker_results[0], "output", "") or ""

            if output:
                self._last_response = output
                self.write("assistant", output)
            elif result and getattr(result, "error", None):
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

    def copy_to_clipboard(self) -> bool:
        """Copy currently selected text or last KAZMA response to system clipboard.

        Returns True if something was copied, False otherwise.
        """
        try:
            selected = self.screen.get_selected_text()
            if selected:
                self.app.copy_to_clipboard(selected)
                return True
        except Exception:
            pass
        # Fallback: copy the last tracked KAZMA response
        if self._last_response:
            self.app.copy_to_clipboard(self._last_response)
            return True
        return False
