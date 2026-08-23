# Plan: Turn Delivery V2 — Event-Sourced Cursor Delivery (One-Shot Replacement)

**Date:** 2026-08-23
**Status:** IMPLEMENTED (P0–P4 shipped 2026-08-23; P5 Web Push deliberately deferred as opt-in follow-up)
**Supersedes (delivery portions of):** `docs/plans/RELIABILITY_MODEL_AND_TURN_DELIVERY.md` (2026-08-03) — that plan shipped `turn_complete` contracts + pollers + watchdogs as incident responses. They worked individually; collectively they form six overlapping recovery heuristics whose interactions are themselves the remaining bug class ("reply invisible until F5 after tab switch").

---

## Governing rule (user directive, binding on every PR)

> Every fix must answer YES to both: (1) **Is this the industry-standard fix for this problem class?**
> (2) **Is it the best-known instance of that standard — i.e., does it remove the root cause class, not the incident?**
> A change justified only by "this incident happened" and that leaves the class reproducible by another path is REJECTED, even if it works.

Consequence: this plan **deletes** the incident-driven mechanisms it replaces in the same PR series that lands their replacements. No dual paths, no "keep both for safety".

---

## Problem statement

When the user leaves the chat page (browser tab switch, in-app soft-nav, sleep) during a running turn and returns, the response often does not appear until a manual refresh. Refresh always works because it renders unconditionally from SessionStore.

### Root cause (architecture, not any single bug)

The system has two delivery models and no protocol connecting them:

1. **Live streaming** (WS frames / SSE tokens) — connection-bound, lossy. On disconnect, mid-turn events are dropped by design (`_make_ws_sender.send` returns False; `is_lost()` skips emission — `ws_chat.py:70-110`).
2. **Durable truth** (SessionStore messages + `active_turns` registry) — authoritative, complete.

Between them sits **client-side reconciliation built entirely from throttled timers and text-matching heuristics**, accumulated incident-by-incident:

| Mechanism | Location | Added for | Failure mode |
|---|---|---|---|
| Nuclear poll | `chat.js` 3s interval | delivery insurance | Chrome intensive-throttles to ≤1/min hidden |
| Background-turn poller | `_pollBackgroundTurn` | refresh recovery | same throttling; 40-attempt caps; fights live stream |
| `_reconcileDelivery` gates | debounce + `expectReply` skip + 10-min activity window | cheap-skip | swallows the one resume attempt after long hides |
| Bubble-delivery text matching | `_bubbleShowsContent` ≥90% prefix, `data-final-len`, fingerprints | dedupe | false "already delivered" ⇒ reply never painted |
| WS staleness watchdog | `agentStore.js` 5s interval | YOLO-silent 2026-08-21 | detection latency balloons exactly when hidden |
| Reconnect string-matching | `"Reconnected — previous turn still running…"` regex | re-arm poller | brittle copy-coupled protocol |

Browsers throttle **timers** in hidden tabs (1s min; ≤1/min after ~5 min) but never network callbacks. Therefore: a healthy socket keeps painting in a hidden tab; every failure requires a *dead-or-detached* socket **plus** timer-driven detection/recovery — precisely the components that degrade while hidden. The class is structural.

Secondary latent defects the same architecture causes:

- **SSE cannot reattach mid-turn.** New page load during a running turn gets rejected (`turn_busy`) and falls back to blind polling. WS has live-socket rebinding; SSE does not (transport parity gap).
- **Multi-tab on one session silently breaks the loser.** `bind_live_socket` is single-slot (last connect wins); the older tab just stops receiving frames with no error and no catch-up trigger.
- **In-app soft-nav destroys the socket by design** (`nav.js:teardownLiveSockets` → `destroyChatMouth` → `agentStore.disconnect()`), making return-to-page depend wholly on the heuristic reconciler.

---

## Target architecture: journal + cursor resume + snapshot reconciliation

One idea replaces all six mechanisms:

> Every turn event is **appended to a per-thread journal with a monotonic sequence number**. Clients hold a **cursor** (`last_seq`) and on (re)connect say "resume from N"; the server replays N+1…head, then streams live. The client renders from a local turn-state object (a pure function `state → DOM`), and **any doubt triggers an unconditional snapshot resync** (status + messages fetch → rebuild → render). Doubt triggers: tab become-visible, focus, pageshow, resume complete, seq-gap detected, terminal-without-content.

This is the standard realtime-delivery architecture:

| Pattern | Reference implementations |
|---|---|
| Sequence-numbered event log + RESUME opcode | Discord gateway (`RESUME`, sequence ops) |
| `id:` lines + `Last-Event-ID` replay | SSE spec itself; GitHub streaming APIs |
| Replay-buffered SSE bus, subscribe-before-replay | Already shipped in-repo: `swarm_sse.py` + `SSEEventBus.get_history` |
| Render-from-store, refetch-on-doubt (SWR/React Query model; Linear sync engine) | Modern SPA data layer |
| Death detection by the non-throttled party (server ping/pong timeout) | WebSocket protocol ping (RFC 6455); uvicorn `ws_ping_interval` |

### Key decisions

| KD | Decision | Rationale |
|----|----------|-----------|
| KD-1 | **Single emit choke point.** All turn events from both transports flow through `kazma_ui/delivery.py::emit_turn_event(thread_id, event)` → journal append → fan-out to bound sockets/subscribers. | Journalling at one choke point is the only way cursors are trustworthy. Mirrors §7-style "one list / one gate" SoT discipline used elsewhere in Kazma. |
| KD-2 | **Journal is process-local memory, bounded** (per-thread deque, e.g. 2 000 events / 60-min TTL, whichever first; final reply remains durable in SessionStore — unchanged). | Replay only ever needs to bridge a disconnected *user*, not a server restart. Avoids a new DB and WAL contention (§8/§15 split-DB discipline). Swarm bus proves the pattern in-repo. |
| KD-3 | **Seq is assigned at journal append**, stamped on every emitted frame (`seq` field) and on heartbeats. Gaps are detectable clients-side. | Discord-model. Heartbeat-without-seq is why today's watchdog can't tell "idle" from "missing frames". |
| KD-4 | **Client paints from state, not events.** A single `TurnChannel` owns transport + cursor + dedupe; it mutates a plain turn-state object; one `render()` applies state→DOM idempotently. Text-comparison "did it render?" heuristics are deleted. | Kills the entire false-"already delivered" bug family structurally. |
| KD-5 | **Resync is unconditional and gate-free.** `resync()` = GET status + GET messages → rebuild tail state → render. No debounce windows, no `expectReply` gating, no activity-window expiry. Fired on: visible / focus / pageshow / resume-done / seq-gap / terminal-without-content. | SWR principle: reconciliation must be safe-by-construction (idempotent), so it never needs guarding. |
| KD-6 | **Resume protocol on both transports.** WS: first client frame `{"action":"resume","last_seq":N}` → server replies `{"type":"resumed",…}` → replays journal → live (subscribe-before-replay, no-gap). SSE: `id:` on frames; reconnecting POST carries `last_event_id`; server replays then attaches live — including attaching to a **running turn started by another connection** (parity with WS rebind). | Protocol-grade completeness; fixes SSE-reattach and multi-tab simultaneously. |
| KD-7 | **Death detection moves to the party that never throttles — the server.** Verify/enforce uvicorn WebSocket protocol ping (`ws_ping_interval`/`ws_ping_timeout`); a black-holed socket is closed server-side, `send` raises, journal retains everything. Client-side liveness runs in a **Web Worker timer** (worker timers are not subject to tab intensive-throttling) that posts a single "check now" message to the page; the page acts only if actually stale. The `setInterval` watchdogs die. | Inverts the 2026-08-21 watchdog: instead of a throttled client guessing, the server certifies death and the client recovers via resume. Worker-timer is the known industry workaround for background-tab timer throttling. |
| KD-8 | **Structured handshake, zero string matching.** The reconnect "previous turn still running" prose frame and the client's regex on it are replaced by the `resumed` frame carrying `{mode: "live"|"caught_up", running: bool, last_seq: M}`. | Copy-coupled protocols are latent bugs (proven by incident annotations in `agentStore.js`). |
| KD-9 | **HITL rides the journal.** `approval_required`/clarify frames are journaled like any event; replay re-renders the card idempotently (render-from-store makes double-render impossible). Existing scan-on-connect stays as belt-and-braces. | §7 invariant: HITL must survive every detach path; making approvals ordinary journaled events removes their special case. |
| KD-10 | **Additive wire compat, hard cutover internally.** Old clients (stale cached JS) keep working: frames gain optional `seq`; absence of `resume` first-frame ⇒ legacy behavior (today's catch-up) server-side. Internal client code has exactly one path (KD-4/5) — no feature flag leaving both alive. | Cached-JS stragglers must not break during deploy; but the codebase must not carry two client architectures. |

---

## Architecture

### Server: `kazma_ui/delivery.py` (new, ~250 lines)

```
TurnJournal (per thread_id)
    append(event_dict) -> seq            # assigns seq, stamps, prunes by TTL/cap
    replay(after_seq) -> [events]        # bounded; caller decides overflow strategy
    head_seq() -> int

TurnBroker
    register_socket(thread_id, ws, conn_id)     # MULTI-slot (fixes multi-tab)
    unregister_socket(thread_id, conn_id)
    async emit_turn_event(thread_id, type, data) -> seq
        # 1. journal.append  2. stamp seq  3. fan-out to all bound sockets,
        # never raise per-socket (mirror _make_ws_sender semantics)
    subscribe_sse(thread_id, after_seq) -> async iterator   # replay-then-live, swarm-style
```

Both `ws_chat.py` and `sse_chat.py` convert their existing `TelemetryEvent` emissions to `emit_turn_event(...)` calls. The SSE detached pump keeps its survival semantics (§ active_turns) — it simply journals instead of yielding into a dead response.

### Wire contract (v2, additive)

```jsonc
// every server frame gains:
{ "type": "...", "data": {...}, "seq": 184 }

// client → server, FIRST frame after open (or query param ?last_seq=):
{ "action": "resume", "last_seq": 180 }
// server → client:
{ "type": "resumed", "data": { "from": 180, "to": 184, "running": true } }
// ...then journal entries 181..184 verbatim, then live.

// SSE: "id: <seq>\n" line on every event; reconnecting POST body carries
// "last_event_id"; server replays then attaches (to running turn if any).

// heartbeat (unchanged cadence) gains:
{ "type": "status_update", "data": {"status":"thinking", ...}, "seq": 191 }
```

Overflow policy: if `after_seq` predates journal retention, server sends `resumed {gap: true, from: <oldest-1>, to: head}` and the client goes straight to snapshot resync (KD-5). Correctness never depends on replay depth.

### Client: `static/js/modules/turn_channel.js` (new) + chat.js/agentStore.js surgery

```
TurnChannel
  - transport mgmt (WS primary w/ backoff, SSE POST fallback), cursor, dedupe-by-seq
  - gap detection => onGap -> resync()
  - Web-Worker-backed liveness ticker => stale check only acts when truly silent mid-turn
  - emits semantic callbacks into a plain turnState:
      {phase: idle|thinking|tool|awaiting_approval|synthesizing|done,
       tokenBuffer, toolRows[], approval?, stats?, error?, lastSeq}

chat.js render(turnState)   # pure-ish, idempotent: bubble text, workbench rows,
                            # approval card, badges, Stop-button state
chat.js resync()            # unconditional snapshot rebuild (status+messages)
```

Deleted (same series): nuclear poll, `_pollBackgroundTurn`, `_reconcileDelivery` + its gates, `_bubbleShowsContent`/`_domMissingAssistantReply`/`_softApplyFinalAssistant`/fingerprint stamps, agentStore `_stalenessTimer` + reconnect-string regex + `pollBackgroundTurn` arming, `TURN_IDLE_WATCHDOG` force-finalization path, `replay:true` special casing (folded into resume replay).

Kept unchanged: detached pump + `DETACHED_TTL_S` reaper, SessionStore persistence, HITL graph interrupts + resume endpoints (§7A), keepalive comments, platform isolation (§2 — none of this touches gateway state).

### Long-task visibility UX (small, standard)

While hidden: title/favicon badge increments on tool events; on terminal, fire a page `Notification` (permission-prompted once, Settings toggle, default on). Frozen/discarded tabs (Chrome Memory Saver) are covered because on revival the first act is `resync()` — notification for those cases arrives in Phase 5 via Service Worker + Web Push (VAPID), deliberately separate.

---

## Implementation phases (stacked PRs, each green in CI before next)

### P0 — `delivery.py` + journal + broker (server foundation)
- New `kazma_ui/delivery.py` per above; unit-tested standalone (fake sockets).
- No transport rewired yet. Metrics: `kazma_delivery_events_total`, `kazma_delivery_replayed_total`, `kazma_delivery_seq_gap_total`.
- Tests: seq monotonicity; TTL/cap prune; replay bounds; multi-socket fan-out; per-socket exception isolation.

### P1 — WS resume protocol
- `ws_chat.py`: route all emissions through broker; handle `resume`; `resumed` handshake; heartbeats carry seq; multi-slot sockets.
- Legacy clients (no resume frame): today's catch-up behavior preserved server-side (KD-10).
- Tests: fake-socket integration — disconnect mid-turn, reconnect with last_seq, assert byte-identical replay + live continuation; no-gap subscribe-before-replay; HITL card replays once.

### P2 — SSE parity (`sse_chat.py`)
- `id:` lines; `last_event_id` handling; attach-to-running-turn for a second connection; pump journals instead of direct yield (response writer drains journal-subscription — this also simplifies the detached case).
- Tests: reattach mid-turn over a fresh POST returns full continuation; terminal-after-persist ordering.

### P3 — Client cutover (`turn_channel.js` + surgery)
- Build channel + render-from-store; flip transports to it; **delete the patch pile** (list above) in the same PR.
- Resync triggers wired; worker-timer liveness.
- JS contract tests per house convention (source assertions) + node-executable pure-reducer tests if a runner exists; `node --check` gate green.
- Manual matrix (user-run, server restarted by user): quick tab switch; >5-min hide; DevTools-offline kill/restore; in-app nav away/back; second tab same session; HITL approval across all six.

### P4 — Visibility UX + cleanup
- Title/favicon badge + Notification on terminal while hidden (Settings toggle).
- Remove now-dead helpers; grep-audit for orphan references (§24 import-integrity discipline applied to JS: no dangling `KazmaChat.*` consumers).
- CHANGELOG + this plan marked implemented.

### P5 — (Separate, opt-in) Web Push for frozen/discarded tabs
- Service worker + VAPID push on turn completion; only valuable beyond P4 for Memory-Saver-discarded tabs. Not a dependency of the core fix.

---

## Risk register

| Risk | Mitigation |
|------|------------|
| chat.js imperative DOM code resists render-from-store | Introduce `turnState`+`render()` alongside; migrate handler-by-handler within P3; deletions land in same PR so no fork lives past review |
| Replay duplicates paint (double bubbles) | Dedupe belongs to channel (by seq), render is idempotent by construction; regression-test the exact 2026-08-16 double-answer scenario |
| Cached old JS vs new server | Additive fields only (KD-10); legacy server catch-up retained for resume-less clients |
| Journal memory growth (many threads) | Per-thread cap + global LRU ceiling + 60-min TTL; metrics expose occupancy |
| Worker timers unavailable (edge browsers/file://) | Feature-detect; fall back to visibility-event-driven checks only (still correct under KD-5) |
| §7 HITL regressions | Approval flows exercised in P1/P3 tests + manual matrix; scan-on-connect kept |
| CI (§24) | py_compile all, `node --check` static JS, `scripts/fast_test.py` chunks green each PR; `tests/test_imports.py` green after any module addition/deletion |

## Non-goals

Gateway/TUI delivery (their own paths, unaffected), multi-user session-scoped delivery topology, LangChain BaseChatModel streaming migration, durable event journal across restarts.

---

## Success criteria (all must hold, verified by tests + user manual pass)

1. Hide tab mid-turn for any duration (incl. >5 min, incl. machine sleep), return → reply visible ≤ ~1 s, never requiring refresh — regardless of whether the socket survived.
2. Network killed mid-turn and restored → zero lost text: either seamless replay (cursor) or one clean snapshot resync; no partial-stuck spinners.
3. In-app soft-nav away and back mid-turn → same guarantee as (1).
4. Second tab on same session: both tabs stay live (multi-slot broker).
5. No client code anywhere compares rendered text against desired content to decide whether to paint.
6. All six replaced mechanisms are gone from the tree; their historical incident scenarios are covered by named regression tests.
