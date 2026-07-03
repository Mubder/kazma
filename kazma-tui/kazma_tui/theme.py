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
   Kazma Web UI Palette → Textual TCSS
   ═══════════════════════════════════════════════════════════════════════ */

/* Base colors */
$primary:    #22d3ee;      /* accent-cyan */
$secondary:  #a855f7;      /* purple */
$accent:     #22d3ee;
$error:      #ef4444;      /* danger */
$success:    #10b981;
$warning:    #f59e0b;      /* amber */
$surface:    #0a0f14;      /* bg — deepest */
$panel:      #11171f;      /* bg-panel */
$boost:      #141c25;      /* bg-surface */
$border:     rgba(255,255,255,0.1);

$text:       #e6edf3;      /* text-primary */
$text-muted: #b1bac4;      /* text-secondary */
$text-disabled: #8b949e;   /* text-tertiary */

/* ═══════════════════════════════════════════════════════════════════════
   Global
   ═══════════════════════════════════════════════════════════════════════ */

Screen {
    background: $surface;
    color: $text;
}

Header {
    dock: top;
    height: 3;
    background: $panel;
    border-bottom: solid $primary 40%;
    color: $text;
    text-style: bold;
}

Footer {
    dock: bottom;
    height: 1;
    background: $primary 18%;
    color: $primary;
}

/* ═══════════════════════════════════════════════════════════════════════
   Tabs — matching web UI nav style
   ═══════════════════════════════════════════════════════════════════════ */

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
    height: 3;
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
    border-bottom: double $primary;
}

/* ═══════════════════════════════════════════════════════════════════════
   Chat
   ═══════════════════════════════════════════════════════════════════════ */

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

/* ═══════════════════════════════════════════════════════════════════════
   Swarm
   ═══════════════════════════════════════════════════════════════════════ */

WorkerTable {
    height: 1fr;
    background: transparent;
}
WorkerTable > .datatable--header {
    background: $panel;
    color: $primary;
    text-style: bold;
}

/* ═══════════════════════════════════════════════════════════════════════
   Shared components
   ═══════════════════════════════════════════════════════════════════════ */

DataTable {
    background: transparent;
    border: solid $border;
}
DataTable > .datatable--header {
    background: $panel;
    color: $primary;
}
DataTable > .datatable--cursor {
    background: $primary 12%;
}

RichLog {
    background: transparent;
    scrollbar-color: $primary $panel;
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

SelectionList {
    background: transparent;
    border: solid $border;
}
SelectionList > ListItem {
    padding: 0 2;
}
SelectionList > ListItem.-highlight {
    background: $primary 12%;
}

Tree {
    background: transparent;
}
Tree > .tree--cursor {
    background: $primary 12%;
}

ProgressBar {
    height: 1;
}

    /* ═══════════════════════════════════════════════════════════════════════
       Scrollbar — matching web UI
       ═══════════════════════════════════════════════════════════════════════ */

    Scrollbar {
        scrollbar-color: $border;
        scrollbar-color-hover: $primary;
        scrollbar-color-active: $primary;
        scrollbar-size: 1 0;
    }
    """
