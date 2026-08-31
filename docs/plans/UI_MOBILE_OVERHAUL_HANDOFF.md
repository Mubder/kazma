# Handoff — Kazma Web UI overhaul (mobile + desktop)

**Status date:** 2026-08-31 (full P1–P4 + defects sweep shipped; **code complete**. Real iPhone Light/Dark canvas sign-off is still an operator check — desktop DevTools cannot close that class.)
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
| 2026-08-12 | `855e6eb8` | Phase 0 canvas (iOS Safari dark-canvas leak) |
| 2026-08-12 | (prior) | P0 Light contrast + handoff file |
| 2026-08-12 | `4315ca4a` | **P1** token unification — one Abyss table in kazma.css; v5 stripped to shell polish |
| 2026-08-12 | `07b8cdd0` | **P2a** mobile `.page-body` dock clearance (longhand padding in Tiers 3/5) |
| 2026-08-12 | `8f5ea00c` | **P2b** 769–1280px icon-rail dead strip removed (sidebar stays full, toggle-driven) |
| 2026-08-12 | `5b1664c5` | **P2c** phone chrome — opaque header/dock, retire hamburger, visualViewport composer |
| 2026-08-12 | `900919dd` | **P3** x-show + inline `display:flex` blink purge (Workspace/Memory/Settings/base) |
| 2026-08-12 | `6f6635fa` | **P4** delete orphaned `index.html` (soft-nav + HTMX intentionally kept — see §4) |
| 2026-08-12 | `e0ab8c80` | **Defects sweep** — code themes, Auto theme, AR/EN copy, welcome tile, `--error` |
| 2026-08-12 | (this commit) | **Canvas-race fix** — inline critical `<style>` + remove render-blocking `@import` (intermittent iOS gray in Light on iOS Dark) |

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

## 4. Now — full P1–P4 + defects sweep shipped; phone verify still operator

All six planned slices (P1, P2a, P2b, P2c, P3, P4) **plus** the "Other known
defects" sweep are committed (see §3). Each slice is its own commit so a
visual regression can be bisected/reverted independently.

**Verify on a real iPhone (required) + desktop after pull + restart:**
- Both Kazma themes: no cyan/indigo/violet bleed; one blue family everywhere.
- Phones: last Dashboard/Settings/Workspace rows NOT under the tab bar.
- 769–1280px (small laptop): full sidebar with labels + visible collapse toggle; no dead strip.
- Phones: opaque header + dock (no gray bleed under safe-area); composer stays visible when keyboard opens.
- Light theme: code blocks render light (CodeMirror + highlight.js); welcome logo not a black square.
- Settings: Auto theme preview applies (resolves OS preference live).
- AR ↔ EN toggle: confirm dialog shows in the CURRENT language.
- No first-paint blink on Workspace modals / Settings badges / Memory pagers.

**Known invariants to preserve (do not regress):**
- ONE token table (kazma.css §1/§23). Do NOT reintroduce a token table in kazma.v5.css or any `#22d3ee`/`#5e6ad2`/`#a78bfa` literal.
- Do NOT lighten the Light muted-text values (#334155/#475569/#64748b) — P0 contrast fix.
- Do NOT put `color-scheme: light dark` or remove the `html[data-theme]` single-value rules (Phase-0 iOS canvas).
- Do NOT re-add inline `display:` on any `x-show`-gated element (AGENTS.md blink rule).
- Keep `SOFT_NAV_ENABLED` + `HARD_RELOAD_ALWAYS` in nav.js as-is (working fallback).

### Deferred (real features, not one-line fixes — picked up separately)
- **`accent_color`**: stored + edited in Settings but not applied. Deriving the full accent family (hover/subtle/glow/rgb/gradient) from one hex needs a color-math util; a half-implementation (just `--accent`) looks worse than none.
- **Notification bell panel**: `$store.notifications.items` is always empty; a real panel needs a data source (SSE?) + read state + a rendered drawer. Out of scope for a CSS/bug-overhaul pass.
- **Soft-nav "finish"**: soft-nav only fires for `/workspace` (every heavy page hard-reloads via `HARD_RELOAD_ALWAYS`). It's working, reliable fallback logic (~280 lines); making it soft-nav the SSE/CodeMirror/Alpine pages is a separate project. Left as-is.
- **P3 cosmetic polish** (Chat/Dashboard/Settings-tab visual tweaks): the load-bearing P3 work (the `x-show`+inline-display blink purge) shipped; remaining page-by-page cosmetic polish is lower-severity and risk-heavy without a real device.

---

## 5. Next — post-overhaul backlog (the planned queue is empty)

The P1–P4 + defects sweep queue from the original plan is **done**. What
remains is the deferred-feature backlog (§4 "Deferred") and anything a
phone verify surfaces. Pick up in roughly this order:

| Item | Notes |
|------|-------|
| Phone verify of P1–P4 + sweep | The load-bearing gate. Fix anything that regresses before new work. |
| `accent_color` feature | Color-math util to derive `--accent-*` family from one hex; wire into `saveAppearance`/`previewTheme`. |
| Notification panel | A real `$store.notifications` data source + a rendered drawer component. |
| Soft-nav finish (optional) | Make soft-nav handle SSE/CodeMirror/Alpine pages, or delete it and accept hard-reload everywhere. |
| P3 cosmetic polish | Per-page visual passes (Chat/Dashboard/Settings tabs) once a device is in hand. |

### Remaining known defects (post-sweep)
- `accent_color` stored, not applied (see §4 Deferred).
- Notification bell toggles an empty store — no panel (see §4 Deferred).
- Soft-nav only fires for `/workspace` (working fallback; see §4 Deferred).

---

## 6. Key files

| File | Role |
|------|------|
| `kazma-ui/kazma_ui/templates/base.html` | viewport, color-scheme, theme-color, bottom-nav |
| `kazma-ui/kazma_ui/templates/login.html` | standalone head — must stay in sync with base metas |
| `kazma-ui/kazma_ui/templates/chat.html` | composer `#chat-input.chat-input`; highlight.js theme (SSR via `theme()`) |
| `kazma-ui/kazma_ui/static/css/kazma.css` | **single token table** (§1 dark, §23 light) + layout + components + 5-tier responsive + utility classes (`.ic-row`, `.ws-modal-overlay`, `.mem-pager`, `.spinner-inline`) |
| `kazma-ui/kazma_ui/static/css/kazma.v5.css` | shell/component polish ONLY. color-scheme rules kept (Phase-0 canvas). NO token table after P1. |
| `kazma-ui/kazma_ui/static/js/modules/components.js` | `syncDocumentColorScheme`, theme toggle, language-toggle confirm, boot appearance-fetch (resolves `auto`) |
| `kazma-ui/kazma_ui/static/js/app.js` | exports `window.syncDocumentColorScheme`; `initPhoneViewport` (visualViewport → `--app-ivh`) |
| `kazma-ui/kazma_ui/static/js/settings.js` | `saveAppearance` / `previewTheme` resolve `auto`; `_resolveAutoTheme` |
| `kazma-ui/kazma_ui/static/js/ide.js` | CodeMirror theme picks `default`/`material-darker` from `data-theme` |
| `kazma-ui/kazma_ui/static/js/modules/nav.js` | soft-nav (`SOFT_NAV_ENABLED` + `HARD_RELOAD_ALWAYS`) — kept as-is |
| `kazma-ui/kazma_ui/app.py` | `_dynamic_theme()` resolves `auto`→dark (concrete for SSR); `css_version` includes v5 |
| `tests/test_ui_components.py` | canvas contract tests; `test_has_accent_color` asserts Abyss royal |

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
| 2026-08-12 | P0 Light contrast | Composer uses theme ink; Light field elevated; muted text darkened. |
| 2026-08-12 | P1 token unification (`4315ca4a`) | ONE token table (kazma.css §1/§23); v5 stripped to shell polish; all cyan/indigo/violet literals purged; `--bg-card` defined; `test_has_accent_color` updated. 137 passed. |
| 2026-08-12 | P2a dock clearance (`07b8cdd0`) | Tiers 3/5 `.page-body` use longhand padding; dock clearance survives. 137 passed. |
| 2026-08-12 | P2b icon rail (`8f5ea00c`) | 769–1280px auto-rail removed; sidebar full + toggle-driven (no dead strip). 137 passed. |
| 2026-08-12 | P2c phone chrome (`5b1664c5`) | Opaque header/dock; hamburger retire; visualViewport → `--app-ivh` composer. 137 passed. |
| 2026-08-12 | P3 blink purge (`900919dd`) | All `x-show`+inline `display:` violations moved to CSS classes across Workspace/Memory/Settings/base. 137 passed. |
| 2026-08-12 | P4 dead code (`6f6635fa`) | Orphaned `index.html` deleted; soft-nav + HTMX kept (working). 139 passed. |
| 2026-08-12 | Defects sweep (`e0ab8c80`) | Code themes follow theme; Auto applies; AR/EN copy de-swapped; welcome tile Light; `--error` aliased. `accent_color` + bell panel deferred (features). 137 passed. |
| 2026-08-12 | Canvas-race fix | User: "Light theme on iOS Dark shows gray, intermittent — sometimes good." Cause: kazma.css had a render-blocking Google-Fonts `@import` that delayed the external `html { background-color }` + `color-scheme` rules; iOS committed the canvas from the dark system pref before they applied. Fix: inline critical `<style>` (SSR-resolved html bg + color-scheme) in base.html + login.html first bytes; removed the `@import`, moved fonts to `<link>`. 137 passed. |
| 2026-08-12 | Full run complete | P1–P4 + sweep + canvas-race shipped. **Awaiting phone verify.** |

---

## 9. Prompt stub for the next agent

```
Read docs/plans/UI_MOBILE_OVERHAUL_HANDOFF.md end-to-end. Do not rewrite the frontend.
The full P1–P4 + defects sweep is shipped (§3); the planned queue is empty. Start at
§4 (Now) — the priority is PHONE VERIFY of everything in §4's verify checklist. Fix any
regression before new work. After §4 verifies, the only remaining work is the §4/§5
Deferred backlog (accent_color feature, notification panel, soft-nav finish, cosmetic
polish) — each is a real feature, not a one-line fix.
Verify on a real iPhone if you touch color-scheme, tokens, or the mobile shell.
Do NOT reintroduce a token table in kazma.v5.css, any #22d3ee / #5e6ad2 / #a78bfa
literal, inline display: on an x-show element, or lighten the Light muted-text values.
```
