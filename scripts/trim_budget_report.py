#!/usr/bin/env python3
"""Trim-budget decision report (context-integrity deferred item).

The hardening plan's rule: read ``kazma_context_trims_total`` for a week of
real traffic BEFORE re-tuning ``agent.trim.token_budget``. This script makes
that gate executable instead of aspirational — it reads the Prometheus
``/metrics`` surface and prints the numbers with the decision rule applied.

Usage:
    python scripts/trim_budget_report.py                    # localhost:9090
    python scripts/trim_budget_report.py --url http://host:port/metrics
    python scripts/trim_budget_report.py --turns 400        # rate needs a denominator
    cat metrics.txt | python scripts/trim_budget_report.py --stdin

Decision rule (plan docs/plans/CONTEXT_INTEGRITY_HARDENING_PLAN.md,
"Open question"):
  * ``summary="missed"`` non-zero  -> BUG REPORT (a trim dropped turns
    without the summary net firing) — investigate before any tuning.
  * Trims rare for a week          -> the 24K budget is fine as-is; the
    S1-1/S1-4 fixes made the losses survivable, so rarity is success.
  * Trims frequent (>1% of turns, when --turns is given) -> consider
    raising ``agent.trim.token_budget`` (Settings/ConfigStore; clamped to
    [4000, window x 0.95]). Frequent WITH the summary net always firing is
    a cost/UX decision, not a correctness emergency.
"""

from __future__ import annotations

import argparse
import re
import sys
import urllib.request

_SAMPLE_RE = re.compile(
    r'^(?P<name>kazma_context_trim[s_]*\w*)'
    r'(?:\{(?P<labels>[^}]*)\})?\s+(?P<value>\d+(?:\.\d+)?)\s*$'
)


def parse_metrics(text: str) -> dict[str, float]:
    """Extract the context-trim samples from Prometheus text format."""
    out: dict[str, float] = {
        "fired": 0.0,
        "missed": 0.0,
        "dropped_messages": 0.0,
    }
    for line in text.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        m = _SAMPLE_RE.match(line.strip())
        if not m:
            continue
        name = m.group("name")
        labels = m.group("labels") or ""
        value = float(m.group("value"))
        if name == "kazma_context_trim_dropped_messages_total":
            out["dropped_messages"] += value
        elif name == "kazma_context_trims_total":
            sm = re.search(r'summary="([^"]+)"', labels)
            kind = sm.group(1) if sm else "unknown"
            if kind in ("fired", "missed"):
                out[kind] += value
    return out


def fetch(url: str) -> str:
    with urllib.request.urlopen(url, timeout=10) as resp:  # noqa: S310
        return resp.read().decode("utf-8", "replace")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--url", default="http://127.0.0.1:9090/metrics")
    ap.add_argument("--stdin", action="store_true",
                    help="read Prometheus text from stdin instead of fetching")
    ap.add_argument("--turns", type=int, default=0,
                    help="approximate user turns in the measurement window "
                         "(denominator for the trim rate)")
    args = ap.parse_args()

    text = sys.stdin.read() if args.stdin else fetch(args.url)
    m = parse_metrics(text)
    total = m["fired"] + m["missed"]

    print("Context-trim measurement (since server start / counter reset)")
    print("-" * 62)
    print(f"  trims that dropped turns      : {int(total)}")
    print(f"    summary net fired           : {int(m['fired'])}")
    print(f"    summary net MISSED          : {int(m['missed'])}")
    if total:
        print(f"  avg messages dropped per trim : {m['dropped_messages'] / total:.1f}")
    print(f"  total messages dropped        : {int(m['dropped_messages'])}")
    print()

    if m["missed"] > 0:
        print("DECISION: BUG REPORT — a trim dropped user/assistant turns")
        print("without the summary net firing. Investigate that before any")
        print("budget tuning (a non-zero 'missed' is never a tuning input).")
        return 1

    if args.turns > 0:
        rate = total / args.turns
        print(f"  trim rate                     : {rate:.2%} of ~{args.turns} turns")
        if rate > 0.01:
            print()
            print("DECISION: trims are frequent (>1% of turns). If the summary")
            print("net always fired (missed=0), this is a cost/UX call — consider")
            print("raising agent.trim.token_budget (clamped to window x 0.95).")
        else:
            print()
            print("DECISION: trims are rare — keep the 24K default budget. The")
            print("S1-1/S1-4 fixes made residual losses survivable; rarity is the")
            print("success state, not a signal to loosen anything.")
    else:
        print("DECISION: counts recorded; pass --turns <N> for a rate-based")
        print("recommendation. Rule of record: missed=0 and rare trims => keep")
        print("the default; frequent trims => consider raising the budget.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
