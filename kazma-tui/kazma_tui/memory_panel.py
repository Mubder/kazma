"""Memory health strip for the TUI dashboard (Web Memory & Governance parity)."""

from __future__ import annotations

import logging
from typing import Any

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widget import Widget
from textual.widgets import Static

__all__ = ["MemoryHealthPanel"]

logger = logging.getLogger(__name__)


class MemoryHealthPanel(Widget):
    """Compact live memory stack status (L1–L4 + pipeline flags).

    Refreshes on a timer when mounted. Uses ``build_memory_health()`` —
    same probe as the Web Dashboard.
    """

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
        self._chips: dict[str, Static] = {}

    def compose(self) -> ComposeResult:
        yield Static("  MEMORY  ·  live health", classes="mem-title")
        yield Static("[dim]probing…[/]", id="mem-status-line", classes="mem-status-line")
        with Horizontal(classes="mem-chips", id="mem-chips-row"):
            for chip_id, label in (
                ("mem-enabled", "enabled"),
                ("mem-perturn", "per-turn"),
                ("mem-autostore", "auto-store"),
                ("mem-consol", "consolidator"),
                ("mem-l1", "L1"),
                ("mem-l2", "L2"),
                ("mem-l3", "L3"),
                ("mem-l4", "L4"),
                ("mem-embed", "embedder"),
            ):
                yield Static(f"○ {label}", id=chip_id, classes="mem-chip")

    def on_mount(self) -> None:
        self._refresh()
        self.set_interval(self.REFRESH_INTERVAL, self._refresh)

    def _refresh(self) -> None:
        try:
            from kazma_core.memory.health import build_memory_health

            data = build_memory_health()
        except Exception as exc:
            logger.debug("memory health probe failed: %s", exc)
            try:
                self.query_one("#mem-status-line", Static).update(
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
        if headline and headline not in summary:
            line += f"\n[dim]{headline[:120]}[/]"
        try:
            self.query_one("#mem-status-line", Static).update(line)
        except Exception:
            pass

        by_id: dict[str, dict] = {}
        for c in data.get("components") or []:
            if isinstance(c, dict) and c.get("id"):
                by_id[str(c["id"])] = c

        mapping = {
            "mem-enabled": "memory_enabled",
            "mem-perturn": "per_turn_retrieval",
            "mem-autostore": "auto_store",
            "mem-consol": "consolidation",
            "mem-l1": "layer_l1",
            "mem-l2": "layer_l2",
            "mem-l3": "layer_l3",
            "mem-l4": "layer_l4",
            "mem-embed": "embedder",
        }
        labels = {
            "mem-enabled": "enabled",
            "mem-perturn": "per-turn",
            "mem-autostore": "auto-store",
            "mem-consol": "consolidator",
            "mem-l1": "L1",
            "mem-l2": "L2",
            "mem-l3": "L3",
            "mem-l4": "L4",
            "mem-embed": "embedder",
        }
        for wid, cid in mapping.items():
            c = by_id.get(cid) or {}
            st = str(c.get("status") or ("ok" if c.get("ok") else "error"))
            cls = {
                "ok": "-ok",
                "warn": "-warn",
                "error": "-err",
                "off": "-off",
            }.get(st, "-err" if not c.get("ok") else "-ok")
            mark = "●" if st == "ok" else ("◐" if st in ("warn", "off") else "○")
            label = labels[wid]
            try:
                w = self.query_one(f"#{wid}", Static)
                w.set_classes(f"mem-chip {cls}")
                detail = str(c.get("detail") or "")[:80]
                w.tooltip = detail or None
                w.update(f"{mark} {label}")
            except Exception:
                pass
