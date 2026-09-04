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

# Conversation turns sent to the provider per chat (≈2 messages per turn).
# Bounds prompt size for long TUI sessions; older turns stay in
# self._messages for /context and /export.
_MAX_HISTORY_TURNS = 30


class ChatPanel(Vertical):
    """Chat: RichLog + ProgressBar + Input. Supports token-by-token streaming."""

    ALLOW_SELECT = True

    SLASH_COMMANDS = [
        ("/help", "Show available commands"),
        ("/clear", "Clear chat history"),
        ("/reset", "Reset conversation context"),
        ("/sessions", "List seasons (same list as Web/Telegram/Discord)"),
        ("/session", "Switch onto a season (#short_id, n, or name)"),
        ("/model [set <name>]", "Show/switch active model (interactive picker)"),
        ("/models", "Alias for /model"),
        ("/status", "Gateway health overview"),
        ("/memory", "Memory store stats"),
        ("/cost", "Session token spend"),
        ("/context", "Season usage + server context window"),
        ("/personality [list|<name>]", "Show/switch live server personality"),
        ("/config [show|model|personality|memory|tools|export]", "Live server config"),
        ("/replay [list|clear|<n>]", "Time travel: list or rewind this season"),
        ("/fork <n>", "Branch this season from snapshot n (new thread)"),
        ("/export", "Export session to file"),
        ("/swarm [status|list|<task>]", "Swarm dispatch and management"),
        ("/quit", "Exit Kazma TUI"),
    ]

    DEFAULT_CSS = """
    ChatPanel {
        height: 1fr;
        border: none;
        background: $surface;
        padding: 0 1;
    }
    ChatPanel .chat-banner {
        height: 1;
        color: $primary;
        text-style: bold;
        padding: 0 1 1 1;
    }
    ChatPanel > RichLog {
        height: 1fr;
        background: $surface;
        border: tall $border;
        padding: 1 2;
        scrollbar-color: $border $panel;
        scrollbar-color-hover: $primary 50%;
    }
    ChatPanel > ProgressBar {
        height: 1;
        margin: 0 1;
        color: $primary;
    }
    ChatPanel > Input {
        dock: bottom;
        height: 3;
        margin: 0 0 1 0;
        background: $panel;
        border: tall $border;
        color: $text;
        padding: 0 1;
    }
    ChatPanel > Input:focus {
        border: tall $primary;
        background: $boost;
    }

    ChatPanel > #autocomplete {
        dock: bottom;
        offset: 0 -4;
        width: auto;
        min-width: 36;
        max-height: 16;
        background: $panel;
        border: tall $primary;
        display: none;
        padding: 0 1;
    }
    ChatPanel > #autocomplete ListItem {
        padding: 0 1;
        height: auto;
    }
    ChatPanel > #autocomplete ListItem.-highlight {
        background: $primary 18%;
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
        self._ac_suppress: bool = False
        self._session_id: str = ""
        self._thread_id: str = ""
        self._session_model: str = ""
        self._workspace_id: str = ""

    def compose(self) -> ComposeResult:
        yield Static(
            "  CHAT  ·  / for commands  ·  Ctrl+P palette",
            classes="chat-banner",
        )
        yield RichLog(id="chat-log", highlight=True, markup=True, wrap=True, auto_scroll=True, max_lines=500)
        bar = ProgressBar(id="chat-progress", total=100, show_eta=False)
        bar.display = False
        yield bar
        yield Input(placeholder="Message  ·  / for commands  ·  Ctrl+P palette", id="chat-input")
        yield ListView(id="autocomplete")

    # ── Slash command autocomplete ─────────────────────────────────
    def on_input_changed(self, event: Input.Changed) -> None:
        """Show autocomplete suggestions when the user types /."""
        if event.input.id != "chat-input":
            return
        if self._ac_suppress:
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

        # Default: match slash commands. Hide once the user types args so
        # Enter on `/session 12` submits instead of filling `/sessions`.
        from kazma_tui.slash_complete import slash_matches

        matches = slash_matches(val, self.SLASH_COMMANDS)
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
            from kazma_tui.slash_complete import enter_completes_autocomplete

            inp = self.query_one("#chat-input", Input)
            if not enter_completes_autocomplete(inp.value, self._ac_matches):
                return
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
            new_val = f"/model set {match_text}"
        else:
            new_val = match_text + " "

        # Set value, then immediately override the selection that _watch_value
        # stretches. Selection is applied synchronously in the reactive setter.
        self._ac_suppress = True
        inp.value = new_val
        pos = len(new_val)
        inp.selection = (pos, pos)
        inp.cursor_position = pos
        inp.focus()
        # Second pass: timer fires after all reactive watchers settle
        self.set_timer(0.05, lambda: self._clear_ac_selection(inp, pos))

    def _clear_ac_selection(self, inp: Input, pos: int) -> None:
        """Final selection clear after all reactive processing settles."""
        inp.selection = (pos, pos)
        inp.cursor_position = pos
        self._ac_suppress = False

    # ── Message display ────────────────────────────────────────────

    def write(self, role: str, text: str, *, ts: str | None = None) -> None:
        """Write a message to the chat log with role prefix."""
        log = self.query_one("#chat-log", RichLog)
        stamp = ts or datetime.now().strftime("%H:%M")
        c = ROLE_HEX.get(role, "#8b949e")
        # Escape Rich markup in user/LLM text to prevent injection
        from rich.text import Text
        log.write(Text.from_markup(f"[dim]{stamp}[/] [{c}]▌ {role.upper()}[/] ") + Text(text))

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
        """Send prompt through the live Kazma supervisor (same as Web)."""
        from rich.text import Text

        log = self.query_one("#chat-log", RichLog)
        ts = datetime.now().strftime("%H:%M")
        log.write(f"[dim]{ts}[/] [#c084fc]▌ KAZMA[/] ")
        self.show_progress(True)

        try:
            if not self._session_id:
                import uuid

                self._session_id = str(uuid.uuid4())
                self._thread_id = self._session_id
            self._messages.append({"role": "user", "content": prompt})

            from kazma_core.agent.turn_client import stream_chat_turn

            def _on_event(ev: Any) -> None:
                if ev.kind == "tool_call" and ev.tool:
                    log.write(Text(f"[tool] {ev.tool}", style="dim"))
                elif ev.kind == "error" and ev.text:
                    log.write(Text(ev.text, style="red"))

            if not self._workspace_id:
                try:
                    self._workspace_id = await self._ensure_workspace_id()
                except Exception:
                    self._workspace_id = ""
            content = await stream_chat_turn(
                text=prompt,
                session_id=self._session_id,
                on_event=_on_event,
                model=self._session_model,
                workspace_id=self._workspace_id,
            )
            if content:
                self._last_response = content
                self._messages.append({"role": "assistant", "content": content})
                log.write(Text(content))
            else:
                log.write(
                    "[dim](empty response — the server sent no assistant text. "
                    "If this keeps happening, the TUI is not reaching "
                    "127.0.0.1:9090; check KAZMA_PORT / KAZMA_SECRET.)[/]"
                )
        except Exception as e:
            log.write(f"\n[#ef4444]Error: {e}[/]")
        finally:
            self.show_progress(False)
            self._busy = False
            try:
                self.query_one("#chat-input", Input).disabled = False
            except Exception as exc:
                logger.debug("Re-enable input failed: %s", exc)

    # ── Input handling ─────────────────────────────────────────────

    def _on_model_picked(self, model_name: str | None) -> None:
        """Callback when a model is selected from the picker."""
        if not model_name:
            return
        self._session_model = model_name
        self.write(
            "system",
            f"This season will use {model_name} (not a process-wide switch).",
        )
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
        try:
            from kazma_core.agent.slash_turns import rewrite_work_slash

            _rw = rewrite_work_slash(text)
            if _rw:
                text = _rw
        except Exception:
            pass
        if text.startswith("/") and not rewrite_work_slash(text):
            self._handle_command(text)
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
                self._session_model = model_name
                self.write(
                    "system",
                    f"This season will use {model_name} (sent on the next turn).",
                )
            else:
                active = self._session_model
                from kazma_tui.widgets.model_picker import ModelPicker
                self.app.push_screen(ModelPicker(active_model=active), self._on_model_picked)
        elif cmd == "/memory":
            self.app.call_later(self._cmd_memory)
        elif cmd == "/status":
            self.app.call_later(self._cmd_status)
        elif cmd == "/cost":
            self.app.call_later(self._cmd_cost)
        elif cmd == "/context":
            self.app.call_later(self._cmd_context)
        elif cmd == "/reset":
            self.write("system", "Conversation context reset.")
        elif cmd in ("/sessions", "/seasons", "/session", "/season", "/switch"):
            self.app.call_later(self._cmd_sessions, text)
        elif cmd == "/personality":
            self.app.call_later(self._cmd_personality, text)
        elif cmd == "/config":
            self.app.call_later(self._cmd_config, text)
        elif cmd == "/replay":
            self.app.call_later(self._cmd_replay, text)
        elif cmd == "/fork":
            self.app.call_later(self._cmd_fork, text)
        elif cmd == "/export":
            self.app.call_later(self._cmd_export)
        elif cmd == "/swarm":
            self.app.call_later(self._handle_swarm_command, text)
        else:
            self.write("system", f"Unknown: {cmd}")

    async def _cmd_sessions(self, text: str) -> None:
        """List or switch onto a season shared with Web and the gateways."""
        try:
            from kazma_core.sessions.directory import (
                create_named_session,
                format_session_list,
                list_directory,
                resolve_session,
            )
        except Exception as e:
            self.write("error", f"Session directory unavailable: {e}")
            return
        parts = text.split(None, 1)
        rest = parts[1].strip() if len(parts) > 1 else ""
        cmd = parts[0].lower()
        if cmd in ("/sessions", "/seasons") or not rest or rest.lower() in {"list", "ls"}:
            here = f"this TUI mouth: {self._session_id[-8:]}" if self._session_id else "no season yet"
            self.write("system", format_session_list(list_directory()) + f"\n({here})")
            return
        bits = rest.split(None, 1)
        if bits[0].lower() in {"new", "start"}:
            import uuid

            title = bits[1].strip() if len(bits) > 1 else ""
            sid = ""
            if title:
                try:
                    entry = create_named_session(
                        platform="web", sender_id="tui", title=title
                    )
                    sid = entry.session_id
                except Exception:
                    logger.debug("TUI named season create failed", exc_info=True)
            self._session_id = sid or str(uuid.uuid4())
            self._thread_id = self._session_id
            self._messages = []
            self._reset_chat_log()
            label = title or f"#{self._session_id[-8:]}"
            self.write("system", f"New TUI season {label} — same brain as Web.")
            return
        hit = resolve_session(rest, current_thread_id=self._session_id or None)
        if hit is None:
            self.write("system", f"No season matches {rest!r}. Try /sessions.")
            return
        self._session_id = hit.session_id or hit.thread_id
        self._thread_id = hit.thread_id or self._session_id
        try:
            n = await self._load_season_messages(hit.session_id, thread_id=hit.thread_id)
            self._replay_season_log()
        except Exception as exc:
            logger.exception("TUI season load failed for %s", hit.session_id)
            self.write("error", f"Could not load season #{hit.short_id}: {exc}")
            return
        hint = (
            f"Loaded #{hit.short_id}  {hit.title}  ({n} msgs). "
            "Next messages continue this season on the same supervisor."
        )
        if n == 0:
            hint += (
                " History was empty here — send a message to continue, "
                "or check KAZMA_SECRET if this season has turns on Web."
            )
        try:
            self.show_progress(False)
        except Exception:
            pass
        self.write("system", hint)

    def _reset_chat_log(self) -> None:
        try:
            log = self.query_one("#chat-log", RichLog)
            log.clear()
        except Exception:
            logger.debug("TUI chat log clear failed", exc_info=True)

    def _replay_season_log(self) -> None:
        """Paint loaded history into the visible log (cap so huge threads fit)."""
        self._reset_chat_log()
        tail = self._messages[-40:]
        skipped = max(0, len(self._messages) - len(tail))
        if skipped:
            self.write("system", f"Showing last {len(tail)} of {len(self._messages)} messages.")
        for m in tail:
            role = str(m.get("role") or "assistant")
            content = str(m.get("content") or "")
            if not content.strip():
                continue
            if len(content) > 4000:
                content = content[:4000] + "…"
            raw_ts = str(m.get("ts") or m.get("timestamp") or "")
            stamp = None
            if raw_ts:
                try:
                    stamp = datetime.fromisoformat(raw_ts.replace("Z", "+00:00")).strftime("%H:%M")
                except Exception:
                    stamp = raw_ts[11:16] if len(raw_ts) >= 16 else None
            self.write(role, content, ts=stamp)

    async def _load_season_messages(
        self, session_id: str, *, thread_id: str = ""
    ) -> int:
        from kazma_tui.season_load import load_season_messages_async

        self._messages = await load_season_messages_async(session_id, thread_id)
        return len(self._messages)

    async def _api(self, method: str, path: str, payload: Any | None = None) -> Any:
        from kazma_core.runtime.local_api import request_json_async

        return await request_json_async(method, path, payload=payload)

    async def _ensure_workspace_id(self) -> str:
        if self._workspace_id:
            return self._workspace_id
        try:
            data = await self._api("GET", "/api/workspaces")
            self._workspace_id = str((data or {}).get("active_workspace_id") or "")
        except Exception:
            self._workspace_id = ""
        return self._workspace_id

    def _replay_thread_id(self) -> str:
        return self._thread_id or self._session_id or ""

    async def _cmd_memory(self) -> None:
        try:
            health = await self._api("GET", "/api/memory/v2/health")
            status = health.get("status", "?")
            lines = [f"Memory Status: {status} (live /api/memory/v2/health)", ""]
            beliefs = health.get("beliefs") or {}
            if beliefs:
                lines.append(
                    "  Beliefs: "
                    f"active={beliefs.get('active', 0)} "
                    f"superseded={beliefs.get('superseded', 0)} "
                    f"archived={beliefs.get('archived', 0)}"
                )
            episodes = health.get("episodes") or {}
            if episodes:
                lines.append(
                    "  Episodes: "
                    + " ".join(f"{k}={v}" for k, v in episodes.items())
                )
            queue = health.get("queue") or {}
            if queue:
                lines.append(
                    "  Queue: "
                    f"pending={queue.get('pending', 0)} "
                    f"processing={queue.get('processing', 0)} "
                    f"failed={queue.get('failed', 0)}"
                )
            lines.append(f"  Entities: {health.get('entities', 0)}")
            self.write("system", "\n".join(lines))
        except Exception as e:
            self.write("error", f"Memory health unavailable: {e}")

    async def _cmd_status(self) -> None:
        try:
            status = await self._api("GET", "/api/status")
            active: dict[str, Any] = {}
            try:
                active = await self._api("GET", "/api/provider/active")
            except Exception:
                active = {}
            swarm: dict[str, Any] = {}
            try:
                swarm = await self._api("GET", "/api/swarm/status")
            except Exception:
                swarm = {}
            lines = [
                "Server Status (live)",
                f"  Health:   {status.get('status', '?')}",
                f"  Provider: {active.get('provider') or 'none'}",
                f"  Model:    {self._session_model or active.get('model') or 'none'}"
                + (" (this season)" if self._session_model else " (server default)"),
                f"  Workers:  {swarm.get('count', 0)}",
            ]
            errs = status.get("init_errors") or []
            if errs:
                lines.append(f"  Init:     {len(errs)} subsystem warning(s)")
            self.write("system", "\n".join(lines))
        except Exception as e:
            self.write("error", f"Status unavailable: {e}")

    async def _cmd_cost(self) -> None:
        sid = self._session_id
        if not sid:
            self.write("system", "No season yet — send a message first.")
            return
        try:
            data = await self._api(
                "GET", f"/api/chat/sessions/{sid}/messages?stats=1"
            )
            if isinstance(data, list):
                cost = 0.0
                tokens = 0
            else:
                cost = float((data or {}).get("total_cost") or 0.0)
                tokens = int((data or {}).get("total_tokens") or 0)
            lines = [
                "Season Cost (live session totals)",
                f"  Total Cost:   ${cost:.4f}",
                f"  Total Tokens: {tokens:,}",
                f"  Season:       #{sid[-8:]}",
            ]
            self.write("system", "\n".join(lines))
        except Exception as e:
            self.write("error", f"Cost tracking unavailable: {e}")

    async def _session_message_payload(
        self,
    ) -> tuple[list[dict[str, Any]], int, float]:
        """Live session transcript + billed totals (not this TUI process)."""
        sid = self._session_id
        if not sid:
            return [], 0, 0.0
        data = await self._api("GET", f"/api/chat/sessions/{sid}/messages?stats=1")
        if isinstance(data, list):
            return list(data), 0, 0.0
        data = data or {}
        msgs = list(data.get("messages") or [])
        tokens = int(data.get("total_tokens") or 0)
        cost = float(data.get("total_cost") or 0.0)
        return msgs, tokens, cost

    async def _cmd_context(self) -> None:
        try:
            ctx = await self._api("GET", "/api/settings/agent/context") or {}
            window = int(ctx.get("max_context_tokens") or 128_000)
            threshold = float(ctx.get("summarization_threshold") or 0.8)
            strategy = str(ctx.get("context_strategy") or "sliding_window")
            msgs: list[dict[str, Any]] = []
            tokens = 0
            if self._session_id:
                msgs, tokens, _cost = await self._session_message_payload()
            pct = (tokens / window * 100) if window else 0
            bar_len = 20
            filled = max(0, min(bar_len, int(bar_len * pct / 100)))
            bar = "#" * filled + "-" * (bar_len - filled)
            sid = self._session_id or ""
            lines = [
                "Context Window (live server)",
                f"  Season tokens: {tokens:,} / {window:,} ({pct:.1f}%)",
                f"  [{bar}]",
                f"  Messages:      {len(msgs)}"
                + (f"  #{sid[-8:]}" if sid else "  (no season yet)"),
                f"  Strategy:      {strategy}",
                f"  Compact at:    {threshold:.0%} of window",
                "  Totals are billed session usage from /api/chat/sessions, "
                "not this TUI process's local buffer.",
            ]
            self.write("system", "\n".join(lines))
        except Exception as e:
            self.write("error", f"Context info unavailable: {e}")

    def _format_personality_row(self, item: dict[str, Any]) -> str:
        name = str(item.get("name") or "?")
        emoji = str(item.get("emoji") or "")
        desc = str(item.get("description") or "").strip()
        label = f"{name} {emoji}".strip()
        return f"{label} — {desc}" if desc else label

    async def _cmd_personality(self, text: str = "/personality") -> None:
        parts = text.strip().split()
        sub = parts[1].lower() if len(parts) > 1 else ""
        try:
            agent = await self._api("GET", "/api/settings/agent") or {}
            plist_raw = await self._api("GET", "/api/settings/agent/personalities") or []
            plist = [p for p in plist_raw if isinstance(p, dict)]
        except Exception as e:
            self.write("error", f"Personality API unavailable: {e}")
            return

        current = str(agent.get("personality") or "default")
        by_name = {
            str(p.get("name") or "").strip().lower(): p
            for p in plist
            if str(p.get("name") or "").strip()
        }
        cur = by_name.get(current.lower(), {"name": current, "emoji": "", "description": ""})

        if not sub or sub == "current":
            self.write(
                "system",
                f"Current personality (live server): {self._format_personality_row(cur)}\n"
                "Available: "
                + ", ".join(
                    f"{p.get('name')} {p.get('emoji') or ''}".strip() for p in plist
                )
                + "\nSwitch: /personality <name>",
            )
            return

        if sub == "list":
            lines = ["Available personalities (live server):"]
            for p in plist:
                mark = " *" if str(p.get("name") or "").lower() == current.lower() else ""
                lines.append(f"  {self._format_personality_row(p)}{mark}")
            lines.append("Switch: /personality <name>")
            self.write("system", "\n".join(lines))
            return

        hit = by_name.get(sub)
        if hit is None:
            available = ", ".join(str(p.get("name") or "") for p in plist) or "(none)"
            self.write(
                "system",
                f"Unknown personality {sub!r}. Available: {available}",
            )
            return
        name = str(hit.get("name") or sub)
        try:
            await self._api("PUT", "/api/settings/agent", {"personality": name})
        except Exception as e:
            self.write("error", f"Personality switch failed: {e}")
            return
        self.write(
            "system",
            f"Switched personality to {self._format_personality_row(hit)} (live server).",
        )

    async def _cmd_config(self, text: str = "/config") -> None:
        parts = text.strip().split()
        sub = parts[1].lower() if len(parts) > 1 else "show"

        if sub in ("help", "?"):
            self.write(
                "system",
                "Config (live /api/settings — protected keys stay denied):\n"
                "  /config show\n"
                "  /config model [name]\n"
                "  /config personality [name]\n"
                "  /config memory [on|off]\n"
                "  /config tools list | /config tools toggle <name>\n"
                "  /config export",
            )
            return

        if sub == "personality":
            rest = parts[2] if len(parts) > 2 else ""
            await self._cmd_personality(
                f"/personality {rest}".strip() if rest else "/personality"
            )
            return

        try:
            if sub in ("show", ""):
                agent = await self._api("GET", "/api/settings/agent") or {}
                active: dict[str, Any] = {}
                try:
                    active = await self._api("GET", "/api/provider/active") or {}
                except Exception:
                    active = {}
                mem_on = True
                try:
                    grouped = await self._api("GET", "/api/settings") or {}
                    for keys in grouped.values() if isinstance(grouped, dict) else []:
                        if isinstance(keys, dict) and "memory.enabled" in keys:
                            mem_on = bool(keys.get("memory.enabled"))
                            break
                except Exception:
                    pass
                model = (
                    self._session_model
                    or active.get("model")
                    or "none"
                )
                model_note = " (this season)" if self._session_model else " (server default)"
                lines = [
                    "Current configuration (live server)",
                    f"  Name:         {agent.get('name') or 'kazma'}",
                    f"  Language:     {agent.get('language') or '?'}",
                    f"  Model:        {model}{model_note}",
                    f"  Personality:  {agent.get('personality') or 'default'}",
                    f"  Max rounds:   {agent.get('max_iterations') or '?'}",
                    f"  Memory:       {'enabled' if mem_on else 'disabled'}",
                    "  Change: /config model|personality|memory|tools|export",
                ]
                self.write("system", "\n".join(lines))
                return

            if sub == "model":
                if len(parts) < 3:
                    active = {}
                    try:
                        active = await self._api("GET", "/api/provider/active") or {}
                    except Exception:
                        active = {}
                    current = self._session_model or active.get("model") or "none"
                    self.write(
                        "system",
                        f"Current model: {current}\n"
                        "Usage: /config model <name>  (server active model)\n"
                        "Season-only pin: /model set <name>",
                    )
                    return
                model_name = parts[2].strip()
                data = await self._api(
                    "PUT", "/api/settings/active_model", {"model": model_name}
                )
                if isinstance(data, dict) and data.get("status") == "error":
                    self.write(
                        "error",
                        data.get("error") or data.get("detail") or "model switch failed",
                    )
                    return
                self.write("system", f"Server active model → {model_name}")
                return

            if sub == "memory":
                if len(parts) < 3 or parts[2].lower() not in ("on", "off"):
                    grouped = await self._api("GET", "/api/settings") or {}
                    mem_on = True
                    for keys in grouped.values() if isinstance(grouped, dict) else []:
                        if isinstance(keys, dict) and "memory.enabled" in keys:
                            mem_on = bool(keys.get("memory.enabled"))
                            break
                    state = "enabled" if mem_on else "disabled"
                    self.write(
                        "system",
                        f"Memory is {state}. Usage: /config memory on|off",
                    )
                    return
                enabled = parts[2].lower() == "on"
                await self._api(
                    "PUT",
                    "/api/settings/single",
                    {
                        "key": "memory.enabled",
                        "value": enabled,
                        "category": "memory",
                    },
                )
                self.write(
                    "system",
                    f"Memory {'ON' if enabled else 'OFF'} (live ConfigStore).",
                )
                return

            if sub == "tools":
                action = parts[2].lower() if len(parts) > 2 else "list"
                tools = await self._api("GET", "/api/settings/tools") or []
                if not isinstance(tools, list):
                    tools = []
                if action == "list" or action == "":
                    if not tools:
                        self.write("system", "No tools reported by /api/settings/tools.")
                        return
                    lines = ["Tools (live server):"]
                    for t in tools[:80]:
                        if not isinstance(t, dict):
                            continue
                        name = t.get("name") or "?"
                        on = t.get("enabled", True)
                        lines.append(f"  {'on ' if on else 'off'}  {name}")
                    if len(tools) > 80:
                        lines.append(f"  … {len(tools) - 80} more")
                    lines.append("Toggle: /config tools toggle <name>")
                    self.write("system", "\n".join(lines))
                    return
                if action == "toggle" and len(parts) >= 4:
                    tool_name = parts[3]
                    current = next(
                        (
                            t
                            for t in tools
                            if isinstance(t, dict)
                            and str(t.get("name") or "").lower() == tool_name.lower()
                        ),
                        None,
                    )
                    if current is None:
                        self.write("system", f"Unknown tool: {tool_name}")
                        return
                    new_on = not bool(current.get("enabled", True))
                    await self._api(
                        "PUT",
                        f"/api/settings/tools/{current.get('name')}/toggle",
                        {"enabled": new_on},
                    )
                    self.write(
                        "system",
                        f"Tool {current.get('name')} "
                        f"{'enabled' if new_on else 'disabled'} (live server).",
                    )
                    return
                self.write(
                    "system",
                    "Usage: /config tools list | /config tools toggle <name>",
                )
                return

            if sub == "export":
                grouped = await self._api("GET", "/api/settings") or {}
                from datetime import datetime
                from pathlib import Path
                import json

                export_dir = Path("kazma-data/exports")
                export_dir.mkdir(parents=True, exist_ok=True)
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                path = export_dir / f"config_{ts}.json"
                path.write_text(
                    json.dumps(grouped, indent=2, ensure_ascii=False, default=str),
                    encoding="utf-8",
                )
                self.write(
                    "system",
                    f"Wrote live /api/settings (secrets masked) to {path}",
                )
                return

            self.write(
                "system",
                "Unknown /config sub-command. Try /config help.",
            )
        except Exception as e:
            self.write("error", f"Config command failed: {e}")

    async def _cmd_replay(self, text: str) -> None:
        parts = text.strip().split()
        sub = parts[1].lower() if len(parts) > 1 else ""

        if not sub:
            self.write("system",
                "Replay Commands:\n"
                "  /replay list          — snapshots from the live server\n"
                "  /replay <iteration>   — rewind this season's graph\n"
                "  /replay show <n>      — snapshot details (no rewind)\n"
                "  /replay clear         — clear snapshots for this season")
            return

        thread_id = self._replay_thread_id()
        if not thread_id:
            self.write("system", "No season yet — send a message or /session first.")
            return

        try:
            if sub == "list":
                data = await self._api("GET", f"/api/replay/snapshots/{thread_id}")
                items = (data or {}).get("snapshots") or []
                if not items and thread_id != self._session_id and self._session_id:
                    data = await self._api(
                        "GET", f"/api/replay/snapshots/{self._session_id}"
                    )
                    items = (data or {}).get("snapshots") or []
                    if items:
                        thread_id = self._session_id
                        self._thread_id = thread_id
                if not items:
                    self.write("system", "No snapshots available for this season.")
                    return
                lines = ["Available snapshots (live graph):", ""]
                for rec in items:
                    lines.append(
                        f"  Iteration {rec.get('iteration')}  |  "
                        f"{rec.get('timestamp') or '?'}  |  "
                        f"model={rec.get('model') or '?'}  |  "
                        f"{rec.get('message_count', 0)} msgs"
                    )
                self.write("system", "\n".join(lines))
                return

            if sub == "clear":
                data = await self._api("DELETE", f"/api/replay/threads/{thread_id}")
                count = (data or {}).get("cleared", 0)
                self.write("system", f"Cleared {count} snapshot(s) for this season.")
                return

            show_only = sub == "show"
            raw_iter = parts[2] if show_only and len(parts) > 2 else sub
            try:
                iteration = int(raw_iter)
            except ValueError:
                self.write(
                    "system",
                    "Unknown sub-command. Use: /replay list, /replay clear, "
                    "/replay show <n>, or /replay <number>",
                )
                return

            if show_only:
                data = await self._api(
                    "GET", f"/api/replay/snapshots/{thread_id}/{iteration}"
                )
                self.write(
                    "system",
                    f"Snapshot {iteration}  |  model={data.get('model') or '?'}  |  "
                    f"{data.get('message_count', 0)} messages",
                )
                return

            data = await self._api(
                "POST",
                "/api/replay/restore",
                {"thread_id": thread_id, "iteration": iteration},
            )
            n = int((data or {}).get("message_count") or 0)
            try:
                loaded = await self._load_season_messages(
                    self._session_id, thread_id=thread_id
                )
                self._replay_season_log()
            except Exception:
                loaded = n
            self.write(
                "system",
                f"Rewound season to iteration {iteration} "
                f"({loaded} messages). Next send continues from here.",
            )
        except Exception as e:
            self.write("error", f"Replay command failed: {e}")

    async def _cmd_fork(self, text: str) -> None:
        parts = text.strip().split()
        if len(parts) < 2:
            self.write(
                "system",
                "Usage: /fork <iteration> — branch this season from a snapshot "
                "into a new thread (original stays put).",
            )
            return
        try:
            iteration = int(parts[1])
        except ValueError:
            self.write("system", "Usage: /fork <iteration>")
            return
        thread_id = self._replay_thread_id()
        if not thread_id:
            self.write("system", "No season yet — send a message or /session first.")
            return
        try:
            data = await self._api(
                "POST",
                "/api/replay/fork",
                {"thread_id": thread_id, "iteration": iteration},
            )
            new_tid = str((data or {}).get("new_thread_id") or "")
            if not new_tid:
                self.write("error", "Fork returned no new_thread_id.")
                return
            self._session_id = new_tid
            self._thread_id = new_tid
            try:
                n = await self._load_season_messages(new_tid, thread_id=new_tid)
                self._replay_season_log()
            except Exception:
                n = int((data or {}).get("message_count") or 0)
            self.write(
                "system",
                f"Forked iteration {iteration} → #{new_tid[-8:]} ({n} msgs). "
                "This mouth is now on the new season.",
            )
        except Exception as e:
            self.write("error", f"Fork failed: {e}")

    async def _cmd_export(self) -> None:
        if not self._session_id:
            self.write("system", "No season yet — send a message or /session first.")
            return
        try:
            from datetime import datetime
            from pathlib import Path
            import json

            msgs, tokens, cost = await self._session_message_payload()
            export_dir = Path("kazma-data/exports")
            export_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            sid = self._session_id

            md_path = export_dir / f"chat_{ts}.md"
            lines = [
                "# Kazma Chat Export",
                f"Date: {datetime.now().isoformat()}",
                f"Season: {sid}",
                f"Tokens: {tokens}",
                f"Cost: ${cost:.4f}",
                "",
            ]
            for msg in msgs:
                role = str(msg.get("role") or "unknown").upper()
                content = str(msg.get("content") or "")
                lines.append(f"## {role}")
                lines.append("")
                lines.append(content)
                lines.append("")
            md_path.write_text("\n".join(lines), encoding="utf-8")

            json_path = export_dir / f"chat_{ts}.json"
            json_path.write_text(
                json.dumps(
                    {
                        "session_id": sid,
                        "total_tokens": tokens,
                        "total_cost": cost,
                        "messages": msgs,
                    },
                    indent=2,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            self.write(
                "system",
                f"Exported {len(msgs)} live-server messages to:\n  {md_path}\n  {json_path}",
            )
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
                if sub in ("status", "list", "ls"):
                    data = await self._api("GET", "/api/swarm/status")
                    workers = (data or {}).get("workers") or []
                    if sub == "status":
                        lines = [f"Swarm Status ({len(workers)} workers, live API):"]
                        for w in workers:
                            name = w.get("name") or "?"
                            model = w.get("model") or "?"
                            st = w.get("status") or ""
                            lines.append(f"  {name} [{model}] {st}".rstrip())
                        if not workers:
                            lines.append("  (no workers registered)")
                        self.write("system", "\n".join(lines))
                    elif not workers:
                        self.write(
                            "system",
                            "No workers registered. Add workers via the Web UI Swarm panel.",
                        )
                    else:
                        lines = [f"Workers ({len(workers)}):"]
                        for w in workers:
                            name = w.get("name") or "?"
                            role = w.get("role") or ""
                            model = w.get("model") or ""
                            lines.append(
                                f"  {name}"
                                + (f" ({role})" if role else "")
                                + (f" [{model}]" if model else "")
                            )
                        self.write("system", "\n".join(lines))
                    return
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

            status = await self._api("GET", "/api/swarm/status") or {}
            worker_names = [
                str(w.get("name") or "").lower()
                for w in (status.get("workers") or [])
                if w.get("name")
            ]

            # sub and task_body are already set above (in the is_slash / else block)

            # ── Known subcommands (only for /swarm prefix) ──────────────
            if is_slash:
                # /swarm broadcast <task>
                if sub == "broadcast":
                    if not task_body:
                        self.write("error", "Usage: /swarm broadcast <task>")
                        return
                    await self._dispatch_swarm(task_body, broadcast=True)
                    return

                # /swarm <worker> <task>
                if sub in worker_names:
                    if not task_body:
                        self.write("error", f"Usage: /swarm {sub} <task>")
                        return
                    await self._dispatch_swarm(task_body, worker_name=sub)
                    return

                # /swarm <task> — auto-route
                task = text[len("/swarm "):].strip()
                if not task:
                    self.write("error", "Usage: /swarm <task>")
                    return
                await self._dispatch_swarm(task)
                return

            # Bare mention: dispatch the extracted task body
            await self._dispatch_swarm(task_body)
        finally:
            self._busy = False
            try:
                self.query_one("#chat-input", Input).disabled = False
            except Exception:
                pass

    async def _dispatch_swarm(
        self,
        task: str,
        worker_name: str = "",
        broadcast: bool = False,
    ) -> None:
        """Dispatch a task via the live server API (same mouth as the Web UI)."""
        self.write("system", "Dispatching to swarm...")
        try:
            payload: dict[str, Any] = {"task": task}
            if broadcast:
                payload["type"] = "broadcast"
                payload["workers"] = ["all"]
            elif worker_name:
                payload["type"] = "dispatch"
                payload["workers"] = [worker_name]
            else:
                payload["type"] = "dispatch"
                payload["workers"] = ["auto"]

            data = await self._api("POST", "/api/swarm/dispatch", payload) or {}
            status = str(data.get("status") or "")
            output = (
                data.get("aggregated_output")
                or data.get("synthesized_output")
                or ""
            )
            if not output:
                for result in data.get("results") or []:
                    if isinstance(result, dict) and result.get("output"):
                        output = result["output"]
                        break
            if output:
                self._last_response = str(output)
                self.write("assistant", str(output))
            elif status in ("ok", "warning") and data.get("message"):
                self.write("system", str(data["message"]))
            elif data.get("error") or data.get("message"):
                self.write("error", f"Swarm error: {data.get('error') or data.get('message')}")
            else:
                self.write("system", "Swarm task completed (no output).")
        except Exception as exc:
            self.write("error", f"Swarm dispatch failed: {exc}")

    async def _generate_response(self, prompt: str) -> None:
        await self.write_stream(prompt)

    @staticmethod
    def _get_system_prompt() -> str:
        """Load the system prompt from kazma.yaml or ConfigStore.

        Unused by the live chat path (TUI posts to the supervisor).
        Kept for /context and export helpers.
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
