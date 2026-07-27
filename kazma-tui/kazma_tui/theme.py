"""Kazma TUI Theme — professional design system (v2).

Maps the kazma.ai Web UI palette into Textual TCSS with denser layout,
clearer hierarchy, and ops-console chrome (tabs, cards, chat, tables).

Color sources (kazma-ui CSS):
  surface   #0a0f14   panel #11171f   boost #161d27
  primary   #22d3ee → muted #4ecdc4 for terminal comfort
  secondary #a855f7 → soft #b794f6
  success / warning / danger from web tokens
"""

from __future__ import annotations

__all__ = ["KAZMA_THEME"]

KAZMA_THEME = """
/* ═══════════════════════════════════════════════════════════════════════
   Kazma TUI v2 — professional ops console
   ═══════════════════════════════════════════════════════════════════════ */

/* ── Tokens ─────────────────────────────────────────────────────────── */
$primary:    #4ecdc4;
$secondary:  #b794f6;
$accent:     #4ecdc4;
$error:      #f87171;
$success:    #34d399;
$warning:    #fbbf24;
$surface:    #0a0f14;
$panel:      #11171f;
$boost:      #161d27;
$border:     #243044;
$border-dim: #1a2332;

$text:          #e6edf3;
$text-muted:    #9aa7b5;
$text-disabled: #6b7785;

$screen-selection-background: rgba(78,205,196,0.22);
$screen-selection-foreground: #e6edf3;

/* ── Screen shell ───────────────────────────────────────────────────── */

Screen {
    background: $surface;
    color: $text;
}

Header {
    background: $panel;
    color: $text-muted;
    text-style: bold;
    dock: top;
    height: 3;
    border-bottom: tall $border-dim;
}

Footer {
    background: $panel;
    color: $text-muted;
    dock: bottom;
    height: 1;
    border-top: solid $border-dim;
}

/* ── Main tabs (horizontal nav bar) ─────────────────────────────────── */

TabbedContent {
    height: 1fr;
    background: $surface;
}

TabPane {
    background: $surface;
    padding: 1 1;
}

ContentTabs {
    background: $panel;
    border-bottom: solid $border-dim;
    height: 3;
    padding: 0 1;
}

ContentTabs > Tab {
    padding: 0 2;
    margin: 0 1;
    color: $text-muted;
    background: transparent;
    text-style: none;
    border: none;
    height: 3;
}

ContentTabs > Tab:hover {
    color: $text;
    background: $boost;
}

ContentTabs > Tab.-active {
    color: $primary;
    background: $boost;
    text-style: bold;
    border-bottom: tall $primary;
}

/* ── Status bar ─────────────────────────────────────────────────────── */

KazmaStatusBar {
    dock: bottom;
    height: 1;
    background: $boost;
    border-top: solid $border-dim;
    color: $text-muted;
    padding: 0 1;
}

/* ── Chat ───────────────────────────────────────────────────────────── */

ChatPanel {
    height: 1fr;
    background: $surface;
    border: none;
    padding: 0;
}

ChatPanel > RichLog {
    height: 1fr;
    background: $surface;
    border: none;
    padding: 1 2;
    scrollbar-background: $surface;
    scrollbar-color: $border;
    scrollbar-color-hover: $primary 50%;
}

ChatPanel > ProgressBar {
    height: 1;
    margin: 0 2;
    color: $primary;
}

ChatPanel > Input {
    dock: bottom;
    height: 3;
    margin: 0 1 1 1;
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
    padding: 0 1;
    display: none;
}

ChatPanel > #autocomplete ListItem {
    padding: 0 1;
    height: auto;
}

ChatPanel > #autocomplete ListItem.-highlight {
    background: $primary 18%;
}

ChatPanel > #autocomplete .ac-cmd {
    color: $primary;
    text-style: bold;
}

ChatPanel > #autocomplete .ac-desc {
    color: $text-muted;
}

/* ── Dashboard metric cards ─────────────────────────────────────────── */

MetricsDashboard {
    height: 1fr;
    padding: 1 1;
    background: $surface;
}

MetricsDashboard .metrics-grid {
    height: auto;
    background: $surface;
}

MetricsDashboard .metric-row {
    height: auto;
    layout: horizontal;
    margin-bottom: 1;
}

MetricCard {
    height: auto;
    width: 1fr;
    min-height: 5;
    padding: 1 2;
    margin: 0 1;
    background: $panel;
    border: tall $border-dim;
}

MetricCard:hover {
    border: tall $border;
    background: $boost;
}

MetricCard > .card-label {
    color: $text-muted;
    text-style: bold;
    text-opacity: 90%;
}

MetricCard > Sparkline {
    margin-top: 1;
    color: $primary;
    height: 1;
}

/* ── Shared widgets ─────────────────────────────────────────────────── */

DataTable {
    background: transparent;
    border: tall $border-dim;
}

DataTable > .datatable--header {
    background: $panel;
    color: $primary;
    text-style: bold;
}

DataTable > .datatable--cursor {
    background: $primary 12%;
}

DataTable > .datatable--hover {
    background: $boost;
}

RichLog {
    background: transparent;
    scrollbar-color: $border $panel;
    scrollbar-color-hover: $primary 50%;
    scrollbar-color-active: $primary;
    scrollbar-size: 1 1;
}

Input {
    background: $panel;
    border: tall $border-dim;
    color: $text;
    padding: 0 1;
}

Input:focus {
    border: tall $primary;
    background: $boost;
}

Button {
    background: $panel;
    border: tall $border-dim;
    color: $text;
    min-width: 12;
}

Button:hover {
    border: tall $primary;
    background: $primary 10%;
    color: $text;
}

Button.-primary {
    background: $primary 18%;
    border: tall $primary;
    color: $primary;
    text-style: bold;
}

Button.-primary:hover {
    background: $primary 28%;
}

Button.-error {
    border: tall $error;
    color: $error;
}

SelectionList {
    background: transparent;
}

SelectionList > ListItem {
    padding: 0 2;
}

SelectionList > ListItem.-highlight {
    background: $primary 12%;
}

ListView {
    background: transparent;
}

ListView > ListItem.-highlight {
    background: $primary 12%;
}

Tree {
    background: transparent;
    border: tall $border-dim;
    padding: 0 1;
}

Tree > .tree--cursor {
    background: $primary 12%;
}

ProgressBar {
    height: 1;
    color: $primary;
}

Scrollbar {
    background: $surface;
    color: $border;
}

/* ── Settings / swarm / files panels ────────────────────────────────── */

SettingsPanel, SwarmPanel, FilesPanel, TracesPanel {
    height: 1fr;
    background: $surface;
    padding: 0 1;
}

/* ── Modals ─────────────────────────────────────────────────────────── */

ModalScreen {
    background: $surface 80%;
}

Toast {
    background: $panel;
    border: tall $primary;
    color: $text;
    padding: 0 2;
}

/* ── Footer keys ────────────────────────────────────────────────────── */

FooterKey {
    background: transparent;
    color: $text-muted;
}

FooterKey > .footer-key--key {
    color: $primary;
    text-style: bold;
    background: $primary 12%;
    padding: 0 1;
}
"""
