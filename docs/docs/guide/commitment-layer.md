---
id: commitment-layer
title: Commitment Layer (resolve-before-act)
sidebar_label: Commitment Layer
description: The policy gate between the LLM and durable mutations — Kazma resolves intent against memory before acting, and disambiguates ambiguous acts with semantic clarify/confirm cards.
---

# Commitment Layer — resolve-before-act

> A **policy gate between the LLM and durable mutations.** Before the agent is
> allowed to schedule, send, execute, or change config, Kazma resolves its
> intent against memory and policy. Ambiguous acts are disambiguated with an
> interactive **semantic clarify/confirm card** on every platform. All phases
> (0–8) are shipped; every enforcement layer is **fail-open** with a
> default-off kill-switch.

**Related:** [Security & Safety](./security-and-safety) (the three HITL gates),
[Memory & RAG](./memory-and-rag) (the belief store the gate reads),
[Slash commands](../reference/slash-commands).

---

## 1. Why it exists — the incident class it blocks

Consider a scheduling agent. The user says *"remind me about the meeting."*
Without a commitment layer, an LLM can **invent a plausible date**, schedule it,
and — worse — overwrite the user's real belief about when the meeting is. This
is a real class of failure (the "CoPilot incident"): the model acts confidently
on a hallucinated fact and the system lets it persist.

Kazma resolves intent against memory **before** acting, and blocks the bad path
at **two** layers:

1. **The schedule layer** — a relative phrase ("next Tuesday", "in two days") is
   anchored to a real memory event, and the tool's `fire_at` is rewritten to the
   memory-correct value (the gate's value wins over the model's).
2. **The memory layer** — a `user_explicit` belief may not be superseded by a
   lower-trust (`llm_inferred` / `system_tool`) source (the source-trust gate).

Removing either invariant reintroduces the incident class.

---

## 2. The three choke points

Every mutating path goes through `authorize_effect` (in
`kazma_core/safety/commitment/authorize.py`). There are three call sites:

| Choke point | Path | Role |
|---|---|---|
| **`tool_worker_node`** (`agent/graph_builder.py`) | Single-agent chat | Full decisions; runs **before** the security HITL split so it can rewrite args first |
| **`LocalToolRegistry.execute`** (`agent/tool_registry.py`) | IDE / swarm | Audit-only (no turn context); full decisions are graph-side |
| **`_mutate_functional`** (`memory/belief_mutation.py`) | Memory writes | The corruption half — source-trust gate (independent of the policy gate) |

---

## 3. The decision mapping

`authorize_effect` returns an `EffectDecision` with one of four outcomes:

| Decision | Meaning | What happens |
|---|---|---|
| **`allow`** | Execute | Optionally with `rewritten_args` (e.g. the gate computed the correct `fire_at` for a remind) |
| **`clarify`** | Interrupt with a targeted question | A real interrupt card fires with discrete options (see §4) |
| **`confirm`** | Interrupt for explicit OK | Same card UX, used for critical acts |
| **`deny`** | Blocked | Clear error returned to the model; no card, no execution |

Plus an **audit-only** path for read tools, `mutate_fs` (containment is in
`IdeService.resolve`), and `delegate` (HMAC trust checked at skill-load time).

### Act-specific behavior

| Act | Resolver behavior |
|---|---|
| **remind** (schedule) | Relative time anchored to a memory event → **allow + rewrite** `fire_at`. Ambiguous relative phrase with a nearby event → `clarify`. Unsatisfiable → `deny`. |
| **cancel_job** | Resolves against pending cron jobs. |
| **exec** (shell) | A **denylist** blocks catastrophic commands (`rm -rf /`, fork bombs, `curl \| sh`, `dd of=/dev/`, `mkfs`, shutdown, `chmod 777 /`) **before** the HITL card. Safe commands pass through (HITL still applies). |
| **send_outbound** | When `agent.commitment.outbound_allowed_targets` is configured, unknown targets → `clarify` with the allowlist. |
| **config_change** | **Protected keys** (`safety.*`, `agent.commitment.*`, `notifications.lifecycle.*`) cannot be mutated by the agent — self-protection. |

> **Rewrite-on-allow:** the gate's computed `fire_at` (for remind) **always wins**
> over the model's original args. The original, possibly-wrong timing never
> reaches the scheduler.

---

## 4. Semantic clarify / confirm cards

When a decision is `clarify` or `confirm`, the graph suspends via LangGraph
`interrupt()` with a payload that carries a **question** and a list of
**options** — each option bundles a `slots_patch` (a partial argument update).

```mermaid
flowchart LR
    U[User: "remind me about the meeting"] --> M[Model calls schedule tool with ambiguous time]
    M --> G[Commitment gate: authorize_effect]
    G -->|ambiguous| C[Clarify card: "Which meeting?"]
    C --> O1[Option: Project sync — Tue 2pm]
    C --> O2[Option: 1:1 — Wed 10am]
    C --> OX[Cancel]
    O1 --> R[Resume: apply slots_patch → correct fire_at]
    OX --> X[Aborted cleanly]
```

- **Renders everywhere** — Web (chat + sidebar), Telegram, Discord, and Slack.
  Each platform renders one button per option.
- **Resume** applies the chosen option's `slots_patch` to the tool arguments and
  continues. The existing Approve/Deny buttons map to *best-option* / *cancel*.
- **Empty options list** → a free-text clarify (no discrete choices).
- **Cancel** is terminal — the model is told to stop the attempt and ask the
  user, never to silently retry.

This is the unified HITL bus (the same one that handles danger-tool approvals) —
there is no second interrupt channel.

---

## 5. Scope tokens (swarm)

Dispatched swarm workers can be capped to a privilege scope. When
`agent.commitment.swarm_scope_enforce` is on, workers default to
**semantic_tier HIGH** (exec/outbound/config/identity CRITICAL denied) and
`denied_acts = {soul_delta, identity, config_change}`. Default is **off**.

See `kazma_core/safety/commitment/scope.py` (`ScopeToken`, `default_worker_scope`,
`is_act_within_scope`).

---

## 6. Soul-confirm gate

The self-improvement engine persists "Soul deltas" — LLM-generated system-prompt
refinements derived from untrusted conversation/tool output. When
`agent.commitment.soul_requires_confirm` is on, deltas are **held** until an
operator confirms them via the queue at **`GET /api/commitment/soul/pending`**
and **`POST /api/commitment/soul/{cid}/confirm`** (or `/reject`). Default is off.

**Storage honesty:** Soul deltas are stored in `get_config_store()` under key
`self_improvement.agent_evolution` — **NOT** a free-standing JSON file. The
supervisor/main-agent Soul lives in ConfigStore-backed `agent_evolution.json` with
a `.migrated` rename for any legacy file. `_load_agent_evolution` / `_save_agent_evolution`
go through ConfigStore — do NOT reintroduce a direct `path.write_text` write (the old
`agent_evolution.json` was non-atomic and corruptible on crash/concurrency). A
compound read-modify-write lock (`_agent_evo_lock`) serializes
`apply_agent_mutation` within a process; ConfigStore's own lock only guards
individual get/set, not the multi-step sequence. Both are required.

A one-time migration (`_migrate_legacy_evolution_if_present`) moves any
pre-existing `agent_evolution.json` into ConfigStore and renames it
`.migrated`. Leave this in place.

Deltas are checked at creation time (`_analyze_success`/`_analyze_failure`)
AND at apply time (`_auto_apply`/`apply_agent_mutation`) — defense-in-depth.
Never inject a delta via the old `"Apply these refinements to your behaviour:"`
framing; always use `format_untrusted_block(evo, source="self_improvement")`.

Kill-switch `KAZMA_SELF_IMPROVEMENT=0` is checked live (not just at init) on
both the chat/supervisor path and the swarm worker path.

---

## 7. Modes & kill-switches

### Modes (`agent.commitment.mode` or `KAZMA_COMMITMENT_MODE`)

| Mode | Behavior |
|---|---|
| **`strict`** | Most conservative — broadest clarify/confirm surface |
| **`balanced`** *(default)* | Resolve clear intent, clarify only genuine ambiguity |
| **`autonomous`** | Minimal interruption |
| **`yolo`** | Effectively passthrough (danger tools still HITL-gated separately) |

### Kill-switches (all default OFF unless noted — `SWARM_SCOPE_ENFORCE` defaults ON since 2026-08-15)

| Env var | Layer | Default | Effect |
|---|---|---|---|
| `KAZMA_COMMITMENT_ENABLED` | Whole layer | **ON** | `0` disables the entire layer |
| `KAZMA_COMMITMENT_MODE` | Mode | `balanced` | Overrides the mode |
| `KAZMA_COMMITMENT_SWARM_SCOPE_ENFORCE` | Swarm scope | **on** | Caps dispatched-worker privileges (disable to opt out) |
| `KAZMA_COMMITMENT_SOUL_REQUIRES_CONFIRM` | Soul gate | off | Holds Soul deltas for confirmation |
| `KAZMA_AUTO_STORE_BELIEFS` | Memory auto-store | conservative | How aggressively beliefs are stored |

Every config key is also settable in the Settings UI / ConfigStore under
`agent.commitment.*` and is **re-read live** (`get_commitment_config`) — no
restart required.

---

## 8. Metrics

Prometheus metrics exposed at `/metrics`:

- `kazma_commitment_decisions_total{decision="allow|clarify|confirm|deny|cancelled"}`
- `kazma_commitment_pending` — currently-held commitments awaiting resolution

---

## 9. Components (`kazma_core/safety/commitment/`)

| Module | Responsibility |
|---|---|
| `side_effects.py` | The single source-of-truth registry: tool → `ToolEffectProfile`. MCP tools (`mcp__*`) route through `classify_mcp_tool_effect`. Unregistered mutators fail-closed (tokenized) when `enforce_unknown_mutators` is on. |
| `authorize.py` | `authorize_effect` (the gate) + `EffectDecision` + the act resolvers (remind, cancel_job, exec, send_outbound, config_change) |
| `relative_time.py` | `resolve_remind` — relative-time anchoring (EN + AR), G2-measured (0 false-allow) |
| `store.py` | `Commitment` rows + ops-SQLite tables + TTL/GC + tiered retention + `list_pending_soul()` |
| `constraints.py` | `is_commitment_enabled` + `load_constraint_beliefs` + `cron_pending_jobs` |
| `config.py` | `get_commitment_config` — the one live config reader |
| `scope.py` | `ScopeToken` + `swarm_scope` (ContextVar) + `default_worker_scope()` + `is_act_within_scope()` |
| `resume.py` | `build_resume_value()` + `is_semantic_kind()` — maps Approve/Deny → option/cancel on every platform |

---

## 10. Honesty & defaults

- **Fail-open throughout** — any error in the gate logs at debug and lets the
  tool proceed; the layer never breaks a working turn.
- **Conservative auto-store** — beliefs are not over-written aggressively.
- **No late approve** — a commitment cannot be retroactively approved after the
  fact; it must be resolved in-turn.
- **GC cadence** — expired/held commitments are reaped on the standard schedule.

The whole layer is **on by default** in `balanced` mode; turn it off with
`KAZMA_COMMITMENT_ENABLED=0` if you need raw passthrough behavior.
