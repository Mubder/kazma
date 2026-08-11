# Kazma Intelligent Agent Plan  
## Commitment Layer: Intent → Resolve → Act (Not Chat-Then-Hope)

**Status:** Approved for Phase 0–1; Phase 2 gated on Phase 0 empirical exits (Revision 2 + third-pass audit sweep: measurement-validity + stale-language)  
**Date:** 2026-08-11  
**Revision 1:** 2026-08-11 — first peer review (`approve-with-changes`): TTL/GC, HITL bus, phase reorder, settled §14  
**Revision 2:** 2026-08-11 — second peer review + Grok concurrence: measure-before-promise, contingencies, corpus/latency design, registry vs UX split, security specs  
**Author intent:** Make Kazma a **durable autonomous agent** that changes the world only after it knows *what* the user wants and *whether* that conflicts with known truth — not a chat UI that sometimes fires tools.

**Trigger incident (canonical failure story):**  
User: “remind me before the CoPilot monthly reset in 2 days…”  
Memory already held: Copilot Pro+ next reset **2026-09-01**.  
Agent: treated “in 2 days” as event date (Aug 13), scheduled wrong job, then on clarification **overwrote** Sep 1 memory with the invented date.  
Root cause was **not** cron or Telegram delivery. It was missing **semantic commitment** before side effects.

---

## R1. Review incorporation (binding decisions)

Second-agent verdict: **approve-with-changes**. Effort realism: **ok** (Phase 2 may bleed to ~4 weeks).  
Simpler alternative considered (Temporal.io durable execution): **rejected for now** — keep modular/self-contained on existing SQLite + LangGraph; revisit only if multi-replica commitment durability becomes a production requirement.

### R1.1 MUST-CHANGE (implemented in this revision)

| Review ask | Decision |
|------------|----------|
| GC policy for aborted/stale drafts | **Mandatory** — see §3.9 Commitment store + GC/TTL |
| Pending commitment TTL | **Mandatory** — default **24h** for `draft`/`needs_clarify`/`needs_confirm`; see §3.9 |
| Sync blocking / clarify loops | Gate is **non-blocking for waiting**: clarify/confirm uses existing LangGraph `interrupt()` (suspend graph, free worker); policy eval is pure sync heuristics (latency **target** &lt;20ms p95 — measure G1). Never spin-wait in `tool_worker_node`. |
| Phase reorder | **Phase 5 (IDE/swarm/MCP) runs concurrent with Phase 3** — see §9 |
| Phase 2 effort | Budget **2–4 weeks** (not 2–3) |

### R1.2 Settled open questions (was §14)

| # | Question | Settled answer |
|---|----------|----------------|
| 1 | Gate placement | **MVP:** `tool_worker_node`. **Industrial target:** extract dedicated `resolve_node` (SRP, easier swarm tests). Tracked as Phase 2.5 / post-MVP. |
| 2 | Confirm UX | **Single unified HITL interrupt bus** for security *and* semantic. No separate semantic channel. Typed payloads only. |
| 3 | High-conf allow skip UX | **Yes** for zero-conflict high-conf acts (e.g. clean `remind`). Still **audit-log** every allow. Friction only for conflict/destructive/critical. |
| 4 | Predicate criticality | **YAML/ConfigStore only** — never hardcode functional predicates in Python. |
| 5 | Extra LLM for slots | **Default MVP: heuristics + tool args.** If G2 fails → **structured args** first (§R2.2); classifier/LLM only after that. |
| 6 | Commitment store | **New table in existing ops SQLite** (`memory_ops.db` / ops plane) — not a new DB file, not checkpointer-only. **Single-replica** residual §R2.5. |
| 7 | Multi-act turns | **Ordered plan with intermediate commits.** Step A can commit; step B queues next draft. Do not force one act per user turn. |
| 8 | Swarm workers | **Inherit parent commitment scope token** through handoff cycle; never widen; mutator without token → deny (§R2.5). |
| 9 | False clarify budget | **Measured in G2**; provisional hope ~15% only after ROC at zero false-allow. Do not hard-promise pre-data. |
| 10 | Under-weighted | **Arabic-first** relative time is a **Phase 0 corpus** (§R2.3), refined in Phase 4 resolvers. |

### R1.3 Nice-to-have (explicitly deferred)

- Local specialized classifier on RTX 4090 for slot resolution / conflict detection — only if Phase 0 heuristics fail contingency (§R2.2).

---

## R2. Second peer review + measurement gates (binding)

**Verdict (both peer agents + plan author):**  
- **Approve Phase 0–1 unconditionally.**  
- **Do not merge Phase 2** until Phase 0 exit criteria (§R2.1) pass or contingency (§R2.2) is re-baselined in writing.  
- Architecture thesis stands; risk is **unmeasured promises** and incomplete choke points.

### R2.1 Three Phase 0 exit criteria (Phase 2 merge gates)

| # | Gate | Pass condition |
|---|------|----------------|
| **G1 Latency** | Spike conflict-detection path against **candidate implementation** (§R2.4) on a **production-scale** belief store: declared cardinality (e.g. ≥10k beliefs, full `functional_predicates` list from YAML, indexed predicate column), **or** a latency-vs-scale curve projected to expected prod cardinality. "Populated" = *calibrated*, not "more than 3 rows" | Report p50/p95/p99. Target **&lt;20ms p95** remains a *target*, not a promise, until measured. If missed, document cost and choose contingency (structured args first — usually *faster*, not slower). |
| **G2 Corpus + heuristics** | EN+AR relative-time corpus per §R2.3; run heuristic resolver; report false-allow / false-clarify | **False-allow on conflict goldens = 0.** False-clarify rate *measured* (no a-priori &lt;15% promise until data exists). |
| **G3 Honesty / choke** | §13 claim rewritten (done in R2) **and** Phase 1 plan includes `LocalToolRegistry.execute` → `authorize_effect` for memory/schedule-class effects | Registry wiring is Phase 1 security invariant, independent of combined-card UX |

> **Gate types:** G1/G2 are *empirical* gates (numbers must land). G3 is a *design* gate (checklist — done or not). Phase 2 merge requires all three.

### R2.2 Contingency if Phase 0 numbers are bad (no mid-Phase-2 design pivot)

**Trigger (either):**

- Heuristic **false-allow &gt; 0** on conflict goldens, **or**  
- At the operating point that achieves zero false-allow, **false-clarify is operationally unacceptable** (product judgment after seeing the ROC; provisional red line if &gt; ~25% on the balanced corpus, to be confirmed post-G2).

**Preferred fallback (ordered):**

1. **Structured tool args (primary)** — force the model to emit typed slots (`event_at` ISO, `lead`, `belief_id` / memory ref, `fire_at`) instead of free-text the gate must parse. Aligns with §2.1 structured control plane. **Cheapest, default expansion of MVP scope.**  
2. Local classifier (RTX 4090) — only if structured args still leave residual parse on natural language.  
3. Cheap gate-LLM call — last resort; breaks latency story; requires explicit budget renego.

**Written re-baseline rule:**  
> If G2 fails, MVP scope expands to structured args for `schedule_task` / `memory_store` (and peers), and Phase 2 re-baselines in a short design note *before* mutator-gate code merges. No inventing architecture mid-Phase-2.

### R2.3 Relative-time corpus shape (Phase 0 deliverable, not Phase 4)

| Dimension | Requirement |
|-----------|-------------|
| **Size** | **≥ 500** cases (100 is a floor for smoke only; **500–1000** for rate honesty) |
| **Language** | **EN/AR parity** — not 90/10. Target ~50/50, min **40% AR** including mixed RTL/LTR |
| **Labels** | Each case: expected slots, conflict? (Y/N), gold decision (allow / clarify / deny), `request_at` |
| **Distribution** | Known mix: conflict / no-conflict / multi-goal / ambiguous-parse / absolute-date / relative-to-event / relative-from-now |
| **Split** | **Goldens** (CI, zero false-allow) vs **adversarial/hostile** set (separate; stress, not vanity metrics) |
| **Test-only goldens** | Goldens are **held-out** — heuristics are tuned on the hostile/broad set (or a declared train partition), never on goldens. G2 false-allow is reported on the *held-out* golden set; else 0 false-allow is reachable by overfit and proves nothing |
| **CoPilot class** | Explicit subset: known belief date + “in 2 days” / `بعد يومين` / “before the subscription ends” |

Corpus lives under e.g. `tests/fixtures/commitment/relative_time_corpus.jsonl` + loader. **Phase 0 exit = corpus merged + heuristic report committed** (even if accuracy forces §R2.2).

### R2.4 Conflict-detection design is a Phase 0 artifact

Latency spike must run against a **candidate** `detect_conflicts` + relative-time fill implementation (same algorithms Phase 2 will call), including:

- How many belief lookups (by predicate list from YAML; index assumptions)  
- Predicate match (exact / glob `preferred_*`)  
- Relative-time parse (EN+AR) + anchor to memory vs `request_at`  
- Decision table stub (allow vs clarify)

Placeholder “SELECT 1” spikes are **invalid** for G1.

### R2.5 Spec patches (must be in design before Phase 2 code)

| Topic | Binding rule |
|-------|----------------|
| **Combined card** | Security axis ⊥ semantic axis. One UI card may show both; **semantic approve never satisfies security tier**; security may be denied independently; each axis independently revocable; expiry fails both closed. Test shell/delete with memory conflict. |
| **`request_at` / `fire_at`** | Relative phrases anchor to **request time**, not approval time. Commitment stores `request_at`; on resume, recompute `fire_at` from `request_at` + lead (deterministic). |
| **Swarm scope token** | Propagates through `_handle_handoff` → `_dispatch_worker_by_name_all` → `_dispatch_worker` with visit cycle. Token **never widens** on handoff; may narrow. Re-dispatch copies parent scope. Missing token on mutator → deny. Test §8.3 #15. |
| **Unregistered tools** | Fail-closed: unknown tool with mutator-like effect → **high semantic_tier or deny** (config default: deny mutators, allow pure reads). Test omission bypass. |
| **Multi-replica** | Commitments on SQLite ops = **single-replica consistency**. Residual risk (like document metadata honesty). Multi-replica HA out of scope until ops store is shared (Postgres port or external). |
| **Critical retention** | `remind`/ephemeral: `retention_days` 30 OK. **Critical acts** (`revise_fact`, `config_change`, soul deltas, identity): **archive default ≥ 1 year** (monthly archive table), not hard-delete at 30d. |
| **Soul + commitment** | Compose with `KAZMA_SELF_IMPROVEMENT=0` kill-switch and `_agent_evo_lock`. `needs_confirm` deltas **must not** auto-apply on lock timeout. **Hook (design now, Phase 7):** `_auto_apply` must skip any delta whose commitment is not `confirmed` — enforce at the apply site, not by lock timing. Avoids the “scheduler existed but nothing called it” pattern (AGENTS.md §15B). |
| **Snapshots** | `active_commitment_id` / status in SupervisorState ride checkpointer + snapshots; capture in `_supervisor`; path-rewrite N/A for non-path fields — add test row. |

### R2.6 Phase 1 split clocks (security ≠ UX)

Phase 1 ships **two independent tracks**:

| Track | What | Clock |
|-------|------|--------|
| **(a) Choke-point wiring** | `LocalToolRegistry.execute` → `authorize_effect` for memory- and schedule-class effects; **audit-only allow/deny** on conflict (no card required) | **Must land in Phase 1** |
| **(b) Combined-card UX** | Unified interrupt `kind=combined` + Web/gateway buttons | **May lag to Phase 3** |

**Principle:** The gate is safe without the card. Audit-only deny on memory conflict is correct with zero UI. Phase 1 exit criteria **must not** couple to Phase 3 UI.

### R2.7 Budgets become targets until measured

| Phrase in older revisions | R2 status |
|---------------------------|-----------|
| “&lt;20ms p95” | **Target**; validated by G1 |
| “false clarify &lt;15%” | **Provisional product hope**; set operating point only after G2 ROC |
| “no extra LLM in MVP” | **Default intent**; **overridden by §R2.2** if G2 fails (structured args first, not necessarily an LLM) |

---

## 0. Executive summary

### Thesis
Industry-grade agents are not “better prompts.” They are systems with:

1. **Explicit world-model state** (goals, slots, beliefs, open questions)  
2. **Policy before effect** (allow / clarify / confirm / deny)  
3. **Memory as constraint**, not wallpaper  
4. **Auditable commitments** (what was decided, why, what mutated)  
5. **Graceful recovery** when the model is wrong  

Kazma already has strong bones: LangGraph supervisor, multi-platform isolation, HITL for danger tools, V2 memory, cron, swarm, IDE, checkpoints.  
**The hole in the middle:** message → ReAct tools, with “intent” meaning *RAG focus* and HITL meaning *tool-name risk* — neither means *resolved user goal*.

### One-line product definition
**Kazma may only mutate durable state (schedule, memory, filesystem, outbound, config, soul) after a Commitment object is in `ready` or `confirmed` — never from raw utterance freestyle.**

### Non-goals (important)
- Not a second chat brain or parallel graph that bypasses the supervisor.  
- Not “confirm everything” (that makes a useless agent).  
- Not date-only heuristics.  
- Not replacing HITL security gates — **extending** the control plane with semantic gates.  
- Not requiring a larger model to “be smarter” as the primary fix.

### Success metric (north star)
On ambiguous or memory-conflicting turns:  
**zero silent wrong durable writes** (cron jobs, belief supersedes, file/config mutations) without either (a) user clarification, or (b) an explicit high-confidence commitment logged in audit.

---

## 1. Problem statement

### 1.1 What users think an agent is
An agent that:

- Understands the request  
- Uses past knowledge correctly  
- Plans when uncertain  
- Acts when clear  
- Remembers faithfully  
- Asks when stuck  
- Does not invent facts to finish the turn  

### 1.2 What Kazma does today (accurate)

| Layer | Exists? | Role |
|-------|---------|------|
| ReAct supervisor loop | Yes | `supervisor → tool_worker → respond` |
| `classify_turn_intent()` | Yes | Focus/RAG: continue/store/shift/… — **not goal resolution** |
| HITL danger tools | Yes | Tool-name security approve/deny |
| Soft `` ```plan `` `` nudge | Yes | Prompt-only; ignorable |
| Memory per-turn inject | Yes | Context, not hard constraints |
| Memory write + auto-extract | Yes | Free-fire / silent supersede possible |
| Cron `schedule_task` | Yes | Immediate on invoke; schema is free-form strings |
| Swarm / IDE / documents | Yes | Separate paths; same “act first” pattern risk |

### 1.3 Failure class (generalized)

```
Ambiguous utterance
  + Retrieved memory (correct)
  + Model prefers action over disambiguation
  + Side-effect tools free-fire or HITL only checks tool name
  + Memory mutation free-fire
  = Durable wrong world state + confident reply
```

This is the same class as:

- Scheduling wrong dates/times  
- Overwriting preferences (“I use X now”) with a misparse  
- Cancelling the wrong job  
- Storing a hallucinated “fact” from tool noise  
- Spawning work on the wrong workspace  
- Self-improvement soul deltas from bad turns  

### 1.4 Industry names for this gap
- Missing **dialog state tracking / slot filling** before tools  
- Missing **tool-use policy / authorization layer** beyond RBAC  
- Missing **belief revision protocol** (AGM-style / epistemic hygiene)  
- Missing **plan–act separation** (ReAct without commitment)  
- **Specification gaming** by the model (satisfy “be helpful” over “be correct”)

---

## 2. Industry patterns to adopt (and what to reject)

### 2.1 Adopt (proven patterns)

| Pattern | Industry examples / analogues | Kazma mapping |
|---------|------------------------------|---------------|
| **Plan then execute** | OpenAI Assistants “required actions”, many enterprise agents; classical BDI | Commitment object before mutators |
| **Slot filling + confirm** | Classic task-oriented dialog (Rasa, Dialogflow, Alexa skills) | Required slots per act type |
| **Tool gateway / policy engine** | MCP servers with auth, cloud IAM, OPA-style policies | Pre-tool interceptor in `tool_worker_node` |
| **Human-in-the-loop tiers** | LangGraph interrupt, enterprise approval workflows | Extend HITL: security *and* semantic confirm |
| **Memory as source of truth with conflict rules** | Knowledge bases with versioning; CRM merge rules | Gate around `mutate_belief` |
| **Two-phase commit for side effects** | Distributed systems | propose → (optional confirm) → commit → audit |
| **Structured outputs for control plane** | JSON schema tool args, constrained decoding | Commitment schema, not free prose only |
| **Epistemic status tags** | Research agents; citation systems | fact vs inference vs user-asserted |
| **Circuit breakers on wrongness** | Reliability engineering | Conflict rate, supersede rate, clarify rate metrics |

### 2.2 Reject (anti-patterns)

| Anti-pattern | Why |
|--------------|-----|
| “Just improve the system prompt” | Incident already showed model *saw* Sep 1 and ignored it |
| Confirm every tool | Agent becomes a button-clicker; users turn YOLO on forever |
| Parallel “smart router” that bypasses tools | Dual brains = dual bugs; keep one supervisor |
| Date-specific regex only | Fixes one symptom; next failure is workspace/identity/money |
| Bigger model as the plan | Reduces error rate, does not create unbreakable invariants |
| Auto-store everything the model said | Pollutes SoT; amplifies hallucinations |

### 2.3 Design principles (non-negotiable)

1. **Invariants beat vibes.** If a check can be code, it is code.  
2. **One choke point for mutations.** Graph tools, swarm tools, post-turn extract, IDE mutators — same policy spine.  
3. **Clarification is a first-class outcome**, not a failure.  
4. **Asymmetry:** wrong durable write ≫ delayed reply. Prefer ask when EV(error) high.  
5. **User-asserted absolute facts beat model arithmetic** unless user explicitly revises.  
6. **Retrieved high-confidence beliefs are soft locks** until superseded by explicit user contradiction.  
7. **Auditability:** every durable write has `commitment_id` + reason code.  
8. **Channel parity:** Web SSE, Telegram, Discord, Slack, TUI same commitment path.  
9. **Backward compatible default:** low-risk reads and pure Q&A stay snappy.  
10. **Kill-switches:** config to loosen/tighten policy without redeploy.

---

## 3. Target architecture

### 3.1 Mental model: three planes

```
┌─────────────────────────────────────────────────────────────┐
│  COGNITIVE PLANE (LLM)                                      │
│  Understand, draft plan, propose slots, draft messages      │
└───────────────────────────┬─────────────────────────────────┘
                            │ proposes Commitment / tool args
┌───────────────────────────▼─────────────────────────────────┐
│  COMMITMENT PLANE (new — hard control)                      │
│  Resolve slots · conflict check · policy · clarify/confirm  │
└───────────────────────────┬─────────────────────────────────┘
                            │ only if ready/confirmed
┌───────────────────────────▼─────────────────────────────────┐
│  EFFECT PLANE (existing tools / memory / cron / IDE)        │
│  schedule_task · mutate_belief · file_write · shell · …     │
└─────────────────────────────────────────────────────────────┘
```

Today cognitive and effect are almost fused. **Insert commitment plane.**

### 3.2 Target graph (evolution, not rewrite)

**MVP:**

```
START
  → supervisor_node
  → tool_worker_node
        ├─ authorize_effect / policy_gate (NEW)  # allow | rewrite | clarify | confirm | deny
        ├─ unified HITL interrupt (security + semantic kinds)
        └─ execute authorized tools only
  → supervisor_node | respond_node
```

**Industrial (Phase 2.5+):**

```
START
  → supervisor_node
  → resolve_node             # policy only (SRP); no tool I/O
  → tool_worker_node         # execute pre-authorized calls only
  → supervisor_node | respond_node
```

**Critical:** Gate lives in `tool_worker_node` first (minimize topology risk). Extract `resolve_node` once Phase 2 is stable — required for industrial SRP and swarm unit-testing, not for MVP correctness.

### 3.3 Core object: `Commitment`

Minimal schema (implementation language: Python TypedDict / Pydantic):

```text
Commitment
  id: uuid
  thread_id: str
  turn_id: str
  act: enum
    # remind | store_fact | revise_fact | cancel_job | send_outbound
    # mutate_fs | exec | config_change | delegate | research | answer_only
  goal_text: str                 # user-facing one-liner
  slots: dict                    # act-specific (see §4)
  evidence:
    user_spans: list[str]        # what user said that filled slots
    memory_refs: list[belief_id]
    tool_refs: list[tool_result_id]
  confidence: float              # 0..1 model or heuristic
  conflicts: list[Conflict]
  status: draft | needs_clarify | needs_confirm | ready | committed | aborted
  policy_decision: allow | clarify | confirm | deny | rewrite
  created_at, updated_at
```

`Conflict`:

```text
Conflict
  type: memory_contradiction | ambiguous_parse | missing_slot | multi_goal
        | low_confidence | source_untrusted | unit_mismatch
  severity: low | medium | high | critical
  existing: {...}    # e.g. belief {id, object: "2026-09-01"}
  proposed: {...}    # e.g. {object: "2026-08-13"}
  resolution_hint: str
```

### 3.4 Policy decision table (core of “unbreakable”)

| Condition | Decision | User experience |
|-----------|----------|-----------------|
| Read-only tools only | **allow** | Instant |
| Mutator + all required slots filled + no conflict + conf ≥ T_high | **allow** (log commitment; silent UX) | Instant act — **no confirm card** |
| Mutator + missing required slot | **clarify** | One targeted question (unified interrupt bus) |
| Mutator + memory contradiction (high-confidence belief) | **clarify** or **confirm** with both options shown | “Memory says Sep 1; you said 2 days — which?” |
| Mutator + low confidence multi-parse | **clarify** | Show 2 interpretations |
| Security danger tool | **same HITL interrupt bus** after semantic ready (combined card when both apply) | One approval surface |
| User explicit YOLO / task grant | allow per existing grants, still **log** commitment | Power users |
| Critical acts (config list) / identity-money-delete | always **confirm** unless user restated absolute value this turn | High friction only where needed |

**Thresholds and critical acts/predicates are config** (`agent.commitment.*`, `memory.functional_predicates` in YAML/ConfigStore) — **never hardcode** functional belief lists in Python.

### 3.5 Act catalog (cross-domain, not date-only)

| Act | Required slots (examples) | Conflict sources |
|-----|---------------------------|------------------|
| `remind` | `event_ref` OR `event_at`, `fire_at` OR `lead`, `prompt`, `delivery` | Beliefs about event dates; existing cron jobs |
| `store_fact` | `subject`, `predicate`, `object`, `epistemic` (asserted/inferred) | Active belief same (s,p) |
| `revise_fact` | `belief_id` or (s,p), `new_object`, `user_explicit` | Must be explicit revision language |
| `cancel_job` | `job_id` or unique match criteria | Multiple matches → clarify |
| `mutate_fs` | `path`, `op`, `workspace_root` | Workspace binding |
| `exec` | `command` summary, `cwd` | Allowlist + HITL |
| `send_outbound` | `channel`, `target`, `body_hash` | Wrong target risk |
| `delegate` | `goal`, `workers` or template | Scope creep |
| `answer_only` | none | N/A |

Tools map to acts via a **side-effect registry** (single SoT):

```text
schedule_task        → remind
cancel_scheduled     → cancel_job
memory_store         → store_fact | revise_fact
memory_invalidate…   → revise_fact (destructive)
file_write/delete    → mutate_fs
shell_exec/code_exec → exec
email_send           → send_outbound
spawn_*/dispatch_*   → delegate
```

### 3.6 Memory becomes a constraint system

#### Write path (single gateway)
All of these call one function, e.g. `commit_belief_change(proposal, commitment)`:

- `memory_store` tool  
- memory admin tools  
- `belief_extractor` / micro_consolidation  
- (optional later) memory API from UI with same conflict rules when `source=agent`

Rules:

1. **Additive / low-stakes** (`noted`, ephemeral) → allow more freely.  
2. **Functional supersede** (current fact: dates, subscriptions, identity, defaults) →  
   - If active belief differs and user did **not** explicitly assert new absolute value → **block + clarify**.  
   - If user asserted absolute value this turn → supersede + audit.  
3. **Inferred from model arithmetic / tool hallucination** → never functional supersede; store as `inferred` with low conf or drop.  
4. Relative phrases (“in 2 days”, “next week”) **never** overwrite absolute calendar beliefs by themselves; they fill `lead` or `fire_at` relative to anchored event.

#### Read path
Injected memory block gains machine-readable appendix for the policy gate (not only markdown for the LLM):

```text
CONSTRAINT_CANDIDATES:
- belief_id=… predicate=copilot_next_reset object=2026-09-01 conf=0.91
```

Supervisor still sees human markdown; **gate sees structured constraints**.

### 3.7 Reminder/cron as reference implementation of acts

`schedule_task(timing, prompt)` stays for compatibility; **if G2 fails**, schema expands to structured slots (§R2.2). Preferred path:

1. Build Commitment `act=remind`; stamp **`request_at`** (user message time, UTC)  
2. Resolve:  
   - `event_at` from memory if user referenced known event  
   - `lead` from “in 2 days / 30 min before” / `بعد يومين`  
   - **Relative-from-now:** `fire_at = request_at + lead` (not approval time)  
   - **Relative-before-event:** `fire_at = event_at - lead`  
3. If ambiguous (relative with no anchor vs absolute) → clarify  
4. On resume after interrupt: **recompute** `fire_at` from stored `request_at` + resolved slots (deterministic; no 20‑minute drift from delayed approve)  
5. Only then call scheduler with ISO `fire_at`; store job_id on commitment audit  

This generalizes: **relative language binds to anchors or `request_at`; it does not invent anchors; approval latency does not shift fire time.**

### 3.8 Relationship to existing systems (do not break)

| Existing | Keep | Change |
|----------|------|--------|
| Platform isolation (§2 AGENTS) | Yes | Commitment never stores chat_id in graph; delivery_target still from ContextVar at schedule |
| HITL danger tools | Yes | Semantic gate **before** security HITL |
| `classify_turn_intent` | Yes | Remains RAG/focus; may feed act hints only |
| YOLO / tool grants | Yes | Bypass semantic *confirm* optionally; never bypass audit log |
| Swarm bus HITL | Yes | Share side-effect registry |
| turn_failed / transient LLM errors | Yes | Untouched |
| Prompt fence / self-improvement fence | Yes | Soul apply later stages through commitment |
| Checkpointer / time-travel | Yes | Commitments in ops DB for audit; optional link from snapshots |
| Swarm workers | Yes | Inherit parent commitment **authorization scope** — no privilege escalation |

### 3.9 Commitment store, TTL, and garbage collection (MUST)

**Store location:** new table(s) on the **existing ops SQLite plane** (`memory_ops.db` via `memory_ops_db()` / same ops connection family) — **not** a new `commitments.db`, **not** checkpointer-only. Enables relational audit by `commitment_id`, `thread_id`, `turn_id`, `status`, `expires_at`.

Suggested tables:

```text
commitments (
  commitment_id TEXT PK,
  thread_id TEXT NOT NULL,
  turn_id TEXT,
  parent_commitment_id TEXT NULL,   -- ordered multi-act / swarm child
  act TEXT NOT NULL,
  status TEXT NOT NULL,             -- draft|needs_clarify|needs_confirm|ready|committed|aborted|expired
  goal_text TEXT,
  slots_json TEXT,
  evidence_json TEXT,
  conflicts_json TEXT,
  policy_decision TEXT,
  confidence REAL,
  tool_name TEXT,
  args_digest TEXT,
  result_json TEXT,
  request_at REAL NOT NULL,          -- user message time; relative fire_at anchor
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  expires_at REAL NULL,             -- set for pending states
  resolved_at REAL NULL             -- approve/clarify resolution time (NOT fire_at anchor)
)

commitment_events (                 -- append-only audit
  id INTEGER PK,
  commitment_id TEXT NOT NULL,
  event_type TEXT NOT NULL,         -- created|clarified|confirmed|allowed|denied|committed|expired|gc
  payload_json TEXT,
  created_at REAL NOT NULL
)
```

**TTL defaults (config under `agent.commitment.ttl_*`):**

| Status | Default TTL | On expiry |
|--------|-------------|-----------|
| `draft` | 1 hour | → `expired`, no execute |
| `needs_clarify` | **24 hours** | → `expired`; graph interrupt superseded/denied on next touch |
| `needs_confirm` | **24 hours** | same as clarify |
| `ready` | 15 minutes (or until tool batch finishes) | → `expired` if orphaned mid-flight |
| `committed` / `aborted` / `expired` | retained for audit | GC per retention policy below |

**Garbage collection policy (binding):**

1. **Expiry sweeper** (asyncio or existing memory worker cadence, e.g. every 15–60 min):  
   `UPDATE commitments SET status='expired', updated_at=now WHERE status IN ('draft','needs_clarify','needs_confirm','ready') AND expires_at < now`.  
   Emit `commitment_events(event_type='expired')`.  
2. **Interrupt coupling:** when a commitment expires, any open LangGraph interrupt for that `commitment_id` must fail closed (deny/abort) — no late approve after TTL.  
3. **Retention (tiered):**  
   - **Ephemeral acts** (`remind`, pure schedule, answer_only): hard-delete after **`retention_days` default 30**.  
   - **Critical acts** (`revise_fact`, `config_change`, soul deltas, identity-class): **archive by default ≥ 365 days** (monthly archive table); do **not** hard-delete at 30d — self-improvement corruption may surface late.  
4. **Hard GC:** after tier retention, delete live rows (archives remain for critical).  
5. **Thread supersede:** new user turn that supersedes pending HITL (existing `hitl_supersede`) also marks related pending commitments `aborted` with reason `superseded_by_new_turn`.  
6. **Bloat guardrails:** cap pending commitments per `thread_id` (default **20**); oldest pending → `aborted` with reason `pending_cap`.  
7. **Metrics:** `commitment.expired_count`, `commitment.gc_deleted_count`, `commitment.pending_gauge`.  
8. **Tests:** unit tests for TTL expiry, supersede abort, retention hard-delete, approve-after-expiry denied.

Without this section implemented, **do not ship** the commitment table to production.

### 3.10 Multi-act ordered plans

- A single user turn may produce **ordered commitments** `C1 → C2 → …` (or parent + children).  
- **Independent batch rule (refined):**  
  - **Conflict coupling:** if two mutators share the same ambiguous slots / same conflict, hold **both** until clarify.  
  - **Sequential allow:** if C1 is ready and C2 depends on C1 result, commit C1, then draft C2 with `parent_commitment_id=C1`.  
  - Do **not** force “only one act per user message” (hurts swarm/IDE multi-step UX).

**Partial-effect policy (binding — amend §3.10):** if C1 commits and a dependent child C2 (`parent_commitment_id=C1`) later clarifies, expires, or is denied, the plan must define what happens to C1's effect. Per act, pick one:

- **Reversible acts** (`remind`, `store_fact`, `cancel_job`) → **compensating action**: define rollback keyed on stored `result_json` (cancel `job_id`, restore prior belief object).  
- **Irreversible acts** (`send_outbound`, `exec`, `mutate_fs` destructive) → **validate-before-commit**: dependent children must be *structurally resolved* (no open conflict) before the parent commits. True two-phase; never fire an irreversible parent ahead of an unresolved child.

Never silently leave inconsistent world state from an ordered plan. Test: C1 commits → C2 denied → C1 rolled back (reversible) or C1 held (irreversible).  

### 3.11 Swarm scope-token propagation (mechanism)

Scope token fields (min): `parent_commitment_id`, `allowed_acts`, `max_semantic_tier`, `workspace_id`, `thread_id`, `narrowed_from` (optional).

**Propagation rule (through existing cycle plumbing):**

1. Orchestrator creates root commitment / scope when dispatching.  
2. `_dispatch_worker` / `_dispatch_worker_by_name_all` **copy** scope onto child task metadata (same path as `workspace_id` today).  
3. `_handle_handoff` A→B: B receives **same or narrower** scope (intersection of allowed acts / tier). **Never widen.**  
4. Revisit A→B→A (`_MAX_VISITS`): token remains; visit counter independent of scope.  
5. Worker mutator without valid scope token → **deny** (fail-closed).  
6. Worker cannot mint a new root scope; only orchestrator / parent commitment can.  

**Test:** worker attempts `memory_store` / `schedule_task` outside inherited acts → denied; handoff A→B→C preserves/narrows; no escalation on re-dispatch.

---

## 4. Detailed design: policy gate

### 4.1 Placement

| Stage | Placement | Notes |
|-------|-----------|--------|
| **MVP (Phase 2)** | Inside `tool_worker_node` | Minimize topology risk; shared helper `authorize_effect()` |
| **Industrial (Phase 2.5+)** | Dedicated **`resolve_node`** before tool execution | SRP: policy evaluation ≠ tool execution; easier swarm edge-case tests |

Immediately after parsing pending tool calls, **before** parallel execution and before security `interrupt()`.

**Non-blocking rule (review risk closed):**  
`policy_gate` must **never** await user input inline. Decisions `clarify`/`confirm` → set commitment pending + **LangGraph `interrupt()`** (same path as security HITL) → worker returns; process is free. Resume path re-enters gate with slot patches. No synchronous clarify loops inside the node.

Secondary choke points (same library — Phase 3 concurrent / Phase 5 workstream):

| Path | Why |
|------|-----|
| `LocalToolRegistry.execute` | IDE / swarm paths that skip graph worker — **priority security invariant** |
| `mutate_belief` wrapper | Auto-extract and any direct writers |
| `schedule_post_turn_memory` | Throttle functional supersedes |

### 4.2 Algorithm (pseudocode)

```
function policy_gate(state, tool_calls, constraints):
  outcomes = []
  ordered = order_tool_calls_for_plan(tool_calls)  # multi-act sequence
  for tc in ordered:
    act = map_tool_to_act(tc)
    if act is read_only:
      outcomes.append(ALLOW(tc)); continue

    draft = build_commitment_from_tool(tc, state, constraints)
    draft = fill_slots_from_memory(draft, constraints)   # heuristics only (MVP)
    draft = detect_conflicts(draft, constraints)
    draft.expires_at = now + ttl_for(draft.status)

    decision = decide(draft)  # table §3.4

    if decision == ALLOW:
      stamp tc with commitment_id; persist audit allow
      outcomes.append(ALLOW(tc))
    elif decision == REWRITE:
      outcomes.append(ALLOW(rewrite_args(tc, draft)))
    elif decision == CLARIFY:
      outcomes.append(HOLD_CLARIFY(draft))  # interrupt — do not block thread
    elif decision == CONFIRM:
      outcomes.append(SEMANTIC_HITL(draft))  # SAME interrupt bus as security
    elif decision == DENY:
      outcomes.append(DENY(draft, reason))

  if any HOLD_CLARIFY or CONFIRM on coupled set:
    hold coupled mutators; allow already-committed prior steps in ordered plan
    interrupt once (combined card)
  ...
```

**Coupling rule:** Shared-conflict mutators in the same hop do not partially apply.  
**Ordered-plan rule:** Independent prior steps that already `ALLOW`ed may commit before a later step clarifies (parent/child linkage recorded).

### 4.3 Clarify / confirm UX (unified bus only)

**One approval bus.** Extend existing HITL interrupt + `/api/approve/{thread_id}` (and gateway callbacks). Typed payload:

```text
type: hitl_approval          # umbrella — always this channel
kind: security | semantic_clarify | semantic_confirm | combined
commitment_id
axes:                        # independent; both must pass for combined execute
  security: pending|approved|denied|n/a
  semantic: pending|approved|denied|n/a
question
options: [{id, label, slots_patch}]
tool / args summary (when security)
free_text: true
expires_at
request_at                   # for display / recompute fire_at
```

**Combined-card semantics (security-relevant):**

- `kind=combined` is **one card, two axes**.  
- **Semantic approval never satisfies security tier.**  
- Security may be bundled in the same UX, but denial/expiry on **either** axis blocks execute.  
- Axes are independently revocable in API (`approve_security`, `approve_semantic`, or single “approve all pending” that still records both).  
- Test: `shell_exec` or `file_delete` + memory conflict — semantic “use memory date” must not auto-clear security.

Other UX rules:

- Resume: patch slots → recompute from `request_at` → re-run `authorize_effect` → execute if unexpired **and** required axes approved  
- **Approve after `expires_at`:** hard deny  
- High-conf zero-conflict **allow:** no card; audit only  
- Phase 1 may deny via **audit-only** without any card (§R2.6)

### 4.4 What the LLM still does
- Propose acts and provisional slots  
- Write user-facing clarify questions when template insufficient  
- Choose among tools **after** commitment ready  
- Narrate results  

What the LLM **must not** solely decide:

- Whether a contradicting belief may be overwritten  
- Whether relative time invents a new absolute event  
- Whether a mutator batch is consistent  

---

## 5. Side-effect registry (unify policy data plane)

### 5.1 Problem today
- `CANONICAL_DANGER_TOOLS` vs YAML `require_approval_for` can drift  
- `memory_store` is free write  
- Swarm safety list ≠ chat effective list  
- MCP classified by name heuristics  

### 5.2 Proposal
New module e.g. `kazma_core/safety/side_effects.py` (name flexible):

```text
ToolEffectProfile
  name: str
  effect: none | read | write_memory | write_fs | exec | schedule
          | outbound | config | delegate | identity
  security_tier: safe | write | danger | unsafe
  semantic_tier: none | low | high | critical
  act: CommitmentAct | null
  required_slots: list[str]
```

**Runtime rule:**  
`requires_security_approval = security_tier==danger AND hitl enabled`  
`requires_semantic_check = semantic_tier >= low AND commitment.enabled`

**Unregistered / unknown tools (fail-closed):**  
If a tool is not in the registry: treat as **unknown**.  
- Effect inferred mutator (write/exec/schedule/outbound/config patterns, MCP non-safe) → **semantic_tier=critical** or **deny** (default: **deny** mutators).  
- Pure read patterns → allow.  
Never “missing profile = free-fire.” Regression test: omit a mutator from registry → blocked.

Ship defaults; Settings UI later.  
**Parity tests:** YAML cannot silently drop security tools; semantic defaults covered by unit tests.

---

## 6. Post-turn memory & auto-store reform

### 6.1 Current risk
Post-turn extraction can invent or supersede facts the user never committed to (including wrong dates the model “corrected” into memory).

### 6.2 Rules
1. Default: post-turn may write **episodes** freely (what happened).  
2. Beliefs: only if  
   - user store-intent, OR  
   - high-precision extract + no conflict, OR  
   - explicit confirmation this session.  
3. Never functional-supersede from assistant-only claims without user assertion.  
4. If assistant invented a date in dialogue, extractor must **not** treat it as user fact.  
5. Link belief writes to `commitment_id` when present.

Config: `memory.auto_store_beliefs` modes: `off | conservative | aggressive`  
**Default: conservative.**

---

## 7. Prompting & model policy (supporting, not primary)

Add a short **system contract** (all injection sites: agent_runner, sse, gateway):

```text
COMMITMENT RULES:
1. Prefer resolve over invent for durable actions.
2. Relative times need an anchor (memory or user absolute).
3. Never overwrite a recalled fact with arithmetic.
4. If two interpretations exist, ask one precise question.
5. Answer-only questions must not call mutators.
```

Plus tool-description upgrades for `schedule_task` / `memory_store` describing slots and conflict behavior.

**Still not sufficient alone** — code gate is the product.

---

## 8. Observability & “unbreakable” verification

### 8.1 Metrics (must ship with feature)
| Metric | Meaning |
|--------|---------|
| `commitment.clarify_rate` | Share of mutator turns that clarified |
| `commitment.confirm_rate` | Semantic confirms |
| `commitment.allow_rate` | Clean allows |
| `commitment.conflict_block_rate` | Memory conflicts blocked |
| `belief.supersede_without_user_assert` | Should trend → 0 |
| `cron.jobs_cancelled_as_wrong` | User fixes (proxy) |
| `commitment.gate_latency_ms` | Target &lt;20ms p95 (**G1** §R2.1); extra LLM only per **§R2.2** contingency |
| `commitment.false_clarify_rate` | Measured in G2; operating point set post-ROC (hope ~15% only if data allows) |
| `commitment.expired_count` / `gc_deleted_count` | TTL + retention health |
| `commitment.pending_gauge` | Bloat / stuck interrupts |

### 8.2 Audit log
Ops SQLite `commitments` + `commitment_events` (§3.9):

```text
commitment_id, thread_id, turn_id, act, decision, tool, args_digest,
memory_refs, conflicts_json, user_resolution, result, expires_at
```

Silent high-conf allows **must** still write an allow event (review requirement).

### 8.3 Evaluation suite (mandatory, not optional)
New test pack e.g. `tests/test_commitment_gate.py` + scenario corpus:

| # | Scenario | Expected |
|---|----------|----------|
| 1 | Reminder “2 days before known event” + memory date | fire_at = event−2d; no overwrite; silent allow + audit |
| 2 | “Reset is in 2 days” absolute with no memory | schedule ~now+2d; may store only if user asserts |
| 3 | Clarify “subscription ends” after wrong schedule | **must not** overwrite memory with invented date |
| 4 | Explicit “actually it’s Aug 13 now” | supersede allowed + audit |
| 5 | memory_store conflicting preference | clarify/confirm on unified bus |
| 6 | Pure Q&A | no mutators, no clarify tax |
| 7 | Danger shell after clear intent | semantic allow → **same** HITL bus security card |
| 8 | Coupled batch: schedule + memory_store with conflict | **neither** commits until resolve |
| 9 | Ordered multi-act: allow A then clarify B | A committed; B pending with parent link |
| 10 | Arabic relative time (`بعد يومين`, mixed RTL) | same slot rules as EN; Phase 4 goldens |
| 11 | Multi-goal remind + independent read | read free; remind gated |
| 12 | Post-turn extract after model hallucination | no functional supersede |
| 13 | Pending clarify past TTL | status `expired`; late approve denied |
| 14 | hitl_supersede new turn | pending commitment `aborted` |
| 15 | Swarm worker without parent scope | mutator denied / cannot escalate |
| 16 | GC after retention_days | hard-delete committed/expired rows |

Golden traces: store user text, injected constraints, tool calls, gate decisions, final DB state.

### 8.4 Red-team / soak
- Hostile ambiguous corpus (like document hostile fixtures)  
- 100+ synthetic paraphrases of relative-time + known belief (**EN + AR**)  
- Measure false clarify (operating point set post-ROC §R2.7) vs false allow (corruption → 0 on **held-out** goldens §R2.3)

---

## 9. Phased execution plan (how to execute)

```text
Phase 0 ──► Phase 1 ──► Phase 2 ──┬──► Phase 3  ──┐
                                  │               ├──► Phase 4 ──► Phase 6 ──► Phase 7 ──► Phase 8
                                  └──► Phase 5  ──┘
                                       (concurrent with Phase 3)
                                  Phase 2.5 resolve_node (optional extract, after 2 stable)
```

### Phase 0 — Instrumentation, corpus, candidate conflict path, measure (1–2 weeks)
**Goal:** Freeze the incident **and** produce empirical gates G1–G3 (§R2.1). Not “logging only.”

- [ ] Structured logging around tool_worker mutators (name, arg digest, top constraints).  
- [ ] Counter: memory functional supersedes.  
- [ ] CoPilot-class failing **spec tests** (expected correct behavior).  
- [ ] Effective HITL list vs CANONICAL boot warning.  
- [ ] **EN+AR corpus** per §R2.3 (≥500, parity, goldens vs hostile) merged under `tests/fixtures/commitment/`.  
- [ ] **Candidate** conflict-detection + relative-time fill design + code path (Phase 0 artifact §R2.4).  
- [ ] **G1:** latency spike p50/p95/p99 on populated belief store against that candidate.  
- [ ] **G2:** heuristic accuracy report (false-allow / false-clarify / ROC notes).  
- [ ] If G2 fails → write §R2.2 re-baseline note (structured args) **before** Phase 2.  
- [ ] Document multi-replica residual (commitments = SQLite single-replica).

**Exit (all required):** Failing goldens exist; G1 report; G2 report; corpus merged; contingency decided if needed.

**Why first:** Without tests *and* measurement, Phase 2 invents under uncertainty.

---

### Phase 1 — Side-effect registry + memory gateway + **registry choke** (1–2 weeks)
**Goal:** Stop silent belief supersede; wire **one choke** for chat-adjacent **and** IDE/registry paths (audit-only OK).

**Track (a) — must ship (security invariant):**

- [ ] `side_effects.py` profiles + **unregistered fail-closed**.  
- [ ] `authorize_effect()` library (even if UX cards not ready).  
- [ ] Wrap `mutate_belief` conflict detection (YAML functional predicates).  
- [ ] **`LocalToolRegistry.execute` → `authorize_effect`** for memory- and schedule-class effects (audit-only allow/deny on conflict).  
- [ ] Conservative post-turn belief mode.  
- [ ] Parity tests: registry ↔ HITL danger list.  

**Track (b) — explicitly not required for Phase 1 exit:**

- Combined-card / semantic interrupt UX → Phase 3.

**Exit:** Conflicting writes cannot supersede without user-assert; registry path cannot free-fire memory/schedule mutators; unit tests green. **No dependency on Web/Telegram cards.**

**Why:** Memory corruption + IDE bypass are the same class; card UX is not.

---

### Phase 2 — Commitment object + full policy gate (2–4 weeks)
**Goal:** Mutators cannot run without commitment decision. **Blocked until G1–G3 satisfied or §R2.2 re-baseline filed.**

- [ ] `Commitment` model + ops SQLite + TTL/GC (§3.9) + tiered retention (critical ≥1y archive).  
- [ ] `request_at` / `resolved_at`; relative `fire_at` recompute on resume.  
- [ ] Wire `authorize_effect` from `tool_worker_node`; clarify/confirm via unified HITL when UX ready (or hold mutator + audit until Phase 3).  
- [ ] Coupling + ordered multi-act (§3.10); swarm scope mechanism (§3.11) at least stubbed if swarm mutators enabled.  
- [ ] Combined-card **axes** semantics if any confirm path ships in this phase.  
- [ ] Config: `agent.commitment.*`.  
- [ ] Full scenario suite §8.3.  
- [ ] Honor measured latency/accuracy from Phase 0 (structured args if re-baselined).

**Exit:** CoPilot goldens green; GC/TTL tests green; late-approve denied; axes tests if combined path live.

**Why:** Product heart — only after measurement.

---

### Phase 2.5 — Extract `resolve_node` (optional, after Phase 2 stable)
**Goal:** Industrial SRP — policy evaluation out of tool execution node.

- [ ] Graph topology: `supervisor → resolve → tool_worker | respond`.  
- [ ] Move `authorize_effect` scheduling into `resolve_node`; tool_worker only executes authorized calls.  
- [ ] Swarm-focused unit tests against resolve in isolation.

**Exit:** tool_worker has no policy branching; resolve is the single decision surface for graph path.

---

### Phase 3 — Semantic kinds on unified HITL bus + channel parity (1–2 weeks)
**Goal:** Confirm/clarify cards on the **existing** approval bus (not a new channel).

- [ ] Extend interrupt payload: `kind=security|semantic_clarify|semantic_confirm|combined`.  
- [ ] Web UI card + gateway button handlers (same resume as HITL).  
- [ ] Resume patches slots, checks TTL, re-enters gate.  
- [ ] Options for “use memory date” vs “use new date” vs “cancel”.  
- [ ] Surface expiry when useful.

**Exit:** Telegram + Web resolve the same `commitment_id` on one bus.

**Why:** Clarify without good UX fails adoption; one bus keeps API consumers simple.

---

### Phase 5 — Swarm / MCP depth + remaining escape hatches (**concurrent with Phase 3**, 2 weeks)
**Goal:** Finish what Phase 1 track (a) started — full swarm scope through handoffs, MCP, edge paths.

- [ ] Swarm danger classification = same side-effect registry.  
- [ ] **Scope token through handoff cycle** (§3.11) fully tested.  
- [ ] MCP tools: semantic_tier + fail-closed unknown.  
- [ ] Any remaining IDE paths not covered in Phase 1.  
- [ ] Tests: worker escalation denied; A→B→A scope stable.

**Exit:** No production mutator path without `authorize_effect`.

**Why:** Phase 1 already wires registry for memory/schedule; Phase 5 completes multi-worker and MCP.

---

### Phase 4 — Act-specific resolvers (remind first, then expand) (2–4 weeks)
**Goal:** Productionize resolvers on top of **Phase 0 corpus** (already exists). Expand coverage; tune operating point from G2 data.

- [ ] `remind` resolver hardened: memory anchor, lead vs event-at, `request_at` recompute.  
- [ ] Grow corpus beyond Phase 0 minimum; keep EN/AR parity.  
- [ ] `cancel_job` unique match.  
- [ ] `store_fact` / `revise_fact` revision cues EN/AR.  
- [ ] Structured timing / structured args if §R2.2 triggered.  
- [ ] Set false-clarify **operating point from measured ROC** (hope ~15% only if data supports).

**Exit:** Conflict goldens false-allow = 0; false-clarify at chosen point documented; ≥95% on remind goldens.

**Why:** Corpus first (Phase 0); Phase 4 is depth, not first measurement.

---

### Phase 6 — Autonomy modes & progressive trust (1–2 weeks)
**Goal:** Intelligent *and* fast for trusted patterns.

| Mode | Behavior |
|------|----------|
| `strict` | Clarify on medium+ conflicts; confirm critical acts |
| `balanced` (default) | Allow high-conf zero-conflict; clarify conflicts; confirm critical; false clarify at operating point from **G2** (§R2.7) |
| `autonomous` | Allow more; still block critical memory supersede without assert |
| `yolo` | Existing grants; audit only; TTL still applies to abandoned drafts |

- [ ] Per-act learning later (optional, opt-in).

**Exit:** Power users not nannied; defaults protect newcomers.

---

### Phase 7 — Self-improvement & soul under commitment (1 week)
**Goal:** Behavior mutation is a critical act.

- [ ] Soul auto-apply → `needs_confirm` on unified bus or staged pending.  
- [ ] Fence remains; commitment adds human authority.

---

### Phase 8 — Hardening, docs, production checklist (1 week)
- [ ] Docs: architecture section “Commitment layer” + GC/TTL ops  
- [ ] Ops: metrics, kill-switch `agent.commitment.enabled=0`  
- [ ] Production checklist row  
- [ ] CHANGELOG + diagnosis-map entry for “wrong reminder / memory overwrite”  
- [ ] Full regression + commitment corpus CI job (EN+AR)  
- [ ] Optional: design note for local tiny classifier (nice-to-have)

---

## 10. Suggested work breakdown (PR stack)

Order for clean review (each PR shippable):

1. **PR-A:** CoPilot failing goldens + mutator logging (Phase 0 start)  
2. **PR-B:** EN+AR corpus (≥500) + candidate conflict/relative-time path + G1/G2 reports (Phase 0 exit)  
3. **PR-C:** `mutate_belief` gateway + YAML predicates + conservative auto-store (Phase 1)  
4. **PR-D:** Side-effect registry + unregistered fail-closed + HITL parity (Phase 1)  
5. **PR-E:** `authorize_effect` + **`LocalToolRegistry.execute` wiring** audit-only (Phase 1 track a)  
6. **PR-F:** Commitment tables + TTL/GC + tiered retention (Phase 2 foundation — after G1–G3)  
7. **PR-G:** tool_worker full gate + `request_at` + multi-act (Phase 2)  
8. **PR-H:** Unified-bus semantic kinds + Web cards + **combined axes** (Phase 3) **∥** **PR-I:** Swarm scope through handoff + MCP (Phase 5)  
9. **PR-J:** Gateway buttons parity (Phase 3)  
10. **PR-K:** Remind resolver depth on existing corpus (Phase 4)  
11. **PR-L:** Modes + docs + CI (Phases 6–8)  
12. **PR-M (optional):** Extract `resolve_node` (Phase 2.5)

Do **not** start with UI polish or a gate-LLM on every mutator. If G2 fails, expand to **structured args** before inventing mid-phase architecture.

---

## 11. Implementation notes (Kazma-specific)

### 11.1 Files likely touched

| Area | Files |
|------|-------|
| Gate | `agent/graph_builder.py` (`tool_worker_node`) |
| Focus (keep) | `agent/turn_input.py`, `agent/state.py` (add commitment fields) |
| Registry | `safety/hitl.py`, new `safety/side_effects.py` + `safety/commitment.py` (store/GC/authorize) |
| Store/GC | ops SQLite (`memory_ops.db`), sweeper near memory worker or cron cadence |
| Memory | `memory/belief_mutation.py`, consolidator, belief_extractor, tool_registry memory tools |
| Cron | skill `task_scheduler_cron/tools.py`, optionally richer schedule API |
| UX | `sse_chat.py`, HITL approve routes, gateway adapters — **extend payload kinds only** |
| Swarm | worker dispatch scope from parent commitment |
| Tests | `tests/test_commitment_*.py`, TTL/GC, EN+AR remind corpus, HITL wiring |
| Config | `kazma.yaml` functional_predicates + commitment TTL; ConfigStore keys; Settings later |

### 11.2 State fields (SupervisorState)
Add optional:

```text
active_commitment_id: str | None
commitment_status: str | None
pending_clarify: dict | None
```

Do **not** put platform chat_id in state (existing invariant).

### 11.3 Latency budget
- Gate latency: **&lt; 20ms** p95 is a **target** (**G1** §R2.1), not a promise; extra LLM only per **§R2.2** contingency  
- Clarify/confirm: **interrupt suspend** (not thread block); user turn or button resume  
- Local tiny classifier (RTX 4090): MVP scope **only if** §R2.2 triggers (G2 fail + structured args insufficient)  

### 11.4 Failure modes of the plan itself

| Risk | Mitigation |
|------|------------|
| Over-clarify (annoying) | High-conf silent allow; balanced false clarify **&lt;15%**; modes |
| Under-clarify (still broken) | Critical acts confirm; conflict goldens; corpus CI |
| Sync blocking / timeouts | Never await user in tool_worker; interrupt only |
| Pending DB bloat / stale state | TTL + GC + pending_cap + supersede abort (§3.9) |
| Late approve after expiry | Fail closed |
| Double HITL (semantic + security) | Combined card on **one** bus |
| Model ignores clarify and re-fires tools | Hold mutators; strip tools while pending; TTL |
| Performance regression | No extra LLM in MVP gate |
| Escape via post-turn extract | Phase 1 gateway mandatory |
| Escape via IDE/registry | Phase 1 track (a) `registry.execute` → authorize (audit-only OK) |
| Swarm handoff scope leak | §3.11 token through `_handle_handoff`; Phase 5 tests |
| Arabic/RTL mis-parse | Phase 0 corpus + Phase 4 depth |
| Bad heuristic ROC | §R2.2 structured-args re-baseline before Phase 2 |
| Multi-replica split | Document residual; single-replica until shared ops store |
| Combined card lowers security | Axes independent; semantic never satisfies security |
| Approve-delay shifts fire_at | `request_at` recompute on resume |

---

## 12. Why this makes Kazma “most intelligent” (not just chat)

Intelligence in an agent product is:

| Dimension | Chatbot | Intelligent agent (this plan) |
|-----------|---------|-------------------------------|
| Goal understanding | Implicit in next token | Explicit Commitment + slots |
| Use of memory | Flavor text | Soft locks + conflict protocol |
| Action | Whatever tool call fits | Only when policy says ready |
| Uncertainty | Bluffs | Clarifies as a capability |
| Error recovery | Apologizes after damage | Blocks damage class |
| Learning | Uncontrolled soul/memory writes | Staged, audited |
| Trust | YOLO or nanny | Progressive modes |
| Proof | Demo videos | Golden corpus + metrics |

This is how enterprise agents, classic task dialog systems, and reliable tool-using systems converge — **with Kazma’s existing multi-platform and HITL strengths as force multipliers**, not replaced.

---

## 13. Minimal MVP — honest residual (R2 rewrite)

If resources are constrained, the **smallest useful ship** is:

1. Phase 0: failing CoPilot goldens + **corpus** + G1/G2 reports (+ contingency if needed)  
2. `mutate_belief` conflict block without user assert  
3. `authorize_effect` for `schedule_task` / `memory_store` / `cancel_scheduled` on **chat tool_worker**  
4. **`LocalToolRegistry.execute` same authorize** for those effect classes (audit-only deny OK — **no card required**)  
5. Metrics + kill-switch + TTL if any pending state is stored  

### What this stops
- **Chat-path instance** of the incident class (wrong schedule + silent memory supersede from supervisor tools).  
- **IDE/registry free-fire** for memory/schedule-class effects (Phase 1 track a).

### What this does **not** yet stop (known residual until later phases)
- Full semantic confirm UX / combined-card axes (Phase 3)  
- Swarm handoff scope completeness (Phase 5 / §3.11)  
- MCP / exotic mutators if unregistered handling incomplete  
- Soul under commitment (Phase 7)  
- Multi-replica commitment visibility (out of scope; residual risk)

**Do not claim “stops the incident class” without residual language.**  
**Do claim:** “Stops chat + registry free-fire for memory/schedule mutators under measured heuristics (or structured-args re-baseline).”

---

## 14. Open questions — RESOLVED (Revision 1)

See **§R1.2**. Residual design choices only:

| Residual | Owner at implement time |
|----------|-------------------------|
| Exact ops table migration helper location | Implementer (prefer next to memory ops access) |
| Whether `resolve_node` is Phase 2.5 or post-v1 | After Phase 2 metrics (complexity vs test pain) |
| When to add local classifier | Only if false allow or false clarify miss targets under heuristics |
| Documents/vault critical acts | Add to YAML `critical_acts` when those mutators ship through registry |

---

## 15. Review record (audit trail)

### Peer review A (→ Revision 1)

```text
VERDICT: approve-with-changes
STRENGTHS: core supersede fix; stack fit; read-only compat
MUST-CHANGE: GC/TTL for pending commitments
NICE-TO-HAVE: local classifier on RTX 4090
PHASE REORDER: Phase 5 concurrent with Phase 3
EFFORT: ok (Phase 2 up to ~4 weeks)
INCORPORATED: §3.9, R1.*, phase diagram, settled §14
```

### Peer review B / GLM (→ Revision 2) + Grok concurrence

```text
VERDICT: approve Phase 0–1; gate Phase 2 on empirical Phase 0 outputs
STRENGTHS: architecture + HITL reuse + tests-first + conflict-over-confidence
MUST-CHANGE / ADD:
  - Measure latency (G1) + heuristics (G2) before promising budgets
  - EN+AR corpus as Phase 0 deliverable with shape
  - Latency spike against real candidate conflict design
  - Contingency if numbers bad → structured args primary
  - §13 honesty: chat instance vs class; registry in Phase 1
  - Combined card = independent security ⊥ semantic axes
  - request_at vs approval-time fire_at
  - Swarm scope through handoff cycle (mechanism)
  - Unregistered tools fail-closed
  - Multi-replica residual honesty
  - Critical-act retention > 30d
  - Phase 1 split: choke wiring vs card UX
INCORPORATED: §R2.*, §3.11, §4.3 axes, §5 fail-closed, Phase 0–2 rewrite, §13
```

### Dual-review convergence

Both peers agree: **measure before Phase 2**; extend HITL not fork it; TTL/GC ship-blocker; no prompt-only fix.  
R2 adds what R1 lacked: **invalid measurement design**, **bad-number contingency**, and **registry vs UX clock split**.

### Third-pass audit sweep (→ R2 patch)

```text
VERDICT: R2 substantively complete; apply sweep before Phase 0 reads doc as ground truth
SWEEP (stale R1 hard-promises → targets, per R2.7 intent): §8.1 latency row, §8.4 red-team,
  §9 Phase-6 balanced row, §11.3 latency budget
ADDED (measurement validity): §R2.3 goldens held-out / test-only (anti-overfit);
  §R2.1 G1 production-scale cardinality or scaling curve (anti-fixture-vanity)
ADDED (correctness): §3.10 partial-effect policy (compensating vs validate-before-commit,
  by act reversibility); §R2.5 soul _auto_apply hook (apply-site check, not lock timing)
CLARIFIED: G1/G2 empirical gates vs G3 design gate
INCORPORATED: this patch block
```

---

## 16. One-paragraph pitch

Kazma today is a capable multi-platform ReAct agent with real safety and memory infrastructure, but it still **acts from free-form model tool calls** while treating memory as advisory context. The industry step up is a **Commitment Layer**: structured goals and slots, a policy gate that can clarify/confirm/deny before durable mutation (unified HITL bus, security ⊥ semantic axes), memory as soft locks, ops-SQLite commitments with TTL/GC and tiered retention, registry choke in Phase 1 (cards later), swarm scope through handoffs, and an EN+AR corpus built in **Phase 0** so latency and heuristic accuracy are **measured** — with structured-args contingency if numbers fail — before Phase 2 merges. One brain; invariants over vibes; honest residuals for multi-replica and late swarm/MCP depth.

---

## 17. Appendix A — Incident → requirement traceability

| Incident step | Requirement ID |
|---------------|----------------|
| “in 2 days” ambiguous | REQ-SLOT-RELATIVE-ANCHOR |
| Memory Sep 1 ignored | REQ-MEMORY-SOFT-LOCK |
| Wrong schedule created | REQ-GATE-BEFORE-SCHEDULE |
| Clarification overwrote memory | REQ-NO-SUPERSEDE-WITHOUT-ASSERT |
| Confident wrong reply | REQ-AUDIT-COMMITMENT + REQ-CLARIFY-FIRST-CLASS |
| User had to yell | REQ-PROACTIVE-CONFLICT-QUESTION |
| Stale pending / DB bloat | REQ-COMMITMENT-TTL-GC |
| IDE/registry free-fire | REQ-AUTHORIZE-EFFECT-REGISTRY-P1 |
| Swarm handoff escalation | REQ-SWARM-SCOPE-TOKEN |
| Arabic relative phrasing | REQ-AR-RELATIVE-TIME-CORPUS-P0 |
| Delayed approve shifts fire_at | REQ-REQUEST-AT-ANCHOR |
| Unregistered mutator bypass | REQ-REGISTRY-FAIL-CLOSED |
| Bad heuristic ROC | REQ-STRUCTURED-ARGS-FALLBACK |
| Multi-replica split brain | REQ-SINGLE-REPLICA-HONESTY |

## 18. Appendix B — Config sketch

```yaml
agent:
  commitment:
    enabled: true
    mode: balanced          # strict | balanced | autonomous
    high_confidence: 0.85
    clarify_on_memory_conflict: true
    confirm_critical_acts: true
    critical_acts: [revise_fact, exec, mutate_fs, send_outbound, config_change]
    couple_conflicting_mutators: true
    allow_ordered_multi_act: true
    max_clarifies_per_turn: 1
    # Set AFTER G2 — do not treat as hard promise pre-measurement
    false_clarify_budget: null      # e.g. 0.15 once ROC known
    latency_p95_target_ms: 20       # G1 validates
    unknown_mutator_policy: deny    # fail-closed
    # TTL / GC (MUST)
    ttl_draft_seconds: 3600
    ttl_pending_seconds: 86400
    ttl_ready_seconds: 900
    retention_days: 30              # ephemeral acts
    critical_retention_days: 365    # archive path for critical acts
    pending_cap_per_thread: 20
    gc_sweep_interval_seconds: 900
    # Combined card
    security_axis_independent: true # semantic never satisfies security

memory:
  auto_store_beliefs: conservative
  functional_predicates:
    - copilot_next_reset
    - subscription_ends
    - preferred_*
```

## 19. Appendix C — Glossary

| Term | Meaning |
|------|---------|
| Commitment | Structured decision to affect the world |
| Soft lock | High-conf belief that blocks silent overwrite |
| Semantic HITL | Confirm meaning/args on the **same** bus as security danger tools |
| Security ⊥ semantic | Combined card shows both; axes approve independently |
| Functional belief | “Current truth” fact (date, default, identity) vs narrative note |
| request_at | User-message time; anchor for relative fire_at (not approval time) |
| authorize_effect | Single policy function for graph, IDE, swarm, extract paths |
| Parent scope token | Auth ceiling copied through swarm handoff; never widens |
| G1/G2/G3 | Phase 0 empirical gates blocking Phase 2 merge |

---

## 20. Implementation readiness

| Phase | Ready? |
|-------|--------|
| **Phase 0** | **✅ DONE (2026-08-11)** — G1/G2/G3 passed; §R2.2 not triggered. See `docs/plans/COMMITMENT_PHASE0_EXIT_REPORT.md`. |
| **Phase 1** | **Yes — start now** (registry choke + memory gateway; no card UX required). Flips the CoPilot golden green. |
| **Phase 2** | **Unblocked** — G1–G3 satisfied, no §R2.2 re-baseline needed. Mutator-gate PRs may open. |

**Phase 0 result (Revision 2):** G2 false-allow = 0 on held-out goldens (100%
accuracy / 0 % false-clarify); G1 p95 = 0.39 ms @ production scale (50 beliefs,
~50× under the 20 ms target); G3 design gate met. Heuristic resolver
sufficient — "no extra LLM in MVP" holds.

**Next step:** Phase 1 track (a) — `side_effects.py` registry +
`mutate_belief` source-trust gate + `LocalToolRegistry.execute` choke wiring
(audit-only). This is also what flips `test_commitment_copilot_incident.py`
from xfail to passing.

---

## 21. Remaining follow-ons (next-run plan)

All Commitment Layer phases are shipped, live, tested (183+ green), and
documented. The following are UX polish + ops documentation that don't block
functionality but complete the vision.

### A. Per-option rendering (semantic card shows discrete choices)

Today the generic Approve/Deny buttons work on every platform (Approve = best
option, Deny = cancel via `build_resume_value`). The per-option buttons
("Use memory date" / "Use from-now" / "Cancel") need rendering:

**Web UI:**
- `kazma-ui/kazma_ui/static/js/chat.js` ~L2246 (`renderHitlCard`): add a branch
  at the top — when `data.kind` starts with `"semantic_"`, render one button per
  `data.items[0].options`; each calls a new `submitClarifyChoice(optionId)` that
  POSTs `{action:"approve", choices:{[tool_call_id]: optionId}}`. Keep existing
  Approve/Deny for `kind=security`.
- `kazma-ui/kazma_ui/static/js/hitl_approval.js` ~L66 (`renderApprovals`): same
  branch when `item.kind` is semantic.
- `kazma-ui/kazma_ui/static/js/stores/agentStore.js` ~L388 (`submitApproval`):
  add a `choices` param to the WS payload.
- `kazma-ui/kazma_ui/routes_direct.py` ~L3069: read `body.get("choices")` when
  kind is semantic and pass the specific option_id to `build_resume_value`
  (extend it to accept an explicit `option_id` override).

**Gateway (Telegram/Discord/Slack):**
- `kazma-gateway/kazma_gateway/adapters/telegram_keyboards.py`: add
  `build_semantic_keyboard(request_id, options)` — one button per option.
- `kazma-gateway/kazma_gateway/adapters/platform_keyboards.py`: same for
  Discord components + Slack blocks.
- `kazma-gateway/kazma_gateway/adapters/platform_callbacks.py`: parse
  `hitl:opt:{option_id}:{request_id}` → synthetic `/hitl opt {thread} {opt}`.
- `kazma-gateway/kazma_gateway/agent_handler/hitl.py`:
  `_handle_hitl_resume` parse `/hitl opt <thread> <option_id>`;
  `_build_approval_prompt` branch on `payload["kind"]` to render the question +
  option keyboard.

### B. Auto-deny paths handle semantic kind cleanly

Today `hitl_timeout.py` and `hitl_supersede.py` send the security
`{approved: false}` shape for semantic interrupts. The tc is still blocked
(safe) but with an "unresolved" error instead of a clean "cancelled." Fix:

- `kazma-ui/kazma_ui/hitl_timeout.py` ~L52: before building `resume_value`,
  read the pending interrupt's kind (from `aget_state → tasks → interrupts`).
  If semantic → `build_resume_value(payload, approved=False)` which returns
  `{tcid: "cancel"}`.
- `kazma-core/kazma_core/agent/hitl_supersede.py` ~L77, L85: same — for each
  pending interrupt, check kind and use `build_resume_value` instead of the
  hardcoded `{"approved": False}`.

### C. Doc + ops finish

- `docs/docs/ops/production-checklist.md`: add a "Commitment Layer" row —
  kill-switch (`KAZMA_COMMITMENT_ENABLED`), GC cadence (15min),
  `swarm_scope_enforce` / `soul_requires_confirm` flags default OFF,
  `/metrics` endpoint.
- `docs/docs/ops/diagnosis-map.md`: add "Wrong reminder date / memory
  overwrite" → commitment gate + source-trust gate + conservative auto-store.
- `tests/test_commitment_scenarios.py`: add the remaining §8.3 scenarios
  (coupled-batch hold, ordered multi-act parent/child, post-turn-extract
  no-supersede, GC retention edge cases, swarm-scope deny with default token).
- CI: add a pytest marker `commitment` so the corpus + gate tests run as a
  named group; note in AGENTS.md §20.
