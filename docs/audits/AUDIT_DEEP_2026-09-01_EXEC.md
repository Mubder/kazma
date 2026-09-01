# Execution SoT for AUDIT_DEEP_2026-09-01

The finding dump is `AUDIT_DEEP_2026-09-01.md`. **Do not follow its Part 6 order.**
This file is the in-repo execution contract so Turn Delivery and the HITL
Gate Registry (P6) are not restacked by a naive “do the audit” pass.

Verified against `main` at plan time (`20e9d198` and later).

## Skip / recast

| ID | Do this |
|---|---|
| **C-1** | False on current tree (`Bearer {token}`). Guard test only. Never “restore” headers. |
| **T-4** | Retarget projector tests. Do not restore `tokenAccum` dual-paint. |
| **H-7** | Residual DNS-rebinding; dedicated pin-IP design later, not Day-0. |
| **H-12** | Swarm bus tri-state only. Must not retarget web `claim_gate`. |

## Collision recipes (protected)

1. **T-4 / `chat.js`:** scrub stays inside `renderTurn`. No second painter.
2. **H-8 / `tool_registry.execute`:** apply `rewritten_args`; `clarify`/`confirm` fail closed (“run from chat”). Never mint a second gate row.
3. **T-2 pipeline timeout:** finalize the task **and** `settle_gate`.
4. **H-9 bus:** `is_danger_tool()` → `requires_approval()`. Not FanOut first-wins.

Protected files: `chat.js` (`_paintHitlFromDoc`, `renderTurn`, `_hitlAlreadyClaimed`, `_serverGates`), `turn_document.js`, `turn_runtime.py` (`close_turn`), `hitl_gates.py`, `hitl_status.py`.

## Waves

- **0** Preflight: this file + C-1 guard + HITL/delivery baseline green.
- **1** Day-0 security: H-3, H-6, H-5, H-4, T-3, H-14.
- **2** Honest suite: T-6, T-1 (do **not** count commitment denials as tool failures; register dummy tools), T-4 retarget, T-5. **Shipped.**
- **3** Real critical: C-2 importer order + M-9/M-10/M-11. **Shipped.**
- **4** HITL-adjacent: H-9, M-3, H-8, T-2. **Shipped.**
- **5** Isolation / cron / PG docs / workspace_id: H-1, H-2, H-13, H-10/H-11. **Shipped.**
- **6** Swarm reliability: H-12 fan-out tri-state (swarm bus only, not web
  `claim_gate`); M-14 remainder (autoscaler skip-busy + activity at
  completion; breaker refresh + durable probe lease; PG metrics SQL-side
  upsert; MCP list/read through scope guard; path-write keywords from
  `side_effects` SoT). **Shipped.**
- **7** Correctness hygiene: M-1/M-2 transient classification; M-4/M-6
  spawn_background; M-5 Slack private-file prefetch (token stays off graph
  state); M-7 slack/discord output ids; M-8 full-state fork; M-12 cron
  timezone; M-13 firing-ledger last_run + plain-log timestamps; M-14 leftover
  (stale-PG from cadence, voice safe_error + WS cancel, embedder spawn,
  sqlite off-loop, /documents+/scheduled sensitive, telegram webhook open
  prefix, document/upload rate limits, top_k clamp). **Shipped.**
- **8** Lows + leftover high (H-7 pin-IP SSRF last).

Industrial minimum if we stop early: Wave 1 + C-2 + T-4 retarget.

## Per-PR

Finding ID in the subject. Behavior test + negative control. No server start.
Operator: `kazma_guard.py --reload` after Python; Ctrl+F5 after JS.
