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
| 2026-08-12 | (prior commit) | P0 Light contrast + handoff file |
| 2026-08-12 | this commit | **P1 token unification** — see §4 |

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

### P1 — Token unification — **done, shipping with this commit**

**Goal:** fold Abyss into ONE token table; kill the v4 cyan/indigo/violet leftovers.

**Shipped:**

- `kazma.css §1 :root` (dark) rewritten from v4 cyan (`--accent:#22d3ee`) to Abyss royal (`--accent:#3b82f6`, sky secondary, deep blue-black `--bg:#0e1626`). `--brand`, `--bg-card` aliases added inline.
- `kazma.css §23` Light + the `@media (prefers-color-scheme: light)` auto-light block folded to Abyss light (`--accent:#2563eb`, ice `--bg:#f0f4fa`). Kept the dark P0 muted-text values in BOTH blocks (do not lighten).
- `--bg-card` now defined (dark + light) — fixes the `.search-panel` / global-search-chip missing-token defect.
- All surviving component-rule literals purged and replaced with tokens:
  `#a78bfa` → `var(--accent-light)`; `rgba(139,92,246,…)` → `rgba(var(--accent-rgb),…)`;
  `rgba(94,106,210,…)` → `var(--accent-subtle)` / `rgba(var(--accent-rgb),…)`;
  every `var(--accent, #5e6ad2)` fallback → `var(--accent)` (no indigo escape hatch).
- Aurora body glow: cyan `rgba(34,211,238,…)` → sky `rgba(56,189,248,…)` (both tones now blue-family).
- `kazma.v5.css` stripped of its duplicate dark/light/auto-light token tables (they fought kazma.css). v5 now = color-scheme rules (Phase-0 canvas fix, MUST stay) + shell/component polish only. Property-set diff confirmed: nothing v5 defined is lost.
- `--warning`/`--warning-bg` aligned to yellow (`#facc15`/`rgba(250,204,21,…)`) across both files.
- Test `test_has_accent_color` updated to assert Abyss royal (`#3b82f6`/`#2563eb`) instead of the purged cyan/indigo. **137 passed.**

**Verify after pull/restart:** iPhone + desktop, both Kazma themes — no cyan/indigo/violet bleed anywhere; badges, active session pill, hint chips, reaction buttons, info toasts, aurora all read blue-family. Token edit surface is now one file (kazma.css §1/§23).

**Do not** reintroduce a token table in kazma.v5.css, reintroduce `#22d3ee`/`#5e6ad2`/`#a78bfa`/`rgba(94,106,210,…)`/`rgba(139,92,246,…)`, or lighten the Light muted-text values (P0 regression).

### Next: P2a — Mobile `.page-body` padding vs dock

See §5. Dashboard/Settings/Workspace last rows sit under the tab bar because `padding: var(--sp-4)` overwrites `padding-bottom: dock`.

---

## 5. Next (after P1 tokens)

In order from the overhaul plan. Do **not** restyle all 16 pages in one PR.

| ID | Slice | Notes |
|----|--------|------|
| P2a | Mobile `.page-body` padding vs dock | Later `padding: var(--sp-4)` **overwrites** `padding-bottom: dock`. Dashboard/Settings/Workspace last rows sit under the tab bar. |
| P2b | Laptop 769–1280 icon rail | Sidebar width 60px but `.main-content` still margin 250px → ~190px dead strip. Labels gone; collapse control hidden. **Fix:** keep labels; collapse is a user toggle only. |
| P2c | Phone chrome | Compact header; delete hamburger (More is the menu); `visualViewport` for keyboard vs composer; opaque header/dock (no `color-mix(..., transparent)` over canvas). |
| P3 | Page passes | Chat polish → Dashboard → Settings mobile tabs → Workspace `x-show`+`display:flex` → IDE/Swarm/Memory/Documents. |
| P4 | Dead code | Soft-nav is enabled but almost every page hard-reloads — delete or finish. HTMX global for one MCP delete. Unused `index.html`. |

### Other known defects (do not lose)

- Settings **Auto** is a button that does not SSR or apply (`_dynamic_theme` only accepts light\|dark).
- Notification bell toggles an empty store — no panel.
- Language-toggle confirm copy is swapped (AR UI gets English strings) in `components.js`.
- `accent_color` in appearance is stored, not applied to `--accent`.
- Welcome logo forced onto a black tile in both themes (`kazma.v5.css`).
- CodeMirror `material-darker` + highlight.js `github-dark` even in Light.
- `--error` is referenced (`.send-btn.stop-mode`, two `agent-activity` rules) but never defined — all three call sites already carry a `#ef4444` fallback (== `--danger`), so not visibly broken, but worth aliasing `--error: var(--danger)` in a future token pass.

---

## 6. Key files

| File | Role |
|------|------|
| `kazma-ui/kazma_ui/templates/base.html` | viewport, color-scheme, theme-color, bottom-nav |
| `kazma-ui/kazma_ui/templates/login.html` | standalone head — must stay in sync with base metas |
| `kazma-ui/kazma_ui/templates/chat.html` | composer `#chat-input.chat-input` |
| `kazma-ui/kazma_ui/static/css/kazma.css` | **single token table** (§1 dark, §23 light) + layout + components + 5-tier responsive |
| `kazma-ui/kazma_ui/static/css/kazma.v5.css` | shell/component polish ONLY (sidebar/header/bottom-nav/dashboard/chat/swarm). color-scheme rules kept (Phase-0 canvas). No token table after P1. |
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
| 2026-08-12 | P1 token unification | ONE token table (kazma.css §1/§23); v5 stripped to shell polish; all cyan/indigo/violet literals purged; `--bg-card` defined; `test_has_accent_color` updated. 137 passed. Next after verify: P2a dock padding. |

---

## 9. Prompt stub for the next agent

```
Read docs/plans/UI_MOBILE_OVERHAUL_HANDOFF.md end-to-end. Do not rewrite the frontend.
Start at section 4 (Now) — P1 tokens just shipped; next is P2a (mobile .page-body padding
vs dock). After each slice: update sections 3, 4, 5, and 8 of the handoff, then wait for
the user before starting the next ID in section 5.
Verify Light theme composer + page text on a real phone if you touch color-scheme or tokens.
Do NOT reintroduce a token table in kazma.v5.css or any #22d3ee / #5e6ad2 / #a78bfa literal.
```
