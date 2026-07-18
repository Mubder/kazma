"""Chat panel — RichLog + ProgressBar + Input + token-by-token streaming."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Input, ListItem, ListView, ProgressBar, RichLog, Static

__all__ = ["ChatPanel", "ROLE_HEX"]

logger = logging.getLogger(__name__)

ROLE_HEX: dict[str, str] = {
    "user": "#e6edf3",
    "assistant": "#c084fc",
    "tool": "#f59e0b",
    "system": "#8b949e",
    "error": "#ef4444",
    "thinking": "#56b6c2",
}


class ChatPanel(Vertical):
    """Chat: RichLog + ProgressBar + Input. Supports token-by-token streaming."""

    ALLOW_SELECT = True

    SLASH_COMMANDS = [
        ("/help", "Show available commands"),
        ("/clear", "Clear chat history"),
        ("/reset", "Reset conversation context"),
        ("/model [set <name>]", "Show/switch active model (interactive picker)"),
        ("/models", "Alias for /model"),
        ("/status", "Gateway health overview"),
        ("/memory", "Memory store stats"),
        ("/cost", "Session token spend"),
        ("/context", "Context window usage"),
        ("/personality [list|<name>]", "Show/switch personality"),
        ("/config", "Interactive config wizard"),
        ("/replay [list|clear|<n>]", "Time travel: list/replay snapshots"),
        ("/export", "Export session to file"),
        ("/swarm [status|list|<task>]", "Swarm dispatch and management"),
        ("/quit", "Exit Kazma TUI"),
    ]

    DEFAULT_CSS = """
    ChatPanel { height: 1fr; border: solid $border; background: $surface; }
    ChatPanel > RichLog { height: 1fr; background: transparent; border: none; padding: 1 2; }
    ChatPanel > ProgressBar { height: 1; margin: 0 2; }
    ChatPanel > Input {
        dock: bottom; height: 3; margin: 1 2;
        background: $panel; border: solid $border; color: $text;
    }
    ChatPanel > Input:focus { border: solid $primary; }

    ChatPanel > #autocomplete {
        dock: bottom; offset: 0 -4;
        width: auto; min-width: 30; max-height: 18;
        background: $panel; border: solid $primary;
        display: none;
    }
    ChatPanel > #autocomplete ListItem {
        padding: 0 1;
        height: auto;
    }
    ChatPanel > #autocomplete ListItem.-highlight {
        background: $primary 20%;
    }
    ChatPanel > #autocomplete .ac-cmd { color: $primary; text-style: bold; }
    ChatPanel > #autocomplete .ac-desc { color: $text-muted; }
    """

    BINDINGS = [
        ("ctrl+a", "select_all", "Select All"),
        ("shift+enter", "insert_newline", "Newline"),
        ("ctrl+enter", "insert_newline", "Newline"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._last_response: str = ""
        self._messages: list[dict[str, Any]] = []
        self._pulse_timer = None
        self._busy: bool = False
        self._ac_matches: list[tuple[str, str]] = []
        self._ac_index: int = 0
        self._model_cache: list[str] = []

    def compose(self) -> ComposeResult:
        yield RichLog(id="chat-log", highlight=True, markup=True, wrap=True, auto_scroll=True, max_lines=500)
        yield ProgressBar(id="chat-progress", total=100, show_eta=False)
        yield Input(placeholder="Type... / for commands", id="chat-input")
        yield ListView(id="autocomplete")

    # ── Slash command autocomplete ─────────────────────────────────
    def on_input_changed(self, event: Input.Changed) -> None:
        """Show autocomplete suggestions when the user types /."""
        if event.input.id != "chat-input":
            return
        val = event.value
        ac = self.query_one("#autocomplete", ListView)

        if not val.startswith("/"):
            ac.display = False
            self._ac_matches = []
            return

        parts = val.split(None, 2)
        cmd = parts[0].lower() if parts else val

        # /model set <partial> → show matching models
        if cmd in ("/model", "/models") and len(parts) >= 2 and parts[1].lower() == "set":
            partial = parts[2] if len(parts) > 2 else ""
            if not self._model_cache:
                self._refresh_model_cache()
            matches = [
                (m, "")
                for m in self._model_cache
                if partial.lower() in m.lower()
            ]
            self._ac_matches = matches[:15]
            self._ac_index = 0
            self._populate_ac_list(ac)
            return

        # Default: match slash commands
        partial = parts[0] if parts else val
        matches = [(c, d) for c, d in self.SLASH_COMMANDS if c.startswith(partial)]
        self._ac_matches = matches
        self._ac_index = 0
        self._populate_ac_list(ac)

    def _populate_ac_list(self, ac: ListView) -> None:
        """Fill the autocomplete ListView with current matches."""
        ac.clear()
        if not self._ac_matches:
            ac.display = False
            return
        for cmd, desc in self._ac_matches:
            if desc:
                label = f" [bold $primary]{cmd}[/]  [dim]{desc}[/]"
            else:
                label = f" [bold $primary]{cmd}[/]"
            ac.append(ListItem(Static(label)))
        ac.display = True
        # Highlight the current index
        if self._ac_index < len(self._ac_matches):
            ac.index = self._ac_index

    def _refresh_model_cache(self) -> None:
        """Load available model names for autocomplete."""
        try:
            from kazma_core.settings.model_registry import get_universal_models
            self._model_cache = [m["name"] for m in get_universal_models()]
        except Exception:
            self._model_cache = []

    def on_key(self, event) -> None:
        """Handle Tab/Arrow keys for autocomplete navigation."""
        ac = self.query_one("#autocomplete", ListView)
        if not ac.display or not self._ac_matches:
            return

        if event.key in ("tab", "down"):
            self._ac_index = (self._ac_index + 1) % len(self._ac_matches)
            ac.index = self._ac_index
            event.prevent_default()
        elif event.key == "up":
            self._ac_index = (self._ac_index - 1) % len(self._ac_matches)
            ac.index = self._ac_index
            event.prevent_default()
        elif event.key == "enter" and self._ac_matches:
            idx = min(self._ac_index, len(self._ac_matches) - 1)
            self._apply_ac_match(idx)
            ac.display = False
            self._ac_matches = []
            event.prevent_default()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Mouse click on autocomplete item = select it."""
        ac = self.query_one("#autocomplete", ListView)
        if event.list_view is not ac:
            return
        idx = event.index if event.index is not None else self._ac_index
        if idx is not None and 0 <= idx < len(self._ac_matches):
            self._apply_ac_match(idx)
            ac.display = False
            self._ac_matches = []

    def _apply_ac_match(self, idx: int) -> None:
        """Fill the input with the selected autocomplete match."""
        if idx < 0 or idx >= len(self._ac_matches):
            return
        match_text = self._ac_matches[idx][0]
        inp = self.query_one("#chat-input", Input)

        # Check if we're in model set mode
        parts = inp.value.split(None, 2)
        if len(parts) >= 2 and parts[0].lower() in ("/model", "/models") and parts[1].lower() == "set":
            inp.value = f"/model set {match_text}"
        else:
            inp.value = match_text + " "
        inp.cursor_position = len(inp.value)
        inp.focus()

    # ── Message display ────────────────────────────────────────────

    def write(self, role: str, text: str) -> None:
        """Write a message to the chat log with role prefix."""
        log = self.query_one("#chat-log", RichLog)
        ts = datetime.now().strftime("%H:%M")
        c = ROLE_HEX.get(role, "#8b949e")
        # Escape Rich markup in user/LLM text to prevent injection
        from rich.text import Text
        log.write(Text.from_markup(f"[dim]{ts}[/] [{c}]▌ {role.upper()}[/] ") + Text(text))

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
        log.write(f"[dim]{ts}[/] [#c084fc]▌ KAZMA[/] ")
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
            self._messages.append({"role": "user", "content": prompt})
            # Inject system prompt from kazma.yaml so the model knows to
            # respond in the user's language and follow Kazma's persona.
            system_prompt = self._get_system_prompt()
            if system_prompt:
                messages.insert(0, {"role": "system", "content": system_prompt})
            response = await provider.chat(messages)
            content = getattr(response, "content", "") or ""
            if content:
                self._last_response = content
                self._messages.append({"role": "assistant", "content": content})
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
            except Exception as exc:
                logger.debug("Re-enable input failed: %s", exc)

    # ── Input handling ─────────────────────────────────────────────

    def _on_model_picked(self, model_name: str | None) -> None:
        """Callback when a model is selected from the picker."""
        if not model_name:
            return
        try:
            from kazma_core.model_registry import get_model_registry
            registry = get_model_registry()
            registry.set_active_model(model_name)
            self.write("system", f"Active model set to: {model_name}")
        except Exception as e:
            self.write("error", f"Failed to set model: {e}")
        try:
            self.query_one("#chat-input", Input).focus()
        except Exception:
            pass

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
            lines = ["Available commands:"]
            for c, d in self.SLASH_COMMANDS:
                lines.append(f"  {c:<14} {d}")
            lines.append("")
            lines.append("Tip: Type / and use Tab/arrows to autocomplete.")
            self.write("system", "\n".join(lines))
        elif cmd == "/clear":
            self.app.action_clear_chat()
        elif cmd == "/quit":
            self.app.exit()
        elif cmd in ("/model", "/models"):
            parts = text.split(None, 2)
            sub = parts[1].lower() if len(parts) > 1 else ""
            if sub == "set" and len(parts) > 2:
                model_name = parts[2].strip()
                try:
                    from kazma_core.model_registry import get_model_registry
                    registry = get_model_registry()
                    registry.set_active_model(model_name)
                    self.write("system", f"Active model set to: {model_name}")
                except Exception as e:
                    self.write("error", f"Failed to set model: {e}")
            else:
                try:
                    from kazma_core.model_registry import get_model_registry
                    registry = get_model_registry()
                    active = getattr(registry, "_active_model", "") or ""
                except Exception:
                    active = ""
                from kazma_tui.widgets.model_picker import ModelPicker
                self.app.push_screen(ModelPicker(active_model=active), self._on_model_picked)
        elif cmd == "/memory":
            self._cmd_memory()
        elif cmd == "/status":
            self._cmd_status()
        elif cmd == "/cost":
            self._cmd_cost()
        elif cmd == "/context":
            self._cmd_context()
        elif cmd == "/reset":
            self.write("system", "Conversation context reset.")
        elif cmd == "/personality":
            self._cmd_personality(text)
        elif cmd == "/config":
            self.write("system", "Config wizard available in the Settings tab.")
        elif cmd == "/replay":
            self._cmd_replay(text)
        elif cmd == "/export":
            self._cmd_export()
        elif cmd == "/swarm":
            self.app.call_later(self._handle_swarm_command, text)
        else:
            self.write("system", f"Unknown: {cmd}")

    def _cmd_memory(self) -> None:
        try:
            from kazma_core.memory.health import build_memory_health
            health = build_memory_health()
            status = health.get("status", "?")
            summary = health.get("summary", "")
            lines = [f"Memory Status: {status}", summary, ""]
            for c in health.get("components", []):
                icon = "+" if c.get("ok") else "-"
                lines.append(f"  [{icon}] {c['name']}: {c.get('status', '?')}")
            self.write("system", "\n".join(lines))
        except Exception as e:
            self.write("error", f"Memory health unavailable: {e}")

    def _cmd_status(self) -> None:
        try:
            from kazma_core.model_registry import get_model_registry
            registry = get_model_registry()
            provider = getattr(registry, "_active_provider", "") or "none"
            model = getattr(registry, "_active_model", "") or "none"
            lines = [
                "Gateway Status",
                f"  Provider: {provider}",
                f"  Model:    {model}",
            ]
            try:
                from kazma_core.swarm import get_swarm_engine
                engine = get_swarm_engine()
                if engine:
                    names = engine.worker_names
                    lines.append(f"  Workers:  {len(names)}")
                else:
                    lines.append("  Workers:  (swarm not initialized)")
            except Exception:
                lines.append("  Workers:  (unavailable)")
            self.write("system", "\n".join(lines))
        except Exception as e:
            self.write("error", f"Status unavailable: {e}")

    def _cmd_cost(self) -> None:
        try:
            from kazma_core.tracing import get_trace_store
            stats = get_trace_store().stats()
            cost = stats.get("total_cost", 0.0)
            tokens = stats.get("total_tokens", 0)
            llm_calls = stats.get("total_llm_calls", 0)
            tool_calls = stats.get("total_tool_calls", 0)
            uptime_s = stats.get("uptime_seconds", 0)
            uptime = f"{int(uptime_s // 60)}m {int(uptime_s % 60)}s"
            lines = [
                "Session Cost Report",
                f"  Total Cost:   ${cost:.4f}",
                f"  Total Tokens: {tokens:,}",
                f"  LLM Calls:    {llm_calls}",
                f"  Tool Calls:   {tool_calls}",
                f"  Uptime:       {uptime}",
            ]
            self.write("system", "\n".join(lines))
        except Exception as e:
            self.write("error", f"Cost tracking unavailable: {e}")

    def _cmd_context(self) -> None:
        try:
            from kazma_core.summarizer import estimate_tokens, TOKEN_THRESHOLD
            from kazma_core.config_store import get_config_store
            cs = get_config_store()
            window = cs.get("memory.max_context_tokens", 128_000)
            tokens = estimate_tokens(self._messages) if self._messages else 0
            pct = (tokens / window * 100) if window else 0
            bar_len = 20
            filled = int(bar_len * pct / 100)
            bar = "#" * filled + "-" * (bar_len - filled)
            lines = [
                "Context Window",
                f"  Tokens:    {tokens:,} / {window:,} ({pct:.1f}%)",
                f"  [{bar}]",
                f"  Messages:  {len(self._messages)}",
                f"  Threshold: {TOKEN_THRESHOLD:,} tokens (compaction at 80%)",
            ]
            self.write("system", "\n".join(lines))
        except Exception as e:
            self.write("error", f"Context info unavailable: {e}")

    def _cmd_personality(self, text: str = "/personality") -> None:
        try:
            from kazma_core.tools.personality_cmd import handle_personality_command
            from kazma_core.personalities import list_personalities, get_current_personality
            parts = text.strip().split()
            sub = parts[1].lower() if len(parts) > 1 else ""

            if not sub or sub == "current":
                # Show current personality
                p = get_current_personality()
                self.write("system", f"Current personality: {p.name} {p.emoji}\n{p.description}")
                # Also list available
                names = [f"{x.name} {x.emoji}" for x in list_personalities()]
                self.write("system", "\nAvailable: " + ", ".join(names) + "\nSwitch: /personality <name>")
            else:
                response = handle_personality_command(text)
                self.write("system", response)
        except Exception as e:
            self.write("error", f"Personality command failed: {e}")

    def _cmd_replay(self, text: str) -> None:
        parts = text.strip().split()
        sub = parts[1].lower() if len(parts) > 1 else ""

        if not sub:
            self.write("system",
                "Replay Commands:\n"
                "  /replay list          — show available snapshots\n"
                "  /replay <iteration>   — show snapshot details\n"
                "  /replay clear         — clear all snapshots")
            return

        try:
            from kazma_core.time_travel import SnapshotStore, DEFAULT_DB_PATH
            from pathlib import Path
            db_path = Path(DEFAULT_DB_PATH)
            if not db_path.exists():
                self.write("system", "No snapshots available (snapshot DB not found).")
                return
            store = SnapshotStore(str(db_path))
            thread_id = "tui-session"

            if sub == "list":
                records = store.list_for_thread(thread_id)
                if not records:
                    self.write("system", "No snapshots available for this session.")
                else:
                    lines = ["Available snapshots:", ""]
                    for rec in records:
                        lines.append(f"  Iteration {rec.iteration}  |  {rec.timestamp}  |  model={rec.model_used or '?'}")
                    self.write("system", "\n".join(lines))
            elif sub == "clear":
                count = store.clear_thread(thread_id)
                self.write("system", f"Cleared {count} snapshot(s) for this session.")
            else:
                try:
                    iteration = int(sub)
                except ValueError:
                    self.write("system", "Unknown sub-command. Use: /replay list, /replay clear, or /replay <number>")
                    return
                rec = store.get(thread_id, iteration)
                if rec is None:
                    self.write("system", f"No snapshot for iteration {iteration}.")
                else:
                    state = rec.get_state()
                    msg_count = len(state.get("messages", []))
                    self.write("system", f"Snapshot {rec.iteration}  |  {rec.timestamp}  |  model={rec.model_used or '?'}  |  {msg_count} messages")
            store.close()
        except Exception as e:
            self.write("error", f"Replay command failed: {e}")

    def _cmd_export(self) -> None:
        try:
            from datetime import datetime
            from pathlib import Path
            import json
            export_dir = Path("kazma-data/exports")
            export_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")

            # Markdown
            md_path = export_dir / f"chat_{ts}.md"
            lines = [f"# Kazma Chat Export", f"Date: {datetime.now().isoformat()}", ""]
            for msg in self._messages:
                role = msg.get("role", "unknown").upper()
                content = msg.get("content", "")
                lines.append(f"## {role}")
                lines.append("")
                lines.append(content)
                lines.append("")
            md_path.write_text("\n".join(lines), encoding="utf-8")

            # JSON
            json_path = export_dir / f"chat_{ts}.json"
            json_path.write_text(json.dumps(self._messages, indent=2, ensure_ascii=False), encoding="utf-8")

            self.write("system", f"Exported {len(self._messages)} messages to:\n  {md_path}\n  {json_path}")
        except Exception as e:
            self.write("error", f"Export failed: {e}")

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

        try:
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
        finally:
            self._busy = False
            try:
                self.query_one("#chat-input", Input).disabled = False
            except Exception:
                pass

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
            from kazma_core.swarm import NoCapableWorkersError

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
        except Exception as exc:
            logger.debug("ConfigStore system prompt read failed: %s", exc)
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
        except Exception as exc:
            logger.debug("YAML system prompt read failed: %s", exc)
        return ""

    # ── Copy ───────────────────────────────────────────────────────

    def action_select_all(self) -> None:
        """Select all text in the chat log."""
        try:
            self.query_one("#chat-log", RichLog).text_select_all()
        except Exception as exc:
            logger.debug("Select all failed: %s", exc)

    def action_insert_newline(self) -> None:
        """Insert a newline character at the cursor in the chat input.

        Required so users can compose multi-line prompts without sending
        them prematurely on Enter.
        """
        try:
            chat_input = self.query_one("#chat-input", Input)
            chat_input.insert("\n")
        except Exception as exc:
            logger.debug("Insert newline failed: %s", exc)

    def copy_to_clipboard(self) -> bool:
        """Copy currently selected text or last KAZMA response to system clipboard.

        Returns True if something was copied, False otherwise.
        """
        try:
            selected = self.screen.get_selected_text()
            if selected:
                self.app.copy_to_clipboard(selected)
                return True
        except Exception as exc:
            logger.debug("Copy selected text failed: %s", exc)
        # Fallback: copy the last tracked KAZMA response
        if self._last_response:
            self.app.copy_to_clipboard(self._last_response)
            return True
        return False
