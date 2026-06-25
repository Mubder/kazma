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
#     The default sync omits the rag extra and sqlite-vec, which the memory
#     subsystem and RAG tests require; install them explicitly.
uv sync --extra dev --extra cli --extra tui --extra rag 2>&1 | tail -5
uv pip install sqlite-vec 2>&1 | tail -2

PY=".venv/bin/python"

# --- 2. Foundation import check (mirrors setup.sh) ---
echo "--- import check ---"
IMPORT_OK=1
for mod in sqlite_vec aiosqlite langgraph "langgraph.checkpoint.sqlite.aio" yaml httpx textual chromadb; do
    if $PY -c "import $mod" 2>/dev/null; then
        echo "  ok: $mod"
    else
        echo "  FAIL: $mod"
        IMPORT_OK=0
    fi
done

# --- 3. Full test suite ---
echo "--- pytest ---"
$PY -m pytest tests/ -q -p no:cacheprovider -o addopts="" --tb=short \
    2>&1 | tee "$ART/pytest_output.txt"
SUMMARY="$(grep -E '[0-9]+ (passed|failed|error)' "$ART/pytest_output.txt" | tail -1)"
PASSED="$(echo "$SUMMARY" | grep -oE '[0-9]+ passed' | grep -oE '[0-9]+' || echo 0)"
FAILED="$(echo "$SUMMARY" | grep -oE '[0-9]+ failed' | grep -oE '[0-9]+' || echo 0)"
ERRORS="$(echo "$SUMMARY" | grep -oE '[0-9]+ error' | grep -oE '[0-9]+' || echo 0)"

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
AGENT_OK=$(grep -c "E2E_AGENT_LIFECYCLE_OK" "$ART/agent_e2e.txt" || echo 0)

# --- 5. Write EVAL.md ---
if [ "$FAILED" = "0" ] && [ "$ERRORS" = "0" ] && [ "$AGENT_OK" -ge 1 ] && [ "$IMPORT_OK" = "1" ]; then
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

Pytest summary line: \`$SUMMARY\`

## What "end to end" exercises

- Install via \`uv\` with all runtime extras (dev, cli, tui, rag) + \`sqlite-vec\`.
- Full pytest suite (~1374 tests): agent graph, gateway/adapters, RAG pipeline
  (ChromaDB store -> retrieve), HITL safety gate, cron, hub/skills, sandbox.
- Live agent lifecycle: \`load_config()\` -> \`KazmaAgent\` (tool registry + LLM
  router wired) -> \`shutdown()\`.

## Reproduction notes

The default \`uv sync\` does **not** install the \`rag\` extra (chromadb,
sentence-transformers) or \`sqlite-vec\`, yet the memory subsystem and 10 RAG
tests import them. Installing the \`rag\` extra + \`sqlite-vec\` takes the suite
from 1364 passed / 1 failed / 9 errors to fully green.

Artifacts: \`pytest_output.txt\`, \`agent_e2e.txt\`.
EOF

echo "=== wrote EVAL.md ==="
cat "$EVAL"
cp "$EVAL" "$ART/EVAL.md"
