# HITL Gate Registry — One Gate, One Row, One Truth

**Status:** SHIPPED 2026-09-01 (all phases P0–P6; legacy derivation retained
only as the kill-switch/degradation path — see AGENTS.md §30)
**Scope ruling (binding):** this is **gate identity for Turn Delivery, not a new
delivery system**. The registry owns the *decision*; the existing turn story
paints it. Gate changes surface as `hitl` parts of the SAME TurnDocument via
the SAME turn journal — never a parallel event stream. Surfaces render; they
do not mint.
**Unification ruling (binding, 2026-09-01):** ALL FOUR approval mechanisms
(graph pause, swarm bus, pipeline checkpoints, semantic cards) converge on
this one registry in THIS run — no surface keeps a private approval truth.
The rollout is strangler-sequenced (web first, parity-watched, then gateway,
then swarm/pipeline) so each cutover lands green, but nothing is deferred:
the run ends only when the last legacy derivation is deleted (P6). This is
the industry-standard shape (Step Functions task-token / Temporal signal:
one token per pause, one central authoritative store, idempotent decision
records, every UI queries and submits through the same API).
**Born from:** the 2026-09-01 chat-card incident chain (ghost cards, pre-approved
stamps, second question hidden on the dashboard, fake wrap-up while paused) and
the follow-up fix that closed the symptoms but left gate identity re-derived in
three places.
**Owner surfaces:** Web SSE chat, dashboard, gateway (Telegram/Discord/Slack
`/hitl` + buttons), TUI, swarm bus, pipeline checkpoints, commitment-layer
semantic interrupts.

---

## 1. Definition of "fail-safe" (what "never fail" means here)

No system never fails. Fail-safe means **every failure mode has one defined,
tested behavior, and that behavior is always the conservative one**:

| Failure | Defined behavior |
|---|---|
| Registry DB unreachable | Classification degrades to today's snapshot-derived path (warn loudly). Approval of a gate that cannot be *verified* is **refused** with an actionable error — never silently granted. |
| Crash between `interrupt()` and registry write | Boot/periodic reconciler creates the missing row from the checkpoint (checkpoint = execution truth). The card appears; nothing is lost. |
| Crash between claim and resume | Row is `claimed`; reconciler sees no live drive → re-arms the resume or (past TTL) settles as `timeout` and tells the user. Never a silent hang. |
| Two approvals race | SQL compare-and-set: exactly one wins; the loser gets 409 `already_claimed` with the winner's decision. Idempotent repeat of the *same* decision returns 200. |
| Gate expires unanswered | `timeout` transition per config (auto-deny), emitted as an event — the card visibly times out everywhere at once. |
| A reader and the registry disagree | Registry wins for *decision* state; checkpoint wins for *execution* state. The reconciler converges them; disagreement is metered, never hidden. |

**Design law:** ambiguity always resolves toward *showing a live card* and
*refusing to assume approval*. (Same posture as §26B default-deny HITL.)

---

## 2. The core: `kazma_core/safety/hitl_gates.py`

One module, one SQLite store (`kazma-data/hitl_gates.db`, WAL +
`busy_timeout=5000` — house pattern §6/§8), one state machine.

### 2.1 Schema

```sql
CREATE TABLE hitl_gates (
  gate_id      TEXT PRIMARY KEY,   -- LangGraph intr.id (SoT); hash fallback
  alias_id     TEXT NOT NULL DEFAULT '',  -- pre-pause hash id when it differs (two-id window)
  thread_id    TEXT NOT NULL,
  tenant_id    TEXT NOT NULL DEFAULT '',
  session_id   TEXT NOT NULL DEFAULT '',
  turn_id      TEXT NOT NULL DEFAULT '',
  mechanism    TEXT NOT NULL,      -- 'graph' | 'swarm_bus' | 'pipeline' | 'semantic'
  kind         TEXT NOT NULL DEFAULT 'security',  -- security | semantic_clarify | semantic_confirm
  tool         TEXT NOT NULL,
  args_json    TEXT NOT NULL DEFAULT '{}',
  message      TEXT NOT NULL DEFAULT '',
  payload_json TEXT NOT NULL DEFAULT '{}',        -- full card payload (options, items, yolo_allowed)
  state        TEXT NOT NULL DEFAULT 'pending',
  decision     TEXT NOT NULL DEFAULT '',          -- approve | deny | yolo | option:<id>
  actor        TEXT NOT NULL DEFAULT '',          -- who claimed (platform:user / web:principal)
  supersedes   TEXT NOT NULL DEFAULT '',          -- gate_id this one replaced
  created_at   REAL NOT NULL,
  claimed_at   REAL,
  settled_at   REAL,
  expires_at   REAL                                -- TTL → timeout transition
);
CREATE INDEX idx_gates_thread ON hitl_gates(thread_id, state);
CREATE INDEX idx_gates_state  ON hitl_gates(state, expires_at);
CREATE INDEX idx_gates_alias  ON hitl_gates(alias_id) WHERE alias_id != '';
```

**Two-id rule (binding):** `register_gate` is idempotent on BOTH `gate_id` and
`alias_id` — looking up either id lands on the same row, so a pre-pause hash id
and the post-pause LangGraph id can never draw two cards for one pause.
Preferred write site is AFTER the pause is observed (the post-stream scan has
`intr.id` in hand); a pre-pause registration that never pauses is settled
`orphaned` by the same scan within seconds, not by the boot sweep.

**Process honesty (binding):** `hitl_gates.db` is single-process truth, exactly
like the live turn journal. It must NOT be presented as multi-replica shared
state; a Postgres backend is future work and goes next to the other shared
state (§21) when it happens.

### 2.2 State machine (the ONLY legal transitions)

```
pending ──claim(decision, actor)──▶ claimed ──resume_started──▶ resuming ──▶ settled(outcome)
   │                                   │                            │
   ├──timeout──▶ timeout               ├──timeout──▶ timeout        └──error──▶ error
   └──superseded(new_gate)──▶ superseded
```

- Every transition is a single `UPDATE … WHERE gate_id=? AND state=?`
  (compare-and-set). Rows affected == 0 ⇒ `TransitionConflict` carrying the
  row's *actual* state — that IS the 409 body. No lock ordering to get wrong.
- `pending` is the only state a card renders live buttons for. Everything
  else is a terminal or in-flight stamp. The client never infers.
- A **new interrupt on a thread with a non-settled gate** auto-supersedes
  nothing: both rows coexist (gate #1 `claimed/resuming`, gate #2 `pending`).
  `superseded` exists only for re-emission of the *same* execution pause with
  a different id (checkpoint rewind/fork) — reconciler stamps it.

### 2.3 API (sync core + thin async wrappers via `asyncio.to_thread`)

```python
register_gate(gate) -> GateRow            # idempotent on gate_id
claim_gate(gate_id, decision, actor)      # CAS pending→claimed; idempotent same-decision
mark_resuming(gate_id) / settle_gate(gate_id, outcome)
gate_for(gate_id) / live_gates(thread_id) / pending_gates(tenant=None)
expire_due_gates(now) -> list[GateRow]    # TTL sweep
reconcile(thread_id, snapshot) -> Report  # checkpoint ⟷ registry convergence
```

### 2.4 One emitter — into the EXISTING turn story

`register_gate` / `claim_gate` / `settle_gate` publish ONE transition through
a small `GateEvents` hook. In the web app that hook updates the `hitl` part of
the gate's turn **in the turn journal** (same TurnDocument, same SSE tail the
browser is already on) and refreshes the dashboard poll source. It is NOT a
second event stream — a pause is one chapter of one turn, and this hook is the
mechanism that keeps it so. Gateway/TUI adapters (deferred phases) subscribe
to the same hook. **Surfaces render; they never mint.**

---

## 3. Writers (where rows are born and moved)

| Site | Today | After |
|---|---|---|
| `sse_chat/_streaming.py` post-stream scan | mints id, persists part, emits frame | **primary register site** — the pause is observed and `intr.id` is in hand; `register_gate(gate_id=intr.id, alias_id=<payload hash id>)` idempotent on both ids, then emit via GateEvents |
| `graph_builder.py:tool_worker_node` | calls `interrupt()` raw | optional pre-registration under the hash id only; the post-stream scan upgrades it to `intr.id` via `alias_id` match. A pre-registration whose pause never materializes is settled `orphaned` by the same scan within seconds — never left for the boot sweep |
| `routes_direct/misc.py` approve endpoint | lock + stamp part + journal frame | `claim_gate(gate_id, decision, actor)` — the CAS *is* the concurrency control; per-thread asyncio lock stays only for the drive spawn |
| Resume driver | registers turn | `mark_resuming`; `settle_gate` on tool completion/turn close |
| Gateway `/hitl approve\|deny` + platform buttons | own path into graph | same `claim_gate` call — one choke |
| Swarm bus `safety.check()` (§7B) | bus-local approval | gates get `mechanism='swarm_bus'` rows; adapters render from GateEvents |
| Pipeline checkpoints (§7C) | `checkpoint_manager.py` own state | rows with `mechanism='pipeline'`; existing timeout auto-reject becomes the shared TTL sweep |
| Commitment semantic cards (§20B) | unified HITL bus | `kind='semantic_*'`, `payload_json` carries options; `decision='option:<id>'` |

The danger-tool *list* SoT (§7, `CANONICAL_DANGER_TOOLS`) is untouched —
this plan unifies gate *lifecycle*, not gate *policy*.

## 4. Readers (all become one query)

- `/api/pending-approvals` → `pending_gates()` (filtered by tenant). No more
  checkpoint enumeration scan per poll.
- Session status `status.hitl` → `live_gates(thread_id)` (the full list — a
  second gate is naturally visible next to the claimed first one).
- `close_turn` → `live_gates(thread_id)`: any `pending` row ⇒
  `interrupted=True`, keep the turn open. Deletes today's tri-site
  `is_new_gate` derivation (kept only inside the reconciler as cross-check).
- `hitl_thread_status` → thin shim over the registry (kept for API compat,
  body becomes ~10 lines).
- Chat client: renders `gate_*` journal events keyed by `gate_id`; the
  status-poll claim heuristic (`statusInflight`) is deleted — a card is
  claimed iff a `gate_claimed` event (or registry state on resync) says so.
- Dashboard + TUI subscribe to the same events; polling remains only as
  resync fallback.

## 5. The reconciler (what makes it survive crashes)

`reconcile(thread_id, snapshot)` runs (a) inside `close_turn`, (b) on
approve when the gate row is missing, (c) boot sweep + 60s periodic over
threads with live gates:

1. Checkpoint interrupt with no row → create `pending` row (the
   crash-between-interrupt-and-write case). Card appears late, never never.
2. Row `pending/claimed/resuming` with no checkpoint interrupt and no live
   drive → `settle(outcome='orphaned')` after grace; metered.
3. Checkpoint interrupt id ≠ any live row id, row exists for same
   tool/args → stamp `superseded`, register the new id.
4. TTL sweep piggybacks here + on the 15-min commitment GC cadence
   (§15 — no new scheduler loop).

## 6. Rollout — strangler, never big-bang

Each phase lands green and shippable; kill-switch `KAZMA_GATE_REGISTRY=0`
reverts to legacy derivation until Phase 6 removes it.

- **P0 — Core + tests.** Module, schema, CAS state machine. Unit +
  concurrency tests (two claimers, N=100 race loop), crash-recovery tests
  (kill between transitions, reopen DB).
- **P1 — Dual-write, legacy read.** Writers register/claim/settle rows;
  every legacy read path unchanged. A `parity` metric compares registry
  answers vs legacy answers on every status call. Ship, watch counters.
- **P2 — Web read cutover** (pending list, session status, close_turn,
  chat client events) behind the flag, default ON. E2E: the incident
  script — write→approve→delete→second live card in chat→approve→answer,
  plus refresh at every arrow.
- **P3 — Gateway + dashboard/TUI cutover.** `/hitl`, platform buttons,
  panel — all through `claim_gate` + GateEvents. Same lock, next door.
- **P4 — Swarm bus + pipeline + semantic** rows (mechanisms B/C, §20 cards).
  `mechanism='swarm_bus'|'pipeline'`, `kind='semantic_*'`; the pipeline
  timeout auto-reject becomes the shared TTL sweep.
- **P5 — Reconciler chaos tests.** Chaos-inject (§27 harness) registry I/O
  errors, killed drives, duplicated events; assert the defined behaviors
  table above, one by one.
- **P6 — Delete legacy.** `is_new_gate` tri-site derivation (kept only
  inside the reconciler as cross-check), client
  `statusInflight`/`_hitlAlreadyClaimed` heuristics, per-poll checkpoint
  scans, bus-local approval state. Registry becomes the only author
  everywhere. Parity metric retired. **The run is done at P6, not before —
  stopping earlier leaves four derivations instead of one.**

## 7. Tests & metrics (the guards get negative controls — §28)

- `tests/test_hitl_gates.py` — state machine, CAS races, idempotency,
  TTL, reconciler; **negative controls**: assert an illegal transition
  *fails*, assert the parity check *fires* on a seeded divergence.
- E2E incident replay (Playwright smoke tier): the full user story above.
- Metrics: `kazma_hitl_gates_total{state,mechanism}`, `kazma_hitl_gates_pending`
  gauge, `kazma_hitl_gate_parity_mismatch_total` (P1–P5),
  `kazma_hitl_gate_reconciled_total{action}`. Firing-ledger signatures
  (§27C) copied from the emitting lines, not guessed.
- Docs: `docs/docs/ops/diagnosis-map.md` + §7 mission guidance updated in
  the SAME PR as each phase.

## 8. Explicitly out of scope

- Changing *which* tools are gated (CANONICAL list, tiers, §26B floor).
- Grant semantics (`allow this tool` scope/TTL) — unchanged, still
  thread-scoped server-side.
- Multi-operator arbitration policy beyond first-CAS-wins (409 for the rest).
