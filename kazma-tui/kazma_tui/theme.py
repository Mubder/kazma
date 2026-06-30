"""Kazma TUI — Professional terminal dashboard theme.

Adopts the exact color palette from kazma.ai:
  - Deep charcoal background with subtle grid
  - Cyan (#06b6d4) primary accent
  - Purple (#a855f7) secondary accent  
  - Dark panel cards (#0f172a) with subtle borders
  - Gradient cyan→purple for active elements
  - Sans-serif / monospace font pairing
"""

# ── Color Palette (from kazma.ai) ───────────────────────────────────────
KAZMA_CSS = """
/* ═══════════════════════════════════════════════════════════════════════
   Kazma Terminal Theme — kazma.ai palette
   ═══════════════════════════════════════════════════════════════════════ */

$primary: #06b6d4;        /* cyan — kazma.ai accent */
$secondary: #a855f7;      /* purple — kazma.ai secondary */
$accent: #22d3ee;         /* bright cyan — highlights */
$error: #ef4444;          /* red */
$success: #22c55e;        /* green */

$surface: #02040a;         /* deepest bg — kazma.ai page bg */
$panel: #0f172a;          /* card bg — kazma.ai panel */
$panel-alt: #18181b;      /* alternating row / hover */
$border: #1e293b;         /* subtle borders — kazma.ai card edge */
$text: #e2e8f0;           /* near-white body */
$text-muted: #94a3b8;     /* kazma.ai body text */
$text-dim: #64748b;       /* very dim */

/* ═══════════════════════════════════════════════════════════════════════
   Screen
   ═══════════════════════════════════════════════════════════════════════ */

Screen {
    background: $surface;
    color: $text;
    layout: vertical;
}

/* ═══════════════════════════════════════════════════════════════════════
   Header — cyan bottom border, centered
   ═══════════════════════════════════════════════════════════════════════ */

HeaderProviderModel {
    dock: top;
    height: 3;
    background: $panel;
    content-align: center middle;
    border-bottom: heavy $primary;
}

HeaderProviderModel Static#provider-label {
    color: $text-dim;
    content-align: right middle;
    width: auto;
    padding-right: 1;
}

HeaderProviderModel Static#model-label {
    color: $primary;
    text-style: bold;
    content-align: left middle;
    width: auto;
}

/* ═══════════════════════════════════════════════════════════════════════
   Footer — subtle top border
   ═══════════════════════════════════════════════════════════════════════ */

FooterShortcuts {
    dock: bottom;
    height: 1;
    background: $panel;
    color: $text-dim;
    content-align: center middle;
    border-top: solid $border;
}

FooterShortcuts .shortcut-key {
    color: $primary;
    text-style: bold;
}

/* ═══════════════════════════════════════════════════════════════════════
   Metrics Dashboard — panel card with purple border title
   ═══════════════════════════════════════════════════════════════════════ */

MetricsDashboard {
    height: 10;
    background: $panel;
    border: solid $border;
    border-title-align: center;
    border-title-color: $secondary;
    border-title-background: $surface;
    border-title-style: bold;
    margin: 1 1 0 1;
    padding: 1 2;
    overflow: hidden;
}

MetricsDashboard .gauge-label {
    color: $text-muted;
    text-style: bold;
}

MetricsDashboard .gauge-value {
    text-style: bold;
}

MetricsDashboard .gauge-good { color: $success; }
MetricsDashboard .gauge-warn { color: $accent; }
MetricsDashboard .gauge-bad  { color: $error; }

/* ═══════════════════════════════════════════════════════════════════════
   Chat Panel — bordered card
   ═══════════════════════════════════════════════════════════════════════ */

ChatPanel {
    height: 1fr;
    background: $panel;
    border: solid $border;
    border-title-align: center;
    border-title-color: $primary;
    border-title-background: $surface;
    border-title-style: bold;
    margin: 1 1 0 1;
    padding: 0;
    layout: vertical;
}

ChatPanel RichLog {
    height: 1fr;
    background: transparent;
    border: none;
    margin: 0 1;
}

ChatPanel Input {
    dock: bottom;
    margin: 1;
    background: $panel-alt;
    border: solid $border;
    color: $text;
}

ChatPanel Input:focus {
    border: solid $primary;
}

/* ═══════════════════════════════════════════════════════════════════════
   Swarm Panel — split-pane with blue border
   ═══════════════════════════════════════════════════════════════════════ */

SwarmPanel {
    height: 1fr;
    background: $panel;
    border: solid $border;
    border-title-align: center;
    border-title-color: $accent;
    border-title-background: $surface;
    border-title-style: bold;
    margin: 1 1 0 1;
    padding: 0;
}

SwarmPanel > Horizontal { height: 1fr; }

SwarmPanel WorkerTable {
    width: 45%;
    background: transparent;
    border: none;
    margin: 1;
}

SwarmPanel LogStream {
    width: 55%;
    background: transparent;
    border-left: solid $border;
    margin: 1;
}

/* ═══════════════════════════════════════════════════════════════════════
   DataTable — modern terminal table styling
   ═══════════════════════════════════════════════════════════════════════ */

DataTable {
    background: transparent;
    border: none;
}

DataTable > .datatable--header {
    background: $panel-alt;
    color: $primary;
    text-style: bold;
}

DataTable > .datatable--even-row { background: $panel; }
DataTable > .datatable--odd-row  { background: $panel-alt; }
DataTable > .datatable--cursor   { background: $secondary 15%; }

/* ═══════════════════════════════════════════════════════════════════════
   RichLog — clean log output
   ═══════════════════════════════════════════════════════════════════════ */

RichLog {
    background: transparent;
    scrollbar-color: $border;
    scrollbar-color-hover: $secondary;
    scrollbar-background: $surface;
}

/* ═══════════════════════════════════════════════════════════════════════
   Scrollbars
   ═══════════════════════════════════════════════════════════════════════ */

Scrollbar {
    scrollbar-color: $border;
    scrollbar-color-hover: $secondary;
    scrollbar-color-active: $primary;
    scrollbar-background: $surface;
    scrollbar-size-vertical: 1;
}

/* ═══════════════════════════════════════════════════════════════════════
   Utility classes
   ═══════════════════════════════════════════════════════════════════════ */

.metric-value {
    color: $text;
    text-style: bold;
}
.metric-unit {
    color: $text-dim;
}
.metric-label {
    color: $text-muted;
}
"""
