"""Kazma TUI Theme — exact kazma.ai Web UI color palette mapped to Textual TCSS.

Color sources from kazma-ui/kazma_ui/static/css/kazma.css:
  bg:           #0a0f14  (deep charcoal page bg)
  bg-panel:     #11171f  (card/panel bg)
  accent:       #22d3ee  (cyan primary)
  secondary:    #a855f7  (purple)
  success:      #10b981  (green)
  warning:      #f59e0b  (amber)
  danger:       #ef4444  (red)
  text-primary: #e6edf3  (near-white)
  text-secondary: #b1bac4
  text-tertiary:  #8b949e
  border:       rgba(255,255,255,0.1)
"""

KAZMA_THEME = """
/* ═══════════════════════════════════════════════════════════════════════
   Kazma TUI Theme — muted, professional palette
   ═══════════════════════════════════════════════════════════════════════ */

/* Base colors — desaturated for a professional, calm look */
$primary:    #56b6c2;      /* muted teal-cyan (was neon #22d3ee) */
$secondary:  #c084fc;      /* soft purple (was saturated #a855f7) */
$accent:     #56b6c2;
$error:      #ef4444;
$success:    #10b981;
$warning:    #f59e0b;
$surface:    #0a0f14;      /* deepest bg */
$panel:      #11171f;      /* card/panel bg */
$boost:      #141c25;      /* elevated surface */
$border:     rgba(255,255,255,0.08);  /* subtle borders */

$text:       #e6edf3;
$text-muted: #b1bac4;
$text-disabled: #8b949e;

/* Selection */
$screen-selection-background: rgba(86,182,194,0.25);
$screen-selection-foreground: #e6edf3;

/* ═════ Global ═════ */

Screen {
    background: $surface;
    color: $text;
}

Header {
    background: $panel;
    color: $text-muted;
}

Footer {
    background: $panel;
    color: $text-muted;
}

/* ═════ Tabs ═════ */

TabbedContent {
    height: 1fr;
}
TabPane {
    background: $surface;
    padding: 1 2;
}
ContentTabs {
    background: $panel;
    border-bottom: solid $border;
}
ContentTabs > Tab {
    padding: 0 3;
    color: $text-muted;
    background: transparent;
    text-style: bold;
    border: none;
}
ContentTabs > Tab:hover { color: $text; }
ContentTabs > Tab.-active {
    color: $primary;
    background: $surface;
    border-bottom: solid $primary;
}

/* ═════ Chat ═════ */

ChatPanel {
    height: 1fr;
    background: $surface;
}
ChatPanel > RichLog {
    height: 1fr;
    background: transparent;
    border: none;
    padding: 1 2;
}
ChatPanel > Input {
    dock: bottom;
    height: 3;
    margin: 1 2;
    background: $panel;
    border: solid $border;
    color: $text;
}
ChatPanel > Input:focus { border: solid $primary; }

/* ═════ Shared components ═════ */

DataTable {
    background: transparent;
}
DataTable > .datatable--header {
    background: $panel;
    color: $primary;
    text-style: bold;
}
DataTable > .datatable--cursor {
    background: $primary 10%;
}

RichLog {
    background: transparent;
    scrollbar-color: $border $panel;
    scrollbar-color-hover: $primary;
    scrollbar-color-active: $primary;
    scrollbar-size: 1 1;
}

Input {
    background: $panel;
    border: solid $border;
    color: $text;
}
Input:focus { border: solid $primary; }

Button {
    background: $panel;
    border: solid $border;
    color: $text;
}
Button:hover { border: solid $primary; background: $primary 8%; }
Button.-primary {
    background: $primary 15%;
    border: solid $primary;
    color: $primary;
    text-style: bold;
}
Button.-primary:hover { background: $primary 25%; }

SelectionList {
    background: transparent;
}
SelectionList > ListItem {
    padding: 0 2;
}
SelectionList > ListItem.-highlight {
    background: $primary 10%;
}

Tree {
    background: transparent;
}
Tree > .tree--cursor {
    background: $primary 10%;
}

ProgressBar {
    height: 1;
}

Scrollbar {
    scrollbar-color: $border;
    scrollbar-color-hover: $primary;
    scrollbar-color-active: $primary;
    scrollbar-size: 1 1;
}
"""
