# GitHub Issues — Bulk Update Script
# Run this AFTER `gh auth login` with your GitHub token.
# This creates issues for completed P0-P2 audit work and closes them immediately.

#!/bin/bash
set -e

REPO="Mubder/kazma"

echo "=== Creating issues for completed P0-P2 audit work ==="

# P0 items
gh issue create --repo "$REPO" --title "P0-1: HITL approval gates on all platforms" --body "✅ Done — Sprint 14. Three-tier approval (graph interrupt + swarm bus + MCP classification). Fail-closed danger-tool gating across Web, Telegram, Discord, Slack. Commits 13df2d5–e78734a." --label "P0,done" && gh issue close $(gh issue list --repo "$REPO" --search "P0-1: HITL" --json number -q '.[0].number')

gh issue create --repo "$REPO" --title "P0-2: Fix 36 failing tests (test isolation)" --body "✅ Done — Sprint 14. Root cause: KAZMA_SECRET env var leak in test_hub_e2e.py (23 failures), handoff cycle detection, workspace singleton pollution. 28→3 failures. Commits 5e0dda8, d81564c, eea2972." --label "P0,done"

gh issue create --repo "$REPO" --title "P1-1: ConfigStore atomicity (WAL + batch transactions)" --body "✅ Done — Sprint 15. WAL journaling, busy_timeout=5000, batch_set() transactions, process-wide singleton, config reconciliation on startup. Commit 2121e2c." --label "P1,done"

gh issue create --repo "$REPO" --title "P1-2: MCP server auth + HITL gate" --body "✅ Done — Sprint 15. Per-server auth (bearer tokens + custom headers), trust levels, MCP tool classification by name pattern, HITL gate in UnifiedToolExecutor. Commit 00d0f2c." --label "P1,done"

gh issue create --repo "$REPO" --title "P1-3: Skill checksums — fail-closed + HMAC signatures" --body "✅ Done — Sprint 16. Checksum verification is fail-closed (no more except:pass). HMAC-SHA256 signatures against KAZMA_SECRET. New 'kazma hub sign' CLI. Commit b13cc57." --label "P1,done"

gh issue create --repo "$REPO" --title "P2-1: Refactor engine.py god class" --body "✅ Done — Sprint 17. 3 modules extracted: ReliabilityRegistry (174 lines), WorkerPhonebook (87 lines), CheckpointManager (199 lines). engine.py: 1,878→1,573 lines. All public API unchanged. Commits ac770b8, 98a4844, b4c76ed." --label "P2,done"

gh issue create --repo "$REPO" --title "P2-2: Circuit breaker UI badges + per-worker start/stop" --body "✅ Done — Sprint 16. Live ⚡ breaker state badges in swarm panel. Per-worker start/stop APIs + UI buttons. Commit 8f0a97e." --label "P2,done"

gh issue create --repo "$REPO" --title "P2-3: Task cancel/retry from UI" --body "✅ Done — Sprint 16. Cancel kills asyncio handle + finalizes as CANCELLED. Retry builds fresh SwarmTask with lineage. API routes + UI buttons. Commit 9a42017." --label "P2,done"

gh issue create --repo "$REPO" --title "P2-4: Unify config source of truth" --body "✅ Done — Sprint 17. ConfigStore.reconcile_from_yaml() seeds DB on startup (non-clobbering). MCP servers merged from both YAML + ConfigStore. Commit e59f6b0." --label "P2,done"

gh issue create --repo "$REPO" --title "P2-5: Update docs + README accuracy" --body "✅ Done — Sprint 16. Test count 3,299→3,409. Slack 'Socket Mode'→'polling-based'. TelegramWorker reference removed. Commit 8f0a97e." --label "P2,done"

echo ""
echo "=== Closing old open issues that are already done ==="

# Close open issues that were completed in earlier sprints
for ISSUE in 3 6 16; do
  echo "Closing #$ISSUE..."
  gh issue close "$ISSUE" --repo "$REPO" --reason completed 2>/dev/null || echo "  (already closed or not found)"
done

echo ""
echo "=== Current open issues ==="
gh issue list --repo "$REPO" --state open
