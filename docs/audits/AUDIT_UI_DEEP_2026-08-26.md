# UI Deep Audit — 2026-08-26

Full audit of the Kazma Web UI (templates, static JS incl. modules/stores,
CSS, navigation engine, auth flow, asset caching), triggered by three live
symptoms: **page switches reload 2-3 times before content appears**,
**no top status bar on any page**, and **fixes appearing to change nothing**.

Three parallel deep sweeps + runtime probes against the live deployment
(all page routes verified 200 with auth; `/dashboard` unauthenticated
303-bounces to `/login`).

---

## Verdicts on the three symptoms

### 1. "Switching pages reloads 2-3 times" — deterministic root cause

Every sidebar link is soft-navigated (fetch + `.page-body` swap,
`modules/nav.js:576-601`); **any swap failure falls back to
`window.location.href` — a second full load** (nav.js:568-571).

The swap into Chat fails **every time**: chat.html:63 and :84 carry bare
`x-data` attributes; nav.js's classifier (`isEmptyAlpineBind`,
nav.js:144-152) treats Alpine's zero-key empty data stack as "unbound",
retries ~500ms, then throws → full-reload fallback (load #2). A re-click
on the stuck-looking item hits the same-URL rule (nav.js:583-591, no
`preventDefault`) → native navigation (load #3). On the hard load
everything works — matching the reported behavior exactly.

This is a recurring class: the 08-17 series (`f922e650`, `78209dfc`,
`52d0e29e`, `2fcbff6d`) fixed the Settings/Memory "first-click empty
page" variants; the Chat bare-`x-data` variant was never addressed.

Secondary chains (conditional):
- **Session-expiry bounce** (non-loopback only): soft-nav fetch gets 303
  → `/login` → back = 2 loads per switch (auth.py:147-163, nav.js:489-491,
  auth-guard.js:41).
- **Factory/ready timeouts**: 4s (nav.js:132-142) / 8s (nav.js:285-321)
  waits on Settings-class pages → throw → reload ("spinner then sudden
  reload").
- **Impatient re-clicks queue a second softNav** that races the unload.

### 2. "No top status bar on ANY page" — half design, half real regression

- **Chat page: by design.** `bdda10a4` hides `.page-header` on `/` via
  `.app-layout.is-chat` (base.html:46, kazma.v5.css:380). Plan goal G7
  (docs/plans/CHAT_AS_PRODUCT_UI_GOAL.md:171): all other pages keep the
  header.
- **Every other page: real bug.** `is-chat` is stamped server-side only —
  soft-nav swaps `.page-body` and syncs title/breadcrumbs
  (`syncChrome`, nav.js:105-119) but **never syncs `.app-layout`'s
  class**. Load chat → soft-navigate anywhere → `is-chat` sticks →
  header `display:none` on dashboard/settings/ide/swarm/… **plus**
  `.page-body` keeps chat's `padding:0; overflow:hidden`
  (kazma.v5.css:381-386) leaking into those pages. Reverse direction:
  soft-nav INTO chat leaves `is-chat` off → header shows over the
  immersive chat.
- One-line fix direction: sync the layout class in `syncChrome()`/after
  the body swap (`oldLayout.className = newLayout.className`).

### 3. "Nothing changed" after fixes — two mechanisms

- **P0-1: `$store.agent` never registers on soft-nav arrival.**
  `stores/agentStore.js:15` registers only on `alpine:init`, which has
  already fired when soft-nav re-injects the script. Consequences (all
  silently guarded → invisible failure): `_setStatusStrip` no-ops (the
  460c5311 strip fix never shows), `Alpine.store('agent').connect()` is
  skipped (no WS bus), every `$store.agent` x-show evaluates falsy
  (thinking strip / WS dot / HITL bottom card hidden). Fix: register
  synchronously when `window.Alpine` already exists.
- **P1-2: stale-cache split-brain.** `streaming.js` is loaded versioned
  (`?v=`) on chat but **unversioned on 7 other templates**
  (dashboard.html:312-315, ide.html:309, swarm.html:834-835,
  workspace.html:1517, agents/skills/research…); `ide.js`, `swarm.js`,
  `dashboard.js`, `dash_lists.js`, `hitl_approval.js` unversioned;
  `replay.js?v=2` hardcoded. The `js_version()` whitelist (app.py:525-555)
  also misses `dashboard.js`, `ide.js`, `swarm.js`, `skills.js`,
  `agents.js`, `kb.js`, `documents.js`, `research.js`, `voice.js` and
  `modules/*`. Static mount has no immutability → unversioned scripts
  cache heuristically → **stale JS with fresh HTML after every deploy**.
  This is the "works in chat, broken on dashboard until hard reload"
  class, and why some fixes appeared to do nothing without a hard refresh.

---

**Remediation status (2026-08-31):** Phase 1 P0s, Phase 2, and Phase 3
landed in code (`TestUIAuditP0Fixes` / `TestUIAuditPhase2/3Fixes`, CHANGELOG
2026-08-26). This document is the original diagnosis — do not re-open the
P0s as live defects. Remaining: real iPhone Light/Dark sign-off (process),
and opportunistic `chat.js` splits when touching that file.

## P0 — user-visible breakage (fix first)

| # | Defect | Evidence | Fix direction |
|---|---|---|---|
| P0-1 | `$store.agent` unregistered after soft-nav → strip/WS/HITL-bottom dead | agentStore.js:15; nav.js:32-41, 220-262 | Register immediately if `window.Alpine` exists |
| P0-2 | `bindCapacityBar` relocates `#capacity-bar` out of the ⋯ popover on every chat load → popover opens empty, bar renders without its layout rules; JS-created fallback bar diverges from template (extra Plan pill) | chat.js:4254-4299 vs chat.html:121-140, kazma.v5.css:470-474 | One owner: delete the relocation + JS fallback |
| P0-3 | Sticky `is-chat` after soft-nav → header hidden on ALL pages + chat body CSS leaks | base.html:46, kazma.v5.css:380-386, nav.js:105-119, 531-532 | Sync `.app-layout` class in syncChrome |
| P0-4 | Soft-nav into Chat always falls back to full reload (bare `x-data` classified unbound) | chat.html:63,84; nav.js:144-167, 355-376, 568-571 | Treat zero-key bare `x-data` as bound (or give the two divs real state) |

## P1 — glitches

1. **Triplicated keyboard shortcuts** — components.js:234-239 (Ctrl+1-6,
   hard loads) vs nav.js:613-648 (Ctrl+1-8, soft-nav) vs chat.js:4560-4573
   (Ctrl+K/N). Ctrl+1 lands on `/` not `/workspace`; Ctrl+2-6 always
   double-navigate; Ctrl+K focuses the wrong field on chat.
2. **Unversioned script tags** (the split-brain above): sweep `?v=` onto
   the 9 sites; fix `replay.js?v=2`; extend the `js_version()` whitelist
   (or derive it from the directory).
3. **209 of 322 x-show sites lack x-cloak** — flash-on-load class. Worst:
   settings.html:1555-1560 ("Vault Disabled" red alert flashes when vault
   IS enabled), settings empty-states (137/156/213/343/440/1061/1448/1511/
   1684/2410), knowledge_base spinners (92,103,150,154-156), double-icon
   flashes (header.html:77,82; sidebar.html:22,26), sidebar layout jump
   (sidebar.html:11,192), modal.html:64 overlay.
4. **Swarm breaker duplicate IDs** — swarm.html:493,495 both
   `id="cb-badge-{{w.name}}"`; swarm.js:378 can only ever reach the first
   → green "closed" badge unreachable.
5. **Double `loadSession` on every chat boot** — chat.js:221-230 (+100ms)
   and chat.js:1384-1396 (immediate) → two fetches/renders/flickers.
6. **Resync race** — a focus-triggered `_resyncDelivery` in flight when a
   new message is sent can briefly paint the previous reply into the new
   turn's bubble (chat.js:341-398 guards only session id).
7. **Zero-catch fetch clusters** — settings_hub.js 33 fetches / 0 catches;
   mcp.js 8/0; kb.js 11/0; models.js 6/0; providers.js 6/0; skills.js 8/0;
   ide.js 2/0. Failures clear `loading` and render empty-states as truth.
8. **Imperative display writes on JS-toggled elements** remain in
   swarm.js:374-389, hitl_approval.js badge, streaming.js showTyping on
   non-Alpine elements — same class as the strip bug, latent.

## P2 — hygiene

- Dead CSS: `.chat-model-bar`/`.chat-input-area` defined twice in
  kazma.css (1826 block dead for its only consumer) — prune the old
  top-bar rules.
- Voice UI: v5 hides `.composer-voice-btn`/`.composer-mic-recording`
  (kazma.v5.css:477-478) while chat.html:150-165 renders the buttons and
  voice.js (619 lines) stays wired — decide: retire or restore.
- agentStore.js:721-730 dead setTimeout; `_syncThinkingBanner` dead
  branch (216-221).
- Outbox retry button rides markdown raw-HTML (chat.js:313-319) — fragile
  vs the post-render injection pattern (chat.js:1947-1956).
- `components.js:356` fragile `[x-data*="kazmaApp"]` selector.
- Importmap covers 5 of 9 module paths — stale-module trap for future
  edits; x-cloak rule single-sourced in kazma.css only.
- Inert `{% block page_title %}` overrides (Jinja blocks don't cross
  `{% include %}`) — header titles always `active_page|title`.
- `recoverMissedApproval` "single pending, take it" heuristic
  (chat.js:3296) can paint another session's approval into this UI.
- `hasLiveSSE()` is `!!activeStream` — any future unrelated SSE stream
  would suppress WS painting.

## Commit review (last 2 days)

Every functional commit's fragile pattern is catalogued in the sweep;
notable: `460c5311` (strip single-owner) is correct on hard loads but
depends on P0-1; `501929f6`'s module-global `_sseEpoch` breaks with two
concurrent approval cards; `9778cd1c`'s `_reopenSseRef` is per-send — a
resync between sends re-attaches with the previous closure's `content`;
`b8415055`'s recovery heuristic noted above.

## Remediation plan (phased)

**Phase 1 — the four P0s** (reload storm, missing header, dead store,
broken popover). All are small, surgical, and unlock each other:
1. nav.js: sync `.app-layout` class in syncChrome (P0-3).
2. nav.js: accept bare `x-data` as bound (P0-4) — kills the deterministic
   double-reload.
3. agentStore.js: synchronous registration when Alpine exists (P0-1).
4. chat.js: delete capacity-bar relocation/fallback (P0-2).

**Phase 2 — trust & perception**: version-string sweep + whitelist fix
(P1-2), single keyboard-shortcut registry (P1-1), `.catch`/toast pass
over the zero-catch clusters (P1-8), double-loadSession (P1-5), resync
race guard (P1-6), swarm badge id (P1-4).

**Phase 3 — polish**: x-cloak pass over the worst flash sites (vault
alert, settings empty-states, modal overlay, double icons), dead-CSS
prune, voice-UI decision, dead code, importmap coverage.

Verification per phase: `node --check` on touched JS, the existing
source-contract tests extended for each fix, and manual matrix — hard
load + soft-nav into/out of chat on `/`, `/dashboard`, `/settings`,
checking: one load per switch, header present on non-chat pages, strip
arms on turn start, ⋯ popover intact.
