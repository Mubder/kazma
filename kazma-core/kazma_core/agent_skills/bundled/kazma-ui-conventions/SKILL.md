---
name: kazma-ui-conventions
description: Kazma Web UI conventions for editing kazma-ui templates and JS. Use when changing the Kazma web UI (dialogs, toasts, Alpine panels, grids).
---

# Kazma Web UI Conventions

Apply these rules whenever editing files under `kazma-ui/kazma_ui/` (templates, `static/js/`).

## Dialogs — never native browser dialogs

Use the unified Promise-based helpers:

- `window.kazmaConfirm(opts)` → `Promise<boolean>`
- `window.kazmaAlert(opts)` → `Promise<void>`
- `window.kazmaPrompt(opts)` → `Promise<string|null>`

All are backed by `$store.modal` (`static/js/modules/stores.js` + `components/modal.html`)
with a native fallback if Alpine hasn't booted. The modal is single-instance — do not
create a parallel dialog system.

## Toasts — one system only

Use `window.showToast(msg, type, duration)` or `Alpine.store('toast').add(...)`.
`streaming.js`'s `KazmaStream.toast` delegates to `$store.toast`. Never invent a second toast stack.

## x-cloak is GLOBAL — do not reintroduce the blink

`[x-cloak] { display: none !important; }` lives once in `kazma.css`. Every `x-show`-gated
panel MUST also carry `x-cloak`, or it flashes visible before Alpine evaluates.

Never put `display:flex` (or any `display`) in an inline `style` on an `x-show` element —
the inline declaration beats Alpine's `display:none` toggle. Put layout in a CSS class
(see `.system-alerts-banner`).

## Responsive grids

Use `class="two-col-grid"` (collapses to one column ≤768px via `kazma.css`) instead of
bare inline `grid-template-columns:1fr 1fr;`, which does not collapse and crushes mobile.

## After any change

- JS: syntax-check with `node --check <file>`.
- Verify no new native `alert()/confirm()/prompt()` calls were added.
