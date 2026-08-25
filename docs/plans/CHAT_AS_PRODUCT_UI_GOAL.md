# GOAL: Chat as the product (Web UI overhaul)

**Status:** Done (2026-08-26) — A1–C shipped in templates + `kazma.v5.css`; `chat.js` untouched  
**Created:** 2026-08-25  
**Source:** Live UI review (Abyss tokens + v5 shell + dashboard screenshot + `chat.html` / `base.html` / sidebar)  
**Rule:** Do **not** start/restart the Kazma server. Do **not** start this DAG until the operator says proceed.

Kazma’s identity (do not lose this): **one LangGraph brain**, many mouths. The Web UI is the primary mouth. It must feel like a serious agent product (Claude / Linear craft), not a 14-page observability console.

---

## Mission

Make **`/` (chat)** the product. Inspectors stay; they stop competing with the conversation.

Success is: an operator opens Kazma and sees **sessions · transcript · composer** — not breadcrumbs, metric tiles, or YOLO/cost chrome in the first glance.

---

## What this is / is not

| This is | This is not |
|---------|-------------|
| Chat chrome, typography, composer, sidebar IA | A React rewrite |
| CSS + Jinja (mostly `kazma.v5.css` + templates) | Rewriting `chat.js` / `sse_chat.py` / `app.py` |
| Hide ops chrome; keep every control | A new color palette (Abyss stays) |
| Fewer destinations in the first nav | Deleting Dashboard / Swarm / Memory |
| One visual language for messages + tools | Glassmorphism, extra glow, more Google fonts |

**Do not** “make the dashboard prettier” as the main work. That is what currently makes Kazma look like Grafana-with-a-chat.

---

## Current anatomy (freeze — do not invent)

```
base.html
  sidebar.html          14 nav-links (Chat … MCP)
  page-header           title + Home / {page} breadcrumbs + New Chat / search / theme
  system-alerts-banner
  page-body
    chat.html
      chat-sidebar      sessions list
      chat-model-bar    hamburger + model <select> + WS pulse
      chat-messages     welcome + .message-user / .message-assistant
      hitl-approval-card
      thinking-indicator
      chat-input-area   attach, voice, live, textarea, send
      capacity-bar      Long / Mission / YOLO / Unrestricted / $ / tok / context
```

Load-bearing IDs (chat.js / voice.js / agentStore — **must survive**):

`#chat-input` `#send-btn` `#chat-messages` `#model-selector` `#capacity-status` `#session-cost` `#session-tokens` `#context-size` `#capacity-bar` `[data-cap]` `#voice-btn` `#voice-live-btn` `#attach-btn` `#file-input` `#thinking-indicator` `#new-session-btn` `#session-list` `#session-search`

Progress UI is already `.agent-progress` (built in `chat.js`). Style it; do not rebuild the HTML in JS.

Tokens live in `kazma.css` §1 / §23. Shell polish is `kazma.v5.css` (rollback = remove one `<link>` in `base.html`). **Put overhaul CSS in v5**, not a third file, unless a slice needs a kill-switch of its own.

`active_page` is set in each child template (`{% set active_page = "chat" %}`) and is visible in `base.html`.

---

## Out of scope

| Parked | Why |
|--------|-----|
| Rewrite `chat.js` / `sse_chat.py` / `app.py` | God-file rewrite season (C1 hygiene cap) |
| New JS framework | Not Kazma |
| New accent / “rebrand” | v5 Abyss is the freeze |
| Adding more Google font families | Six already load |
| Dashboard metric-card restyle as the hero | Wrong surface |
| TUI / Telegram / Discord visual work | This goal is Web chat |
| SaaS marketing site | Not the product |

---

## Invariants

- LangGraph is the brain. HITL three gates + commitment stay. Alpine `$store.modal` / toast / `x-cloak`.
- No `display:` on inline `style` of `x-show` nodes (cloak flash).
- Unified dialogs: `kazmaConfirm` / `kazmaAlert` / `kazmaPrompt` — never `window.confirm`.
- RTL / Arabic: `dir="auto"` on composer and messages; do not apply Inter OpenType features to Arabic.
- Do not start/restart uvicorn. Operator restarts to see CSS/HTML.
- Windows: PowerShell uses `;` not `&&`. `node --check` on any JS you *must* touch.
- Browser verify chat (desktop + ≤768px) before calling a slice done. No single screenshot as “verified.”

---

## Work packages

### A — Chat chrome (build first; this is the product)

| ID | Slice | What changes | How (freeze) |
|----|-------|----------------|--------------|
| **A1** | **Immersive chat shell** | On `active_page == 'chat'`, hide `page-header` (title + breadcrumbs). Chat fills the main column. Keep system-alerts banner. New Chat stays in header-right **or** moves to sessions sidebar (keep one obvious New Chat). | `base.html` skip include **or** CSS `.app-layout.is-chat .page-header { display:none }`. Prefer a class on `<body>` / `.app-layout` from `active_page` so other pages are untouched. |
| **A2** | **Composer dock** | First glance: attach, mic/live (if shown), textarea, send. Long / Mission / YOLO / Unrestricted / Reset / $ / tok / context live in a **⋯** disclosure (`<details>` or Alpine `x-show` panel). **IDs stay in the DOM** (hidden, not deleted) so `chat.js` keep working. | `chat.html` wrap `#capacity-bar` in `.composer-more`. Default closed. Open state `aria-expanded`. Do not rename `data-cap` pills. |
| **A3** | **Model in the dock** | `#model-selector` moves next to the composer (or into ⋯). Remove the standalone `.chat-model-bar` strip (or CSS-hide it after the select is relocated — **move the node**, do not clone, so the ID stays unique). Sessions hamburger on mobile stays reachable (`.chat-sessions-btn`). | Template move of `#model-selector`. WS pulse can sit in ⋯ or a 4px status on send. |
| **A4** | **Message craft** | Assistant: document measure (~65–72ch), heading/list/code/table typography, no “blue blob vs gray panel” carnival. User: quieter — same type, subtle bubble or flush right, not a heavy filled capsule. Streaming caret stays. | CSS only on `.message-assistant .message-text`, `.message-user .message-text`, `pre/code/table`. Light + dark. RTL padding-inline. |
| **A5** | **Tool timeline** | `.agent-progress` reads as a compact workbench: kicker + title + count; steps as a short list; collapsed by default when done. No new JS structure. | `kazma.v5.css` only. |

### B — Navigation IA (after A; same visual language)

| ID | Slice | What changes | How |
|----|-------|----------------|-----|
| **B1** | **Sidebar: Chat first** | Visible: **Chat**, **Workspace**, **IDE**. One **More** (or `<details>`) for Memory, Dashboard, Agents, Research, Documents, Swarm, Knowledge, Time Travel, Settings, Skills, MCP. Active page in More still highlights and **auto-opens** More. Keyboard `⌘1`/`⌘2` stay. Footer model + avatar stay. | `sidebar.html` regroup. CSS for nested nav. Do not remove hrefs. |
| **B2** | **Sessions list** | Session rows: title + relative time, less kbd chrome, New session as a text+icon control not a fat primary brick. | CSS + small `chat.html` class cleanup. No session API change. |
| **B3** | **Mobile** | Chat: bottom-nav Chat stays. Sessions via existing `.chat-sessions-btn` (must remain visible — v5 once hid the wrong hamburger). Composer ⋯ usable at 44px tap (`--tap`). | Viewport 375 and 768. `x-cloak` on new drawers. |

### C — Hygiene (bounded)

| ID | Slice | Cap |
|----|-------|-----|
| **C1** | Inline `style=` on `chat.html` you already touch | Move to v5 classes. Do not hunt the whole app. |
| **C2** | Icon buttons you touch | `aria-label` (attach, send, ⋯, sessions). |
| **C3** | `prefers-reduced-motion` | Disable pulse/glow on `.agent-progress-pulse` / `.pulse-dot`. |
| **C4** | Dashboard | **No restyle season.** Optional one-line empty state copy only if you land on `/dashboard` during verify. |

### D — Wontfix here

| ID | Item | Why |
|----|------|-----|
| D1 | Rewrite `chat.js` | Distant invariant; Telegram-class regressions. |
| D2 | React / Vite SPA | Not this product. |
| D3 | New palette / logo | Abyss freeze. |
| D4 | Extra webfonts | Already Inter + Arabic stack. |
| D5 | Make Dashboard the hero | Wrong surface. |

---

## DAG

```
A1 immersive chat shell ──┐
A2 composer ⋯ ────────────┼──► A3 model into dock
A4 message typography ────┤
A5 progress CSS ──────────┘
        │
        ▼
B1 sidebar regroup ──► B2 sessions ──► B3 mobile
        │
        ▼
C1–C3 opportunistic a11y / inline-style cleanup
        │
        ▼
WP-FINAL: browser verify chat desktop + mobile; inspectors unchanged
```

Ship order (one visual gate each):

1. **A1** hide chat header  
2. **A2** composer ⋯  
3. **A3** model in dock  
4. **A4 + A5** type + progress (same CSS PR is OK)  
5. **B1** sidebar  
6. **B2 + B3** sessions + mobile  
7. **C** only on files already dirty  

---

## Success gates

| # | Gate | Verify |
|---|------|--------|
| G1 | Chat first paint | No “Chat / Home / Chat” breadcrumb. Transcript + composer dominate. |
| G2 | JS unbroken | Send, stream, stop, attach, HITL Once/Deny, `/yolo` pill still toggles (from ⋯). `#model-selector` still switches model. |
| G3 | IDs | Every load-bearing ID still unique in the DOM (hidden OK). |
| G4 | HITL | Approval card still appears in chat; dashboard pending list still works. |
| G5 | RTL | Arabic message + composer; no clipped send/attach. |
| G6 | Mobile | Sessions list reachable; composer ⋯ tappable; bottom-nav Chat active. |
| G7 | Inspectors | `/dashboard` `/settings` `/ide` `/swarm` `/memory` still have the **page header**. |
| G8 | Rollback | Removing `kazma.v5.css` link must not *break* chat (may look older). Template A1/A2/A3 should still function unstyled. |
| G9 | Docs | [Web UI](../docs/guide) + recent-features: chat is the product; dashboard is ops. This GOAL linked from intro. |
| G10 | No god-file rewrite | `chat.js` diff empty or ≤ trivial className if unavoidable — prefer zero. |

---

## Browser verification (mandatory for UI)

After operator restart:

1. `/chat` empty state + send a short turn + streaming.  
2. Open ⋯, toggle Long, send, Reset.  
3. Danger tool → HITL card Once / Deny.  
4. Switch model. New session. Search sessions.  
5. `/dashboard` and `/settings` still show header.  
6. Width 1280 and 375.  
7. Light and dark.  
8. If `dir=rtl`: one Arabic turn.

A single screenshot is **not** verification.

---

## Key decisions

1. **Chat is the product; dashboard is an inspector.**  
2. **Hide, don’t delete** ops controls (YOLO, cost, model).  
3. **v5 CSS + templates**, not a third stack.  
4. **IDs are the contract with `chat.js`.**  
5. **No `chat.js` rewrite** in this goal.

---

## Operator proceed

Executed after operator **proceed** (2026-08-26). Templates + `kazma.v5.css` only; `chat.js` untouched.

Restart the server to see the shell. Rollback of look: drop the `kazma.v5.css` link (layout still works).
