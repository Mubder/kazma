# Handoff — Kazma Web UI overhaul (mobile + desktop)

**Status date:** 2026-08-12  
**Keep this file current.** Update the “Now / Next / Log” sections after every slice so another agent can pick up without rereading the chat.

**Do not** start a React / Vue / Svelte / native rewrite. Stack stays FastAPI + Jinja + Alpine + `kazma.css` / `kazma.v5.css`.

**Plan (full diagnosis + phases):** session plan at  
`C:\Users\balfa\.grok\sessions\G%3A%5CGitHubRepos%5Ckazma\019ff733-bc86-72e0-a7f9-f0ad25acef33\plan.md`  
Repo-facing copy of the same decisions lives in this file.

---

## 1. Mission

Make Light and Dark look correct on a **real iPhone** (any system Light/Dark) **and** on desktop. The UI is an early-stage Jinja/Alpine app that was patched, not redesigned.

Original user report: on iPhone **system Dark**, the **page background** was iOS Safari’s gray (`#121212`) in **both** Kazma themes. Header, cards, bottom bar were fine. Desktop could not reproduce it.

After Phase 0 shipped and the user pulled: **chat canvas is much better**. New report: **Light theme text is too pale; the chat composer is a white box with white text.**

---

## 2. Stack (locked)

| Keep | Why |
|------|-----|
| FastAPI + Jinja + Alpine | SSE chat, HITL, i18n/RTL, SSR `data-theme` |
| One CSS design system (v5 Abyss tokens as SoT) | v4 cyan leftover still in `kazma.css` |
| Cheap PWA later (manifest + theme-color) | No service worker this pass |

**Do not** introduce React/Next/Capacitor/RN. TUI is a different mouth (`kazma_tui`).

### Non-negotiables

- Chat transport: `POST /api/chat/stream` (`streaming.js`). HITL: `POST /api/approve/{thread_id}`.
- Dialogs: `kazmaConfirm` / `kazmaAlert` / `kazmaPrompt` only. Toasts: `$store.toast`.
- Global `[x-cloak]`. No inline `display:flex` on `x-show`.
- Theme is account-authoritative (`SettingsManager` → SSR `theme()` → `data-theme`).
- IDE writes go through `IdeService` → `LocalToolRegistry` (HITL).
- Desktop Chrome DevTools **cannot** sign off iOS canvas. Test on a real iPhone.

---

## 3. Shipped

| When | Commit / slice | What |
|------|----------------|------|
| 2026-08-12 | `855e6eb8` `fix(ui): stop iOS Safari painting its dark canvas over Kazma themes` | Phase 0 canvas |
| 2026-08-12 | this commit | P0 Light contrast + handoff file |

**Phase 0 (in `855e6eb8`) — do not re-do as “add html { background }”:**

- `<meta name="color-scheme">` is a **single** value from `theme()` — never `light dark`.
- `<meta name="theme-color">` + `viewport-fit=cover`.
- CSS: `html[data-theme="light"] { color-scheme: only light }` / dark `only dark`. **Removed** `:root { color-scheme: dark }`.
- Opaque `background-color: var(--bg)` on `html`, `body`, `.app-layout`, `.main-content`, `.page-body`, `.chat-container`, `.chat-main`, `.chat-messages`, `.chat-welcome`.
- `html::before` fixed paint layer.
- Body grid stops use `var(--bg)`, not `transparent`.
- `--bg-deep` set in v5 dark.
- `_applyTheme()` → `syncDocumentColorScheme()` updates metas + `data-theme`.
- Login uses the same metas / tokens (no indigo `#5e6ad2` fallbacks).
- Header `padding-top: env(safe-area-inset-top)`; `--header-height` includes it.
- Tests in `tests/test_ui_components.py` (136 passed at ship).

**User confirmation after pull:** chat **visually much better** on the phone. Canvas leak is largely gone.

---

## 4. Now (open — start here)

### P0 — Light theme contrast — **done, shipping with this commit**

**Symptom (user, after `855e6eb8`):** Light theme fonts too pale; composer white box + white text.

**Cause:** `.chat-input { color: #e6edf3 }` was hardcoded dark-theme ice. Light `.input-wrapper` is white. `color-scheme: only light` makes Safari paint the textarea white. Ice on white = invisible.

**Shipped:**

- `.chat-input` uses `color` / `caret-color` / `-webkit-text-fill-color: var(--text-primary)`.
- `::placeholder` uses `--text-muted`.
- Light composer: `background: var(--bg-elevated)` + stronger border (not white-on-ice).
- Light header title: solid `--text-primary` (no transparent gradient clip).
- Light `--text-secondary/#334155`, `--text-tertiary/#475569`, `--text-muted/#64748b` (darker than `#8b9bb8`).

**Verify after pull/restart:** iPhone + desktop, Kazma Light — type in the composer; welcome + model bar must be readable.

**Do not** revert Phase 0 color-scheme metas or lighten `--bg`.

---

## 5. Next (after P0 contrast)

In order from the overhaul plan. Do **not** restyle all 16 pages in one PR.

| ID | Slice | Notes |
|----|--------|------|
| P1 | Unify tokens | Fold Abyss into one token table. Delete v4 cyan light/dark duplicate + `@media (prefers-color-scheme: light)` restyle **or** make Auto real. Kill `#22d3ee` / `#5e6ad2` / `#a78bfa` leftovers. |
| P2a | Mobile `.page-body` padding vs dock | Later `padding: var(--sp-4)` **overwrites** `padding-bottom: dock`. Dashboard/Settings/Workspace last rows sit under the tab bar. |
| P2b | Laptop 769–1280 icon rail | Sidebar width 60px but `.main-content` still margin 250px → ~190px dead strip. Labels gone; collapse control hidden. **Fix:** keep labels; collapse is a user toggle only. |
| P2c | Phone chrome | Compact header; delete hamburger (More is the menu); `visualViewport` for keyboard vs composer; opaque header/dock (no `color-mix(..., transparent)` over canvas). |
| P3 | Page passes | Chat polish → Dashboard → Settings mobile tabs → Workspace `x-show`+`display:flex` → IDE/Swarm/Memory/Documents. |
| P4 | Dead code | Soft-nav is enabled but almost every page hard-reloads — delete or finish. HTMX global for one MCP delete. Unused `index.html`. |

### Other known defects (do not lose)

- Settings **Auto** is a button that does not SSR or apply (`_dynamic_theme` only accepts light\|dark).
- `--bg-card` used in `.search-panel` — token does not exist.
- Notification bell toggles an empty store — no panel.
- Language-toggle confirm copy is swapped (AR UI gets English strings) in `components.js`.
- `accent_color` in appearance is stored, not applied to `--accent`.
- Welcome logo forced onto a black tile in both themes (`kazma.v5.css`).
- CodeMirror `material-darker` + highlight.js `github-dark` even in Light.

---

## 6. Key files

| File | Role |
|------|------|
| `kazma-ui/kazma_ui/templates/base.html` | viewport, color-scheme, theme-color, bottom-nav |
| `kazma-ui/kazma_ui/templates/login.html` | standalone head — must stay in sync with base metas |
| `kazma-ui/kazma_ui/templates/chat.html` | composer `#chat-input.chat-input` |
| `kazma-ui/kazma_ui/static/css/kazma.css` | layout, `.chat-input` hardcoded color, light overrides, 5-tier responsive |
| `kazma-ui/kazma_ui/static/css/kazma.v5.css` | Abyss tokens + `color-scheme: only *` + mobile nav |
| `kazma-ui/kazma_ui/static/js/modules/components.js` | `syncDocumentColorScheme`, theme toggle |
| `kazma-ui/kazma_ui/static/js/app.js` | exports `window.syncDocumentColorScheme` |
| `kazma-ui/kazma_ui/static/js/settings.js` | `saveAppearance` / `previewTheme` must call sync |
| `kazma-ui/kazma_ui/app.py` | `_dynamic_theme()`, `css_version` includes v5 |
| `tests/test_ui_components.py` | canvas contract tests |

Live process on **9090 / my.kazma.ai** may be a **different checkout**. After a push the user must pull + restart that process. Workspace repo is `G:\GitHubRepos\kazma`.

---

## 7. How to verify

**iPhone (required for canvas + Light contrast):**

1. System Dark + Kazma Light → ice page (`#f0f4fa`), **dark** body text, composer readable.
2. System Dark + Kazma Dark → Abyss page (`#0e1626`), not iOS gray.
3. System Light + both Kazma themes.
4. Type in the composer (Light): ink must be dark on a visible field.
5. Overscroll / keyboard: no gray strip; input not covered.

**Desktop:** Light/Dark, EN/AR, 390 / 768 / 1280 / 1600 on Chat, Dashboard, Settings.

**Tests:**  
`& '.venv\Scripts\python.exe' -m pytest tests/test_ui_components.py -q`  
`node --check kazma-ui/kazma_ui/static/js/modules/components.js`

---

## 8. Log (append one line per slice)

| Date | Agent / slice | Result |
|------|----------------|--------|
| 2026-08-12 | Phase 0 canvas (`855e6eb8`) | User: chat much better. Light text + white-on-white composer still broken. |
| 2026-08-12 | Handoff file created | `docs/plans/UI_MOBILE_OVERHAUL_HANDOFF.md`. |
| 2026-08-12 | P0 Light contrast | Composer uses theme ink; Light field elevated; muted text darkened. Next after verify: P1 tokens or P2a dock padding. |

---

## 9. Prompt stub for the next agent

```
Read docs/plans/UI_MOBILE_OVERHAUL_HANDOFF.md end-to-end. Do not rewrite the frontend.
Start at section 4 (Now). After each slice: update sections 3, 4, 5, and 8 of the handoff,
then wait for the user before starting the next ID in section 5.
Verify Light theme composer + page text on a real phone if you touch color-scheme or tokens.
```
