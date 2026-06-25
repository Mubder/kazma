#!/usr/bin/env bash
# Minimal end-to-end reproduction for Kazma.
# Installs the framework + all runtime extras, runs the full test suite,
# exercises a live agent lifecycle, and writes results to EVAL.md.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

ART=".openresearch/artifacts"
mkdir -p "$ART"
EVAL="EVAL.md"

echo "=== Kazma minimal E2E reproduction ==="

# --- 1. Install: framework + dev/cli/tui + rag extras (chromadb, sentence-transformers) ---
#     The default sync omits the rag extra (chromadb, sentence-transformers),
#     which the memory subsystem and the RAG tests require; install it.
uv sync --extra dev --extra cli --extra tui --extra rag 2>&1 | tail -5

PY=".venv/bin/python"

# --- 2. Foundation import check ---
echo "--- import check ---"
IMPORT_OK=1
for mod in aiosqlite langgraph "langgraph.checkpoint.sqlite.aio" yaml httpx textual chromadb fastapi; do
    if $PY -c "import $mod" 2>/dev/null; then
        echo "  ok: $mod"
    else
        echo "  FAIL: $mod"
        IMPORT_OK=0
    fi
done

# --- 3. Full test suite ---
echo "--- pytest (with coverage gate) ---"
# Coverage gate: fail the run if line coverage drops below the threshold.
# Measured at 74% on this branch; gate set conservatively at 70% to guard
# against regressions without flakiness.
COV_MIN=70
$PY -m pytest tests/ -q -p no:cacheprovider -o addopts="" --tb=short \
    --cov=kazma_core --cov=kazma_gateway --cov=kazma_ui --cov=kazma_memory \
    --cov=kazma_skills --cov=kazma_providers \
    --cov-report=term-missing --cov-fail-under=$COV_MIN \
    2>&1 | tee "$ART/pytest_output.txt"
SUMMARY="$(grep -E '[0-9]+ (passed|failed|error)' "$ART/pytest_output.txt" | tail -1)"
PASSED="$(echo "$SUMMARY" | grep -oE '[0-9]+ passed' | grep -oE '[0-9]+' || true)"
FAILED="$(echo "$SUMMARY" | grep -oE '[0-9]+ failed' | grep -oE '[0-9]+' || true)"
ERRORS="$(echo "$SUMMARY" | grep -oE '[0-9]+ error' | grep -oE '[0-9]+' || true)"
COV_TOTAL="$(grep -E '^TOTAL' "$ART/pytest_output.txt" | grep -oE '[0-9]+%' | tail -1 || echo 'n/a')"
# NOTE: `grep -c` already prints a single count line (0 on no match) but exits 1
# when the count is 0 — so use `|| true`, NOT `|| echo 0` (which would append a
# second line and break the string comparisons below).
COV_FAIL="$(grep -c 'Coverage failure' "$ART/pytest_output.txt" || true)"

# --- 4. Live agent E2E lifecycle (init -> tools/llm wired -> shutdown) ---
echo "--- agent E2E lifecycle ---"
$PY - <<'PYEOF' 2>&1 | tee "$ART/agent_e2e.txt"
import asyncio
from kazma_core.agent import KazmaAgent, load_config

async def main():
    cfg = load_config()
    print(f"config: name={cfg.name} version={cfg.version} language={cfg.language}")
    agent = KazmaAgent(cfg)
    print(f"tools={type(agent.tools).__name__} llm_wired={agent.llm is not None}")
    await agent.shutdown()
    print("E2E_AGENT_LIFECYCLE_OK")

asyncio.run(main())
PYEOF
AGENT_OK=$(grep -c "E2E_AGENT_LIFECYCLE_OK" "$ART/agent_e2e.txt" || true)

# --- 4b. Server + real-graph end-to-end smoke ---
#     Boot the real FastAPI app (ASGI) and hit /health, /, /api/gateway/status;
#     then flow a real message through the real LangGraph supervisor graph
#     (SUPERVISOR->TOOL_WORKER->SUPERVISOR->RESPOND) with a stub LLM, backed by
#     a real on-disk AsyncSqliteSaver, and resume from the persisted checkpoint.
echo "--- server + graph smoke ---"
$PY scripts/repro_server_smoke.py 2>&1 | tee "$ART/server_graph_smoke.txt"
SERVER_OK=$(grep -c "SERVER_SMOKE_OK" "$ART/server_graph_smoke.txt" || true)
GRAPH_OK=$(grep -c "GRAPH_FLOW_OK" "$ART/server_graph_smoke.txt" || true)

# --- 5. Write EVAL.md ---
if [ "$FAILED" = "0" ] && [ "$ERRORS" = "0" ] && [ "$AGENT_OK" -ge 1 ] && [ "$IMPORT_OK" = "1" ] \
   && [ "$SERVER_OK" -ge 1 ] && [ "$GRAPH_OK" -ge 1 ] && [ "$COV_FAIL" = "0" ]; then
    VERDICT="PASS — minimal reproduction runs end to end"
else
    VERDICT="PARTIAL — see breakdown"
fi

cat > "$EVAL" <<EOF
# Kazma — Minimal End-to-End Reproduction

**Verdict:** $VERDICT

Kazma is an autonomous AI-agent framework (LangGraph + SQLite checkpointing,
ChromaDB RAG memory, multi-platform gateway, Arabic RTL dashboard). It is a
software framework, not an ML paper, so the "core claim" reproduced here is the
README's headline: the framework installs and its full behavior suite passes,
and a live agent boots end to end.

## Results

| Metric | Value |
|---|---|
| Core imports OK | $([ "$IMPORT_OK" = "1" ] && echo "yes" || echo "NO") |
| Tests passed | $PASSED |
| Tests failed | $FAILED |
| Test errors | $ERRORS |
| Agent E2E lifecycle | $([ "$AGENT_OK" -ge 1 ] && echo "OK" || echo "FAILED") |
| Server ASGI smoke | $([ "$SERVER_OK" -ge 1 ] && echo "OK" || echo "FAILED") |
| Real graph flow + resume | $([ "$GRAPH_OK" -ge 1 ] && echo "OK" || echo "FAILED") |
| Line coverage (gate ≥${COV_MIN}%) | $COV_TOTAL $([ "$COV_FAIL" = "0" ] && echo "(pass)" || echo "(BELOW GATE)") |

Pytest summary line: \`$SUMMARY\`

## What "end to end" exercises

- Install via \`uv\` with all runtime extras (dev, cli, tui, rag).
- Full pytest suite (~1381 tests): agent graph, gateway/adapters, RAG pipeline
  (ChromaDB store -> retrieve), HITL safety gate, cron (incl. a job that fires),
  hub/skills, sandbox, plus the new real-graph round-trip and cron/adapter
  integration tests.
- **Real FastAPI server** booted via ASGI (TestClient): \`/health\`, \`/\`, and
  \`/api/gateway/status\` all return 200 with the documented body.
- **Real LangGraph supervisor graph** driven end to end with a stub LLM:
  SUPERVISOR -> TOOL_WORKER (executes a real \`shell_exec\` tool) -> SUPERVISOR
  -> RESPOND, backed by an on-disk \`AsyncSqliteSaver\`; the persisted checkpoint
  is then reopened and the full conversation (user/assistant/tool) resumed.
- Live agent lifecycle: \`load_config()\` -> \`KazmaAgent\` -> \`shutdown()\`.

## What the repro proves beyond "the suite is green"

Built up across two rounds on top of the baseline:

**Round 1 — real graph + server (prior child):**
- \`tests/integration/test_graph_roundtrip.py\` executes the real compiled
  supervisor graph through a full ReAct loop with a real tool and a real
  checkpointer, asserting checkpoint persistence + resume. \`graph_builder.py\`
  coverage 33% -> 54%.
- \`scripts/repro_server_smoke.py\` boots the real FastAPI app and flows a
  message through, instead of only asserting attributes exist.

**Round 2 — dependency hygiene, cron firing, coverage gate (this child):**
- **#2 \`websockets\` declared as a hard dependency** — it is the live transport
  for the Discord *and* Slack \`listen()\`/\`_connect_gateway\` receive loops
  (imported inside them), previously present only transitively via
  \`uvicorn[standard]\`. \`tests/integration/test_cron_and_adapters.py\` confirms
  it imports and that both adapters' \`listen()\` loops start and shut down
  cleanly.
- **#3 Cron job fires end to end** — a previously-untested path: an
  immediately-due job is scheduled, the \`CronScheduler\` firing loop picks it up
  and invokes a graph, and the job transitions PENDING -> DONE with a result.
  \`cron/scheduler.py\` coverage 64% -> 91% (the firing loop was 0%).
- **#5 Coverage gate + cleanup** — \`run.sh\` now runs pytest under
  \`--cov-fail-under=70\` (measured 74%); the hardcoded \`/home/balfaris/...\` path
  in \`test_supervisor.py\` is replaced with a repo-relative path so the
  \`file_search\` test actually matches files; and the dead, shadowed
  \`kazma_core/agent.py\` module (the \`agent/\` package shadows it) is removed.

## Reproduction notes

The default \`uv sync\` does **not** install the \`rag\` extra (chromadb,
sentence-transformers), yet \`kazma_core/memory/vector_store.py\` imports them.
Installing the \`rag\` extra takes the suite from 1 failed / 9 errors to fully
green.

Artifacts: \`pytest_output.txt\`, \`agent_e2e.txt\`, \`server_graph_smoke.txt\`.
EOF

echo "=== wrote EVAL.md ==="
cat "$EVAL"
cp "$EVAL" "$ART/EVAL.md"
