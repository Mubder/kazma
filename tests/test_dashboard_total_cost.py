"""The Dashboard's Total cost card never says $0.0000 over calls of unknown price.

Live 2026-11-15: every chat call went to deepseek-flash, whose price Kazma does
not know; "Spent today" said "—" and "27 calls with no known price", and the
Total cost card beside it said $0.0000 -- the one card AGENTS.md §60 says
never does. The card reads the in-memory trace store, which counted every
call's cost as known. The store now counts the calls of unknown price, and the
card says "—" (none known) or "≥ $x" (some known), on the server
(``dashboard._cost_text``) and in the page (``dashboard.js`` ``costText``),
held to the same cases.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
CASES = [
    (0.0, 0, "$0.0000"),
    (0.0, 27, "—"),
    (0.0123, 3, "≥ $0.0123"),
    (1.5, 0, "$1.5000"),
]


def test_the_trace_store_counts_calls_of_unknown_price():
    from kazma_core.tracing import TraceEntry, TraceStore

    store = TraceStore()
    store.add(TraceEntry(timestamp=0, trace_type="llm", label="m", status="success", duration_ms=1, cost=0.0,
                         priced=False))
    store.add(TraceEntry(timestamp=0, trace_type="llm", label="m", status="success", duration_ms=1, cost=0.01))
    stats = store.stats()
    assert stats["total_llm_calls"] == 2 and stats["unpriced_llm_calls"] == 1


def test_a_traced_call_carries_whether_its_price_is_known(monkeypatch):
    from kazma_core import tracing

    store = tracing.TraceStore()
    monkeypatch.setattr(tracing, "_trace_store", store)
    tracing.KazmaTracer().trace_llm_call(model="m", prompt="p", response="r", tokens=1, cost=0.0, priced=False)
    assert store.stats()["unpriced_llm_calls"] == 1


@pytest.mark.parametrize(("cost", "unpriced", "shown"), CASES)
def test_the_server_says_what_it_knows(cost, unpriced, shown):
    from kazma_ui.dashboard import _cost_text as cost_text

    assert cost_text(cost, unpriced) == shown


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_page_says_the_same():
    src = (REPO / "kazma-ui" / "kazma_ui" / "static" / "js" / "dashboard.js").read_text(encoding="utf-8")
    start = src.index("  function costText(cost, unpriced) {")
    body = src[start:src.index("\n  }\n", start) + 4]
    script = body + "\nconsole.log(JSON.stringify(" + json.dumps([[c, u] for c, u, _ in CASES]) + \
        ".map(([c, u]) => costText(c, u))));\n"
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, encoding="utf-8", check=True)
    assert json.loads(out.stdout) == [shown for _c, _u, shown in CASES]


def test_the_supervisor_passes_the_price_flag():
    src = (REPO / "kazma-core" / "kazma_core" / "agent" / "graph_supervisor.py").read_text(encoding="utf-8")
    call = src[src.index("tracer.trace_llm_call("):]
    assert "priced=bool(getattr(response, \"cost_known\", True))" in call[:400]
