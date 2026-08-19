"""Phase 0 — G1 latency gate for the candidate relative-time resolver (§R2.1).

Measures the conflict-detection path (``resolve_remind``) at production-scale
belief counts and reports a latency-vs-scale curve, per the audit-swept §R2.1
(which requires production cardinality / a scaling curve, NOT a 3-row fixture).

Two paths are benchmarked:
  - **before-event** (Case 1, the CoPilot path): event matched → no full scan.
  - **from-now** (Case 3): the worst case — ``_nearby_unmentioned_event`` parses
    every supplied belief's date looking for a relevance-window collision.

Production reality: the gate receives the *recall top-K* (the structured
constraint candidates injected per turn), typically 10–50 beliefs — NOT the
whole store (recall is the existing memory layer's job). So the asserted gate
is p95 @ 50 beliefs < 20ms (the §R2.7 target), and the full curve is printed
so the headroom (and the cliff where it would break) is visible.

Run standalone:  python tests/test_commitment_g1_latency.py
"""

from __future__ import annotations

import statistics
import time
from datetime import datetime, timezone

from kazma_core.safety.commitment import resolve_remind

REQUEST_AT = datetime(2026, 8, 11, 10, 0, 0, tzinfo=timezone.utc)
TEXT_BEFORE = "remind me 2 days before the copilot reset"
TEXT_FROMNOW = "remind me in 2 days"

SIZES = [10, 50, 100, 500, 1000, 5000, 10000]


def _make_beliefs(n: int) -> list[dict]:
    """n beliefs: 1 event-matcher (copilot) + (n-1) realistic fillers.

    Fillers mix functional predicates with parseable ISO dates (exercising
    parse_belief_date on the from-now scan path) and generic predicates.
    """
    beliefs = [{"predicate": "copilot_next_reset", "object": "2026-09-01"}]
    preds = ["grok_next_reset", "zcode_next_reset", "subscription_ends",
             "noted", "preferred_ide", "active_project", "lives_in"]
    for i in range(max(0, n - 1)):
        p = preds[i % len(preds)]
        obj = (f"2026-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}"
               if i % 3 else f"item_{i}")
        beliefs.append({"predicate": p, "object": obj})
    return beliefs[:n]


def _percentiles(samples_ms: list[float]) -> tuple[float, float, float]:
    s = sorted(samples_ms)
    n = len(s)

    def pick(q: float) -> float:
        return s[min(n - 1, max(0, int(q * n)))]
    return pick(0.50), pick(0.95), pick(0.99)


def _bench(text: str, beliefs: list[dict], iters: int) -> tuple[float, float, float]:
    samples: list[float] = []
    for _ in range(iters):
        t0 = time.perf_counter()
        resolve_remind(text, request_at=REQUEST_AT, memory_beliefs=beliefs)
        samples.append((time.perf_counter() - t0) * 1000.0)
    return _percentiles(samples)


def measure_curve() -> list[dict]:
    rows = []
    for n in SIZES:
        beliefs = _make_beliefs(n)
        # fewer iterations at large N to keep the run quick (still >>30 samples)
        iters = 200 if n <= 1000 else (80 if n <= 5000 else 40)
        b_p50, b_p95, b_p99 = _bench(TEXT_BEFORE, beliefs, iters)
        f_p50, f_p95, f_p99 = _bench(TEXT_FROMNOW, beliefs, iters)
        rows.append({
            "beliefs": n, "iters": iters,
            "before_p50": b_p50, "before_p95": b_p95, "before_p99": b_p99,
            "fromnow_p50": f_p50, "fromnow_p95": f_p95, "fromnow_p99": f_p99,
        })
    return rows


def format_curve(rows: list[dict]) -> str:
    lines = [
        "=== G1 latency curve (resolve_remind) — p50 / p95 / p99 ms ===",
        f"{'beliefs':>7} | {'before-event':>20} | {'from-now (worst)':>20}",
        "-" * 55,
    ]
    for r in rows:
        lines.append(
            f"{r['beliefs']:>7} | {r['before_p50']:>5.2f}/{r['before_p95']:>5.2f}/"
            f"{r['before_p99']:>5.2f} | {r['fromnow_p50']:>5.2f}/{r['fromnow_p95']:>5.2f}/"
            f"{r['fromnow_p99']:>5.2f}"
        )
    return "\n".join(lines)


# ── pytest (fast gate in CI; full curve is the slow-marked report) ─────────

def test_g1_production_scale_under_20ms(capsys):
    """G1 GATE: p95 @ 50 beliefs (recall top-K, the production input) < 20ms.

    Fast — measures only the production-realistic scale so CI enforces the
    latency target cheaply. The full scaling curve (up to 10k beliefs) lives in
    ``test_g1_full_curve`` (slow-marked) and the standalone script.
    """
    beliefs = _make_beliefs(50)
    # warm + measure (worst-case path = from-now, which scans all beliefs)
    _bench(TEXT_FROMNOW, beliefs, 20)
    _, f_p95, _ = _bench(TEXT_FROMNOW, beliefs, 300)
    _, b_p95, _ = _bench(TEXT_BEFORE, beliefs, 300)
    worst = max(b_p95, f_p95)
    print(f"\nG1 @ 50 beliefs: before-event p95={b_p95:.3f}ms, "
          f"from-now p95={f_p95:.3f}ms → worst={worst:.3f}ms (target <20ms)")
    assert worst < 20.0, (
        f"G1 over budget at production scale: {worst:.3f}ms p95 @ 50 beliefs "
        f"— investigate before Phase 2 (§R2.7 target breached)"
    )


def test_g1_full_curve(capsys):
    """Full latency-vs-scale curve (Phase 0 G1 report deliverable, §R2.1).

    Unmarked since 2026-08-19 (deep-audit finding #19): the whole file runs
    in ~6s, well inside the per-chunk timeout, and it was the ONLY
    `slow`-marked file — so `-m "not slow"` silently excluded this curve
    from every CI run. Prints the curve so the headroom and the cliff are
    visible — the gate itself is enforced by
    ``test_g1_production_scale_under_20ms``.
    """
    rows = measure_curve()
    print("\n" + format_curve(rows))
    # sanity: realistic operating point must be well under budget
    realistic = next(r for r in rows if r["beliefs"] == 50)
    assert max(realistic["before_p95"], realistic["fromnow_p95"]) < 20.0


if __name__ == "__main__":
    print(format_curve(measure_curve()))
    beliefs50 = _make_beliefs(50)
    samples = []
    for _ in range(500):
        t0 = time.perf_counter()
        resolve_remind(TEXT_FROMNOW, request_at=REQUEST_AT, memory_beliefs=beliefs50)
        samples.append((time.perf_counter() - t0) * 1000.0)
    print(f"\n[50 beliefs, from-now] mean={statistics.mean(samples):.3f}ms "
          f"stdev={statistics.stdev(samples):.3f}ms over 500 runs")
