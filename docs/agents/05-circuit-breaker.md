<!-- AGENTS.md, piece 05-circuit-breaker. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
### 5. Circuit Breaker Half-Open (`kazma-core/kazma_core/swarm/reliability.py`) + ReliabilityRegistry (`reliability_registry.py`)
- `_probe_in_flight` flag ensures only ONE dispatch passes through half-open state
- Both `record_success()` and `record_failure()` reset it
- Never remove this flag or concurrent calls bypass the probe semantics
- ReliabilityRegistry (P2-1 refactor) owns all breaker/retry/timeout/validator state
- **An outcome names its admission** (Swarm audit SW-E11, 2026-10-14).
  `admit()` / `check_or_raise()` return a `BreakerAdmission(generation,
  probe)`; every change of state bumps `generation`. An outcome from an older
  generation, or any outcome but the probe's own while a probe is in flight, is
  stale: counted (`stale_outcomes`), never applied. A dispatch admitted before
  the trip closed a half-open breaker while its probe still ran. Callers pass
  the admission (`record_success(admission)`); one without it cannot resolve a
  probe. Shared breakers write by compare-and-set: never over a newer
  generation. A dispatch is one in-flight count whatever its retries
  (`SwarmWorker.in_flight`); the autoscaler never reaps an instance with one in
  flight (SW-E12). Gate: `tests/test_swarm_boundaries_and_signals.py`.

