"""Kazma TUI Theme — professional design system (v2 / phase 2).

``KAZMA_DARK_VARS``  — color tokens for the default dark console.
``KAZMA_SHELL_CSS``  — structural rules shared by all themes.
``KAZMA_THEME``      — dark vars + shell (App.CSS default).

Alternate themes in ``themes/theme_manager.py`` supply only variable
overrides and re-use ``KAZMA_SHELL_CSS`` so light/monokai/HC stay aligned.
"""

from __future__ import annotations

__all__ = ["KAZMA_DARK_VARS", "KAZMA_SHELL_CSS", "KAZMA_THEME"]

KAZMA_DARK_VARS = """
/* ── Kazma dark tokens ─────────────────────────────────────────────── */
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
"""

KAZMA_SHELL_CSS = """
/* ── Screen shell ───────────────────────────────────────────────────── */

Screen {
    background: $surface;
    color: $text;
}

/* Do not set hatch here. Textual's default is `hatch: right $panel` (character
   + color). `hatch: right 12%` is invalid (percentage is not a color) and
   crashes launch. `hatch: right $panel 12%` also fails after variable
   substitution (too many tokens). */

Header {
    background: $panel;
    color: $text-muted;
    text-style: bold;
    dock: top;
    height: 3;
    border-bottom: tall $border;
}

Footer {
    background: $panel;
    color: $text-muted;
    dock: bottom;
    height: 1;
    border-top: solid $border;
}

/* ── Body: left rail + main column ──────────────────────────────────── */

#body-row {
    height: 1fr;
    width: 1fr;
}

#main-column {
    height: 1fr;
    width: 1fr;
}

/* Hide top tab bar — navigation is the left rail */
#main-tabs > ContentSwitcher {
    height: 1fr;
}

#main-tabs > ContentTabs {
    display: none;
    height: 0;
}

/* Nested tabs (e.g. Swarm sub-tabs) still visible */
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
    border-bottom: solid $border;
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
    border-top: solid $border;
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

/* ── Dashboard ──────────────────────────────────────────────────────── */

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
    height: 7;
    min-height: 7;
    max-height: 7;
    layout: horizontal;
    margin-bottom: 1;
    align: left middle;
}

MetricCard {
    height: 7;
    width: 1fr;
    min-height: 7;
    max-height: 7;
    padding: 1 2;
    margin: 0 1;
    background: $panel;
    border: tall $border;
    layout: vertical;
}

MetricCard:hover {
    border: tall $primary;
    background: $boost;
}

MetricCard > .card-label {
    color: $text-muted;
    text-style: bold;
    height: 1;
}

MetricCard > .card-value {
    height: 1;
}

MetricCard > .card-spacer {
    height: 1;
}

MetricCard > Sparkline {
    margin-top: 1;
    color: $primary;
    height: 1;
}

/* Left nav rail — full labels; .-collapsed for key-only narrow mode */
NavRail {
    dock: left;
    width: 20;
    background: $panel;
    border-right: solid $border;
    height: 1fr;
}

NavRail.-collapsed {
    width: 5;
}

NavRail .nav-brand {
    height: 3;
    content-align: center middle;
    color: $primary;
    text-style: bold;
    border-bottom: solid $border;
    margin-bottom: 1;
}

NavRail .nav-btn {
    width: 100%;
    min-width: 1;
    height: 3;
    margin: 0;
    padding: 0 1;
    border: none;
    border-left: solid transparent;
    background: transparent;
    color: $text-muted;
    text-align: left;
}

NavRail.-collapsed .nav-btn {
    padding: 0;
    text-align: center;
    content-align: center middle;
}

NavRail .nav-btn:hover {
    background: $boost;
    color: $text;
    border-left: solid $primary 40%;
}

NavRail .nav-btn.-active {
    background: $primary 12%;
    color: $primary;
    text-style: bold;
    border-left: solid $primary;
}

NavRail .nav-collapse {
    dock: bottom;
    width: 100%;
    min-width: 1;
    height: 3;
    border: none;
    border-top: solid $border;
    background: $boost;
    color: $text-muted;
}

/* Panel banners (Chat / Files / Swarm / Memory) */
.chat-banner, .files-banner, .swarm-banner, .mem-banner, .metrics-title {
    color: $primary;
    text-style: bold;
}

MemoryHealthPanel {
    height: auto;
    margin: 0 1 1 1;
    padding: 1 2;
    background: $panel;
    border: tall $border;
}

/* ── Shared widgets ─────────────────────────────────────────────────── */

DataTable {
    background: transparent;
    border: tall $border;
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
    border: tall $border;
    color: $text;
    padding: 0 1;
}

Input:focus {
    border: tall $primary;
    background: $boost;
}

Button {
    background: $panel;
    border: tall $border;
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
    border: tall $border;
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

SettingsPanel, SwarmPanel, FilesPanel, TracesPanel {
    height: 1fr;
    background: $surface;
    padding: 0 1;
}

ModalScreen {
    background: $surface 80%;
}

Toast {
    background: $panel;
    border: tall $primary;
    color: $text;
    padding: 0 2;
}

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

KAZMA_THEME = KAZMA_DARK_VARS + "\n" + KAZMA_SHELL_CSS
