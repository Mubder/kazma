# Commitment Layer — Phase 0 Exit Report

**Plan:** `docs/plans/INTELLIGENT_AGENT_COMMITMENT_LAYER.md` (Revision 2 + audit sweep)
**Date:** 2026-08-11
**Verdict:** **Phase 0 PASSED — G1, G2, G3 satisfied. §R2.2 not triggered. Phase 2 unblocked.**

This report is the binding Phase 0 record. Phase 2 mutator-gate PRs may open
once it is committed.

---

## Gate results

### G1 — Latency ✅ (target <20ms p95, §R2.1)

Candidate path: `kazma_core.safety.commitment.resolve_remind` (the same code
Phase 2's `authorize_effect` will call for the `remind` act). Measured against
production-scale belief counts (§R2.1 audit clause: real cardinality, not
3-row fixtures).

| beliefs (recall input) | before-event p95 | from-now p95 (worst) |
|-----------------------:|-----------------:|---------------------:|
| 10                     | 0.06 ms          | 0.28 ms              |
| **50 (production top-K)** | **0.16 ms**   | **0.39 ms**          |
| 100                    | 0.28 ms          | 0.53 ms              |
| 500                    | 1.19 ms          | 1.67 ms              |
| 1 000                  | 2.37 ms          | 2.55 ms              |
| 5 000                  | 10.93 ms         | 11.56 ms             |
| 10 000                 | 22.18 ms         | 23.72 ms             |

**Production-realistic operating point (50 beliefs, the recall top-K the gate
actually receives): p95 = 0.39 ms — ~50× under the 20ms target, no extra LLM.**

Scaling is linear in belief-count; the cliff (~22ms) only appears at 10 000
beliefs, which is not a production input (the existing memory-recall layer
caps the gate's input at the per-turn top-K, not the full store). Reproduce:
`python tests/test_commitment_g1_latency.py` (full curve) or
`pytest tests/test_commitment_g1_latency.py::test_g1_production_scale_under_20ms`
(CI gate).

### G2 — Accuracy ✅ (gate: false-allow = 0 on held-out goldens, §R2.1)

Corpus: `tests/fixtures/commitment/relative_time_corpus.jsonl` — **500 cases**,
**49.8% Arabic** (≥40% floor met, §R2.3), semantically labeled (ground truth
defined in the generator, NOT by the resolver), held-out golden / train /
hostile split. Goldens are **test-only** (§R2.3 audit clause: never tuned
against).

| split   | n  | accuracy | FALSE_ALLOW (dangerous) | FALSE_CLARIFY (annoying) |
|---------|---:|---------:|------------------------:|-------------------------:|
| golden  | 88 | **100.0 %** | **0**               | **0**                    |
| hostile | 12 | 91.7 %   | 0                      | 1 (8.3 %)                |
| all     | 500| 98.2 %   | **0**                  | 9 (1.8 %)                |

**False-allow = 0 on held-out goldens — the gate holds.** False-clarify is
1.8 % overall (well under the ~25 % provisional red line in §R2.2).

Reproduce: `pytest tests/test_commitment_corpus.py -v`.

### G3 — Honesty / choke point ✅ (design gate, §R2.1)

- §13 honest residual rewrite: **done** in Revision 2 of the plan.
- Phase 1 registry choke (`LocalToolRegistry.execute` → `authorize_effect` for
  memory/schedule-class effects): **scoped** (PR-A already landed the
  observability instrumentation on the same path; the gate itself is Phase 1).

---

## §R2.2 decision: NOT triggered

The structured-args / classifier / gate-LLM fallback chain does **not** engage.
Both G2 triggers are clear:

- false-allow > 0 on conflict goldens → **not met** (it is 0).
- operationally-unacceptable false-clarify (> ~25 %) → **not met** (1.8 %).

The heuristic resolver is sufficient for the Phase 0 corpus. The "no extra LLM
in MVP" intent (§R2.7) holds. Structured tool args remain available as a future
fallback if real-world distribution (beyond this corpus) degrades accuracy.

---

## Artifacts delivered (Phase 0)

| Artifact | Path |
|----------|------|
| Candidate resolver (§R2.4) | `kazma-core/kazma_core/safety/commitment/{__init__,relative_time}.py` |
| Corpus generator + corpus (§R2.3) | `tests/fixtures/commitment/{generate_corpus.py,relative_time_corpus.jsonl}` |
| G2 accuracy harness (gate) | `tests/test_commitment_corpus.py` |
| G1 latency harness (gate + curve) | `tests/test_commitment_g1_latency.py` |
| CoPilot failing golden (PR-A) | `tests/test_commitment_copilot_incident.py` |
| Mutator / supersede / drift instrumentation (PR-A) | `graph_builder.py`, `belief_mutation.py`, `hitl.py` |

---

## Known residuals (carried forward, not blockers)

- **Multi-replica (§R2.5):** commitments will live on the SQLite ops plane →
  single-replica consistency only. A commitment made on replica A is invisible
  to replica B's gate until the ops store is shared (Postgres port). Out of
  scope for Phase 2; documented like the document-metadata honesty caveat.
- **Corpus v1:** 500 cases covers the remind-act conflict/ambiguity space well
  but is not exhaustive — `cancel_job`, `send_outbound`, `mutate_fs` acts get
  their corpora as their Phase 4 resolvers ship.
- **AR diacritics:** the resolver normalizes Arabic-Indic/Eastern digits but
  does not strip harakat; diacritized input may mis-parse. Not in the corpus
  (which is undiacritized); deferred.
- **CoPilot memory golden:** still xfail (by design) until Phase 1's
  `mutate_belief` source-trust gate lands — then it flips to passing.

---

## Sign-off

Phase 0 is complete. **Phase 2 (commitment store + `authorize_effect` in
`tool_worker_node`) is unblocked** per the plan's §20 readiness table. The
recommended next PR is Phase 1 track (a): the `side_effects.py` registry +
`mutate_belief` source-trust gate (which also flips the CoPilot golden green).
