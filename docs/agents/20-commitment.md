<!-- AGENTS.md, piece 20-commitment. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
### 20. Commitment Layer (`kazma-core/kazma_core/safety/commitment/`)

A policy gate between the LLM and durable mutations. Kazma resolves intent
against memory BEFORE acting — the CoPilot incident class (model invents a
date, schedules it, overwrites the user's real belief) is blocked at both the
schedule layer and the memory layer. Full plan + Phase-0 exit report:
`docs/plans/INTELLIGENT_AGENT_COMMITMENT_LAYER.md`,
`docs/plans/COMMITMENT_PHASE0_EXIT_REPORT.md`. **All phases shipped**: 0–2
(core + gate + store + TTL/GC), 3 (semantic clarify/confirm interrupt card),
4 (remind + cancel_job + exec/outbound/config resolvers), 5 (swarm scope-token
default + MCP classification), 6 (autonomy modes), 7 (soul confirm gate), 8
(docs + metrics). Default-off kill-switches on every enforcement layer.
**Fail posture (deep-audit 2026-08-19):** authorization-engine EXCEPTIONS on
semantic tools fail CLOSED at both chokes (a broken policy engine must not
free-fire the remind/exec classes); the layer kill-switch and
import-unavailable degradation stay fail-open (treated as layer-off).

**A. Two `authorize_effect` choke points + an independent memory-side gate.**
- `agent/graph_tool_worker.py:tool_worker_node` — the single-agent chat path;
  the gate runs BEFORE the security HITL split so it can rewrite args first.
- `agent/tool_registry.py:LocalToolRegistry.execute` — the IDE/swarm path
  (mostly audit-only — remind/cancel_job decisions need graph context — but
  the exec denylist / outbound allowlist / config protected-key resolvers DO
  enforce here).
- `memory/belief_mutation.py:_mutate_functional` — the memory corruption half
  is gated by its OWN source-trust check (a fact the user stated yields only
  to the user's own words, `memory.provenance.may_overrule`), NOT by an
  `authorize_effect` call — two independent defenses, by design.

**B. The decision mapping (§3.4). `authorize_effect` returns one of:**
- `allow` (+ optional `rewritten_args`): for the remind act, the gate anchors
  the relative phrase to a memory event and **rewrites the tool args to the
  memory-correct fire_at**: an aware ISO instant computed in the operator's
  zone (`cron.timezone`, §16G), never a naive time read as UTC. A recurring
  timing is never rewritten, and a recurring request ("every day at 9")
  booked as a one-off is denied (Scheduled audit SCH-E03, E05, 2026-10-14).
  `edit_scheduled` is its own act (`edit_job`): the caller's own jobs only,
  and the new timing through the same resolver. For exec, the denylist blocks catastrophes before
  the HITL card. For config, protected keys are denied. For outbound, the
  target allowlist is checked.
- `clarify` / `confirm`: a real interrupt card fires on the unified HITL bus
  (kind=semantic_clarify/confirm) with discrete options. Per-option buttons
  render on Web (chat.js + sidebar), Telegram, Discord, and Slack. Resume
  applies the chosen `slots_patch`. The existing Approve/Deny buttons map to
  best-option / cancel via `build_resume_value`.
- `deny`: blocked with a clear error; no card. The tool worker returns it
  as a `policy` refusal and the model goes on (2026-10-20: it was
  `terminal`, the clarify-card outcome, and a sandbox-refused `python_exec`
  ended a report turn before `generate_pdf`; gate
  `tests/test_policy_deny_keeps_the_turn.py`, the terminal outcome as the
  negative control).
- Audit-only: read tools, `mutate_fs` (containment in `IdeService.resolve`),
  `delegate` (HMAC trust at skill-load).

**C. Invariants — removing any reintroduces the incident class:**
- **Source-trust gate** (`_mutate_functional`, and the history path): a fact
  the user stated may NOT be superseded by a lower-trust source. Kill-switch:
  `cfg.v2.functional_supersede_requires_user_assert`. **The provenance
  ladder** (`memory/provenance.py`, Memory audit M03, 2026-10-13):
  `user_explicit` -- the user's own channel only (the Memory page, a chat
  command, or a memory tool on a turn whose latest user message asks to
  remember AND holds 80 % of the stored words); `user_stated` -- the user's
  literal statement read by the heuristic pass from their own message;
  then `system_tool`, `agent_asserted` (a memory tool the user did not ask
  for) and `llm_inferred`. A `user_*` fact yields only to `user_*`, in its
  own turn too (the model pass's same-turn correction was removed by rule A
  of F1's second security review, 2026-11-18; §15H's entry on a model's
  reading).
  Until then `memory_store` / `memory_link_entities` wrote `user_explicit`
  for anything the model chose (a fetched page, an email), unscreened, and
  the user's own words were `llm_inferred`: the model's fact blocked the
  user's correction. The tools now screen (`filter_injection`,
  `is_override_delta`; refused, nothing stored) and answer
  `user_fact_stands` when a write would replace the user's fact -- the user
  corrects their own facts. Gate: `tests/test_memory_provenance.py` (the
  R02 witnesses inverted, EN/AR remember requests, the injection corpus,
  the same-turn rule, the old gate as negative control, every product site
  writing `user_explicit` declared as a user channel). The rows the old tools
  wrote are relabelled by `scripts/memory_relabel_provenance.py`
  (`memory/provenance_relabel.py`, 2026-10-15; `size` names ids, never
  values; `apply --db --yes` copies to `<db>.pre_relabel` first): a
  `user_explicit` fact a memory tool's note names (or, before notes named
  their chat, holds its value within seconds) or that names a chat turn
  becomes `agent_asserted`, unless the Memory page edited it, an operator
  action named it, or its turn asked to remember those words (today's rule);
  the page's links and the V1 backfill are left. One audit row per fact; the
  live run is the owner's. Gate: `tests/test_memory_relabel_provenance.py`.
- **Rewrite-on-allow**: the gate's fire_at wins over the model's args — do not
  let the original (possibly wrong) `timing` reach the scheduler for remind.
  The fire_at is one instant in the operator's zone, and a recurrence is kept
  as the user asked, never turned into one fire
  (`tests/test_cron_timing_model.py`).
- **Exec denylist**: catastrophic commands (`rm -rf /`, fork bombs, `curl|sh`,
  `dd of=/dev/`, `mkfs`, shutdown, `chmod 777 /`) are denied BEFORE the HITL
  card. Safe commands pass through (HITL still applies).
- **Config protected keys**: `safety.*`, `agent.commitment.*`,
  `notifications.lifecycle.*` cannot be mutated by the agent (self-protection).
- **Outbound allowlist**: when `agent.commitment.outbound_allowed_targets` is
  configured, unknown targets → clarify with the allowlist.
- **Swarm scope default** (`worker_dispatch._do_dispatch`): when
  `swarm_scope_enforce` is on, dispatched workers are capped at semantic_tier
  HIGH (deny exec/outbound/config/identity CRITICAL) + denied_acts=
  {soul_delta, identity, config_change}. Default ON since 2026-08-15 (intent-engine auto-dispatch) — opt-OUT via the env/ConfigStore kill-switch.
- **Soul confirm gate** (`apply_agent_mutation`/`_auto_apply`): when
  `soul_requires_confirm` is on, soul deltas are held until confirmed via
  `POST /api/commitment/soul/{cid}/confirm`. Mint-wired at both apply callers.
  Config default is off; **auto-ON in production / multi-user** unless the
  operator set the key explicitly (`get_commitment_config`).
- **Fail-open + kill-switch**: `KAZMA_COMMITMENT_ENABLED=0` disables the whole
  layer. Every enforcement layer has its own default-OFF flag. (Engine
  *exceptions* on semantic tools fail closed at both chokes — see the §20
  header; only the kill-switch/import-degradation paths fail open.)
- **Conservative auto-store**, **No late approve**, **GC cadence** — as before.

**D. Components (`kazma_core/safety/commitment/`, and `kazma_core/safety/side_effects.py`).**
- `safety/side_effects.py` — the single SoT registry: tool → `ToolEffectProfile`.
  Parity-tested. Unregistered mutators fail-closed (tokenized). MCP tools
  (`mcp__*`) route through `classify_mcp_tool_effect`.
- `authorize.py` — `authorize_effect` (the policy gate) + `EffectDecision` +
  the act resolvers: `_resolve_remind_act`, `_resolve_cancel_job_act`,
  `_resolve_edit_job_act`, `_resolve_exec_act` (denylist+cwd),
  `_resolve_send_outbound_act` (allowlist), `_resolve_config_change_act`
  (protected keys).
- `relative_time.py` — `resolve_remind` (EN+AR). G2-measured (0 false-allow)
  on one-off requests; recurring requests (EN+AR) are measured by the
  Scheduled corpus in `tests/test_cron_timing_model.py`, not by G2.
- `store.py` — `Commitment` + ops-SQLite tables + TTL/GC + tiered retention +
  `list_pending_soul()` (the confirm queue).
- `constraints.py` — `is_commitment_enabled` + `load_constraint_beliefs` +
  `cron_pending_jobs` (for the cancel_job resolver).
- `config.py` — `get_commitment_config` (the ONE config reader).
- `scope.py` — `ScopeToken` + `swarm_scope` (ContextVar) +
  `default_worker_scope()` + `is_act_within_scope()` (the privilege guard).
- `resume.py` — `build_resume_value()` + `is_semantic_kind()` (maps
  Approve/Deny → option/cancel for semantic interrupts on every platform).
- `_commitment_resolve_gate()` in `graph_tool_worker.py` — the extracted gate
  (Phase 2.5 SRP). Called from `tool_worker_node`.
- **Operator API**: `kazma_ui/commitment_api.py` —
  `GET /api/commitment/soul/pending`, `POST .../{cid}/confirm`, `POST .../{cid}/reject`.
- **Metrics**: `kazma_ui/metrics.py` exposes
  `kazma_commitment_decisions_total{decision=...}` + `kazma_commitment_pending`.

**E. Modes + kill-switches.**
- Modes (`agent.commitment.mode` / `KAZMA_COMMITMENT_MODE`): strict |
  balanced (default) | autonomous | yolo.
- Kill-switches (all default OFF / layer default ON):
  `KAZMA_COMMITMENT_ENABLED` (layer, default on),
  `KAZMA_COMMITMENT_SWARM_SCOPE_ENFORCE` (default ON since 2026-08-15),
  `KAZMA_COMMITMENT_SOUL_REQUIRES_CONFIRM` (default off; auto-ON in
  production / multi-user unless explicitly set),
  `KAZMA_AUTO_STORE_BELIEFS` (default conservative).

**Tests:** 15+ test files (`tests/test_commitment_*.py` +
`tests/test_side_effects.py`) — corpus/G1/G2, store+GC, authorize, clarify-card
interrupt+resume, tool_worker gate, scenarios (7), act resolvers, modes, scope,
soul, cancel_job, config. Run:
`python -m pytest tests/test_commitment_*.py tests/test_side_effects.py -m "not slow"`.

