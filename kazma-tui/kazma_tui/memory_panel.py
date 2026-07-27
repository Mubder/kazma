"""Memory health widgets for the TUI (Web Memory & Governance parity).

* :class:`MemoryHealthPanel` — compact strip for Dashboard
* :class:`MemoryTab` — full Memory tab with components, graph search, clear
"""

from __future__ import annotations

import logging
from typing import Any

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widget import Widget
from textual.widgets import Button, DataTable, Input, Static

from kazma_tui.widgets.confirm_dialog import ConfirmDialog

__all__ = ["MemoryHealthPanel", "MemoryTab"]

logger = logging.getLogger(__name__)

_CHIP_MAP = (
    ("mem-enabled", "memory_enabled", "enabled"),
    ("mem-perturn", "per_turn_retrieval", "per-turn"),
    ("mem-autostore", "auto_store", "auto-store"),
    ("mem-consol", "consolidation", "consolidator"),
    ("mem-l1", "layer_l1", "L1"),
    ("mem-l2", "layer_l2", "L2"),
    ("mem-l3", "layer_l3", "L3"),
    ("mem-l4", "layer_l4", "L4"),
    ("mem-embed", "embedder", "embedder"),
)


def _probe_health() -> dict[str, Any]:
    from kazma_core.memory.health import build_memory_health

    return build_memory_health()


def _status_class(st: str, ok: bool) -> str:
    return {
        "ok": "-ok",
        "warn": "-warn",
        "error": "-err",
        "off": "-off",
    }.get(st, "-err" if not ok else "-ok")


def _status_mark(st: str) -> str:
    if st == "ok":
        return "●"
    if st in ("warn", "off"):
        return "◐"
    return "○"


class MemoryHealthPanel(Widget):
    """Compact live memory stack status (L1–L4 + pipeline flags)."""

    DEFAULT_CSS = """
    MemoryHealthPanel {
        height: auto;
        margin: 0 1 1 1;
        padding: 1 2;
        background: $panel;
        border: tall $border;
    }

    MemoryHealthPanel .mem-title {
        color: $text-muted;
        text-style: bold;
        height: 1;
        margin-bottom: 1;
    }

    MemoryHealthPanel .mem-status-line {
        height: auto;
        color: $text;
        margin-bottom: 1;
    }

    MemoryHealthPanel .mem-chips {
        height: auto;
        layout: horizontal;
    }

    MemoryHealthPanel .mem-chip {
        width: auto;
        height: 1;
        margin: 0 1 0 0;
        padding: 0 1;
        background: $boost;
        color: $text-muted;
    }

    MemoryHealthPanel .mem-chip.-ok {
        color: $success;
        background: $success 12%;
    }

    MemoryHealthPanel .mem-chip.-warn {
        color: $warning;
        background: $warning 12%;
    }

    MemoryHealthPanel .mem-chip.-err {
        color: $error;
        background: $error 12%;
    }

    MemoryHealthPanel .mem-chip.-off {
        color: $text-disabled;
    }
    """

    REFRESH_INTERVAL = 5.0

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)

    def compose(self) -> ComposeResult:
        yield Static("  MEMORY  ·  live health  ·  M for full tab", classes="mem-title")
        yield Static("[dim]probing…[/]", id="mem-status-line", classes="mem-status-line")
        with Horizontal(classes="mem-chips", id="mem-chips-row"):
            for chip_id, _cid, label in _CHIP_MAP:
                yield Static(f"○ {label}", id=chip_id, classes="mem-chip")

    def on_mount(self) -> None:
        self._refresh()
        self.set_interval(self.REFRESH_INTERVAL, self._refresh)

    def _refresh(self) -> None:
        try:
            data = _probe_health()
        except Exception as exc:
            logger.debug("memory health probe failed: %s", exc)
            try:
                self.query_one("#mem-status-line", Static).update(
                    f"[bold $error]UNAVAILABLE[/]  [dim]{exc}[/]"
                )
            except Exception:
                pass
            return
        self._apply_status_line(data)
        self._apply_chips(data)

    def _apply_status_line(self, data: dict[str, Any]) -> None:
        status = str(data.get("status") or "UNKNOWN")
        summary = str(data.get("summary") or "")
        headline = str(data.get("headline") or "")
        color = {
            "ACTIVE": "$success",
            "DEGRADED": "$warning",
            "DEMO": "$primary",
            "INSTALLING": "$warning",
        }.get(status, "$text-muted")
        line = f"[bold {color}]{status}[/]"
        if summary:
            line += f"  [dim]{summary}[/]"
        if headline and headline not in summary:
            line += f"\n[dim]{headline[:120]}[/]"
        try:
            self.query_one("#mem-status-line", Static).update(line)
        except Exception:
            pass

    def _apply_chips(self, data: dict[str, Any]) -> None:
        by_id: dict[str, dict] = {}
        for c in data.get("components") or []:
            if isinstance(c, dict) and c.get("id"):
                by_id[str(c["id"])] = c
        for wid, cid, label in _CHIP_MAP:
            c = by_id.get(cid) or {}
            st = str(c.get("status") or ("ok" if c.get("ok") else "error"))
            cls = _status_class(st, bool(c.get("ok")))
            mark = _status_mark(st)
            try:
                w = self.query_one(f"#{wid}", Static)
                w.set_classes(f"mem-chip {cls}")
                detail = str(c.get("detail") or "")[:80]
                w.tooltip = detail or None
                w.update(f"{mark} {label}")
            except Exception:
                pass


class MemoryTab(VerticalScroll):
    """Full Memory tab: status, chips, component table, graph search + clear."""

    DEFAULT_CSS = """
    MemoryTab {
        height: 1fr;
        background: $surface;
        padding: 0 1;
    }
    MemoryTab .mem-banner {
        height: 1;
        color: $primary;
        text-style: bold;
        padding: 0 1 1 1;
    }
    MemoryTab .mem-section {
        margin: 0 0 1 0;
        padding: 1 2;
        background: $panel;
        border: tall $border;
        height: auto;
    }
    MemoryTab .mem-section-title {
        color: $text-muted;
        text-style: bold;
        height: 1;
        margin-bottom: 1;
    }
    MemoryTab #mem-tab-status {
        height: auto;
        margin-bottom: 1;
    }
    MemoryTab .mem-chips {
        height: auto;
        layout: horizontal;
    }
    MemoryTab .mem-chip {
        width: auto;
        height: 1;
        margin: 0 1 0 0;
        padding: 0 1;
        background: $boost;
        color: $text-muted;
    }
    MemoryTab .mem-chip.-ok { color: $success; background: $success 12%; }
    MemoryTab .mem-chip.-warn { color: $warning; background: $warning 12%; }
    MemoryTab .mem-chip.-err { color: $error; background: $error 12%; }
    MemoryTab .mem-chip.-off { color: $text-disabled; }
    MemoryTab #mem-components-table {
        height: auto;
        max-height: 14;
        border: tall $border;
    }
    MemoryTab #mem-graph-stats {
        height: auto;
        color: $text;
        margin-bottom: 1;
    }
    MemoryTab .mem-graph-actions {
        height: 3;
        layout: horizontal;
        margin-bottom: 1;
        align: left middle;
    }
    MemoryTab #mem-graph-search {
        width: 1fr;
        min-width: 16;
        height: 3;
        margin: 0 1 0 0;
        background: $boost;
        border: tall $border;
    }
    MemoryTab #mem-graph-search:focus {
        border: tall $primary;
    }
    MemoryTab #mem-graph-search-btn,
    MemoryTab #mem-graph-clear-btn {
        width: auto;
        min-width: 10;
        height: 3;
        margin: 0 1 0 0;
    }
    MemoryTab #mem-graph-clear-btn {
        border: tall $error;
        color: $error;
    }
    MemoryTab #mem-graph-results {
        height: auto;
        max-height: 12;
        border: tall $border;
    }
    MemoryTab #mem-graph-msg {
        height: auto;
        color: $text-muted;
        margin-top: 1;
    }
    """

    REFRESH_INTERVAL = 5.0

    def compose(self) -> ComposeResult:
        yield Static(
            "  MEMORY  ·  stack health · graph search · components",
            classes="mem-banner",
        )
        with Vertical(classes="mem-section"):
            yield Static("STATUS", classes="mem-section-title")
            yield Static("[dim]probing…[/]", id="mem-tab-status")
            with Horizontal(classes="mem-chips", id="mem-tab-chips"):
                for chip_id, _cid, label in _CHIP_MAP:
                    yield Static(
                        f"○ {label}",
                        id=f"tab-{chip_id}",
                        classes="mem-chip",
                    )
        with Vertical(classes="mem-section"):
            yield Static("GRAPH (L2)", classes="mem-section-title")
            yield Static("[dim]—[/]", id="mem-graph-stats")
            with Horizontal(classes="mem-graph-actions"):
                yield Input(
                    placeholder="Search graph (entity / fact)…",
                    id="mem-graph-search",
                )
                yield Button("Search", id="mem-graph-search-btn", variant="primary")
                yield Button("Clear", id="mem-graph-clear-btn")
            yield DataTable(id="mem-graph-results")
            yield Static(
                "[dim]Search L2 property graph · Clear wipes all nodes/edges[/]",
                id="mem-graph-msg",
            )
        with Vertical(classes="mem-section"):
            yield Static("COMPONENTS", classes="mem-section-title")
            table = DataTable(id="mem-components-table")
            yield table

    def on_mount(self) -> None:
        table = self.query_one("#mem-components-table", DataTable)
        table.cursor_type = "row"
        table.add_columns("Component", "Status", "Detail")

        results = self.query_one("#mem-graph-results", DataTable)
        results.cursor_type = "row"
        results.add_columns("Type", "Label", "Score", "Content")

        self._refresh()
        self.set_interval(self.REFRESH_INTERVAL, self._refresh)

    def on_show(self) -> None:
        self._refresh()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid == "mem-graph-search-btn":
            self._run_graph_search()
            event.stop()
        elif bid == "mem-graph-clear-btn":
            self._confirm_clear_graph()
            event.stop()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "mem-graph-search":
            self._run_graph_search()
            event.stop()

    def _run_graph_search(self) -> None:
        """Search the L2 knowledge graph and fill results table."""
        try:
            q = self.query_one("#mem-graph-search", Input).value.strip()
        except Exception:
            q = ""
        table = self.query_one("#mem-graph-results", DataTable)
        table.clear()
        if not q:
            try:
                self.query_one("#mem-graph-msg", Static).update(
                    "[dim]Enter a search query and press Search or Enter.[/]"
                )
            except Exception:
                pass
            return
        try:
            from kazma_core.swarm.memory.graph import get_knowledge_graph

            hits = get_knowledge_graph().search(q, limit=20)
        except Exception as exc:
            logger.debug("graph search failed: %s", exc)
            try:
                self.query_one("#mem-graph-msg", Static).update(
                    f"[bold $error]Search failed[/]  [dim]{exc}[/]"
                )
            except Exception:
                pass
            return

        if not hits:
            try:
                self.query_one("#mem-graph-msg", Static).update(
                    f"[dim]No graph hits for[/] [bold]{q[:40]}[/]"
                )
            except Exception:
                pass
            return

        for h in hits:
            etype = str(h.get("entity_type") or h.get("type") or "?")[:16]
            label = str(h.get("label") or h.get("id") or "?")[:36]
            score = h.get("score")
            score_s = f"{float(score):.2f}" if score is not None else "—"
            content = str(h.get("content") or "")[:64]
            table.add_row(etype, label, score_s, content)
        try:
            self.query_one("#mem-graph-msg", Static).update(
                f"[bold $success]{len(hits)}[/] hit(s) for [bold]{q[:40]}[/]"
            )
        except Exception:
            pass

    def _confirm_clear_graph(self) -> None:
        """Confirm then wipe L2 graph."""
        dialog = ConfirmDialog(
            message=(
                "Delete ALL nodes and edges from the L2 knowledge graph?\n"
                "This cannot be undone."
            ),
            title="Clear Knowledge Graph",
            confirm_text="Clear graph",
            cancel_text="Cancel",
            is_destructive=True,
        )

        def on_result(confirmed: bool | None) -> None:
            if not confirmed:
                return
            try:
                from kazma_core.swarm.memory.graph import get_knowledge_graph

                get_knowledge_graph().clear()
                self._refresh_graph_stats()
                try:
                    self.query_one("#mem-graph-results", DataTable).clear()
                    self.query_one("#mem-graph-msg", Static).update(
                        "[bold $warning]Graph cleared.[/]"
                    )
                except Exception:
                    pass
                try:
                    from kazma_tui.widgets.toast import Toast

                    self.app.push_screen(
                        Toast("Knowledge graph cleared", "success", duration=2.0)
                    )
                except Exception:
                    pass
            except Exception as exc:
                logger.exception("graph clear failed")
                try:
                    self.query_one("#mem-graph-msg", Static).update(
                        f"[bold $error]Clear failed[/]  [dim]{exc}[/]"
                    )
                except Exception:
                    pass

        self.app.push_screen(dialog, on_result)

    def _refresh_graph_stats(self) -> None:
        try:
            from kazma_core.swarm.memory.graph import get_knowledge_graph

            st = get_knowledge_graph().stats()
            gline = (
                f"nodes [bold]{st.get('nodes', 0)}[/]  ·  "
                f"edges [bold]{st.get('edges', 0)}[/]  ·  "
                f"[dim]{st.get('backend', 'sqlite')}[/]  "
                f"[dim]{str(st.get('path', ''))[:48]}[/]"
            )
            self.query_one("#mem-graph-stats", Static).update(gline)
        except Exception as exc:
            try:
                self.query_one("#mem-graph-stats", Static).update(
                    f"[dim]graph unavailable: {exc}[/]"
                )
            except Exception:
                pass

    def _refresh(self) -> None:
        try:
            data = _probe_health()
        except Exception as exc:
            try:
                self.query_one("#mem-tab-status", Static).update(
                    f"[bold $error]UNAVAILABLE[/]  [dim]{exc}[/]"
                )
            except Exception:
                pass
            return

        status = str(data.get("status") or "UNKNOWN")
        summary = str(data.get("summary") or "")
        headline = str(data.get("headline") or "")
        color = {
            "ACTIVE": "$success",
            "DEGRADED": "$warning",
            "DEMO": "$primary",
            "INSTALLING": "$warning",
        }.get(status, "$text-muted")
        line = f"[bold {color}]{status}[/]"
        if summary:
            line += f"  [dim]{summary}[/]"
        if headline:
            line += f"\n[dim]{headline[:160]}[/]"
        try:
            self.query_one("#mem-tab-status", Static).update(line)
        except Exception:
            pass

        by_id: dict[str, dict] = {}
        for c in data.get("components") or []:
            if isinstance(c, dict) and c.get("id"):
                by_id[str(c["id"])] = c

        for chip_id, cid, label in _CHIP_MAP:
            c = by_id.get(cid) or {}
            st = str(c.get("status") or ("ok" if c.get("ok") else "error"))
            try:
                w = self.query_one(f"#tab-{chip_id}", Static)
                w.set_classes(f"mem-chip {_status_class(st, bool(c.get('ok')))}")
                w.tooltip = str(c.get("detail") or "")[:100] or None
                w.update(f"{_status_mark(st)} {label}")
            except Exception:
                pass

        self._refresh_graph_stats()

        try:
            table = self.query_one("#mem-components-table", DataTable)
            table.clear()
            for c in data.get("components") or []:
                if not isinstance(c, dict):
                    continue
                name = str(c.get("name") or c.get("id") or "?")[:28]
                st = str(c.get("status") or "?").upper()[:8]
                detail = str(c.get("detail") or "")[:72]
                table.add_row(name, st, detail)
        except Exception:
            pass
