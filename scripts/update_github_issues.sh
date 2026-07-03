#!/bin/bash
set -e
export PATH="/c/Program Files/GitHub CLI:$PATH"
REPO="Mubder/kazma"

echo "=== Creating issues for completed P0-P2 audit work ==="

create_and_close() {
  local title="$1"
  local body="$2"
  local labels="$3"
  local num
  num=$(gh issue create --repo "$REPO" --title "$title" --body "$body" --label "$labels" 2>&1 | grep -oP 'https://github.com/[^/]+/[^/]+/issues/\K\d+')
  if [ -n "$num" ]; then
    gh issue close "$num" --repo "$REPO" --reason completed
    echo "  Created & closed #$num: $title"
  fi
}

# P0 items
create_and_close "P0-1: HITL approval gates on all platforms" \
  "Done — Sprint 14. Three-tier approval (graph interrupt + swarm bus + MCP classification). Fail-closed danger-tool gating across Web, Telegram, Discord, Slack. Commits 13df2d5–e78734a." \
  "P0,done"

create_and_close "P0-2: Fix 36 failing tests (test isolation)" \
  "Done — Sprint 14. Root cause: KAZMA_SECRET env var leak in test_hub_e2e.py (23 failures), handoff cycle detection, workspace singleton pollution. 28→3 failures. Commits 5e0dda8, d81564c, eea2972." \
  "P0,done"

# P1 items
create_and_close "P1-1: ConfigStore atomicity (WAL + batch transactions)" \
  "Done — Sprint 15. WAL journaling, busy_timeout=5000, batch_set() transactions, process-wide singleton, config reconciliation on startup. Commit 2121e2c." \
  "P1,done"

create_and_close "P1-2: MCP server auth + HITL gate" \
  "Done — Sprint 15. Per-server auth (bearer tokens + custom headers), trust levels, MCP tool classification by name pattern, HITL gate in UnifiedToolExecutor. Commit 00d0f2c." \
  "P1,done"

create_and_close "P1-3: Skill checksums — fail-closed + HMAC signatures" \
  "Done — Sprint 16. Checksum verification is fail-closed (no more except:pass). HMAC-SHA256 signatures against KAZMA_SECRET. New kazma hub sign CLI. Commit b13cc57." \
  "P1,done"

# P2 items
create_and_close "P2-1: Refactor engine.py god class" \
  "Done — Sprint 17. 3 modules extracted: ReliabilityRegistry (174 lines), WorkerPhonebook (87 lines), CheckpointManager (199 lines). engine.py: 1,878→1,573 lines. All public API unchanged. Commits ac770b8, 98a4844, b4c76ed." \
  "P2,done"

create_and_close "P2-2: Circuit breaker badges + per-worker start/stop" \
  "Done — Sprint 16. Live breaker state badges in swarm panel. Per-worker start/stop APIs + UI buttons. Commit 8f0a97e." \
  "P2,done"

create_and_close "P2-3: Task cancel/retry from UI" \
  "Done — Sprint 16. Cancel kills asyncio handle + finalizes as CANCELLED. Retry builds fresh SwarmTask with lineage. API routes + UI buttons. Commit 9a42017." \
  "P2,done"

create_and_close "P2-4: Unify config source of truth" \
  "Done — Sprint 17. ConfigStore.reconcile_from_yaml() seeds DB on startup (non-clobbering). MCP servers merged from YAML + ConfigStore. Commit e59f6b0." \
  "P2,done"

create_and_close "P2-5: Update docs + README accuracy" \
  "Done — Sprint 16. Test count 3299→3409. Slack Socket Mode description corrected. TelegramWorker reference removed. Commit 8f0a97e." \
  "P2,done"

echo ""
echo "=== Closing old open issues that are already done ==="
for ISSUE in 3 6 16; do
  gh issue close "$ISSUE" --repo "$REPO" --reason completed 2>&1 || true
done

echo ""
echo "=== Current open issues ==="
gh issue list --repo "$REPO" --state open
