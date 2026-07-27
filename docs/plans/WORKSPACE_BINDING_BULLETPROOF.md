# Workspace Binding + Tool Loop + Memory — COMPLETE

All planned workstreams closed. No intentional open cases remain.

## Invariants (enforced in code)

1. **One resolver:** `kazma_core.workspace.binding.resolve_active_root()`
2. **Binding bus:** `notify_root_changed` on Switch Repo / activate / app boot
3. **MCP rebind:** `workspace_bound` + `${KAZMA_ACTIVE_WORKSPACE}`; process-global only
4. **Breaker:** typed outcomes + per-round hard credit
5. **L3:** single `get_texts` SQL batch
6. **Paths:** all runtime writes go to `user_home()` / `data_dir()`; legacy `~/.kazma` is migration + read fallback only; hub merge when project hub empty

## Modules

| Module | Role |
|--------|------|
| `workspace/binding.py` | SoT + bus |
| `workspace/mcp_rebind.py` | MCP rebind consumer |
| `agent/tool_loop_breaker.py` | Graph + swarm breaker |
| `paths.py` | data_dir, user_home, legacy helpers, merge |

## Folders outside monorepo

| Path | Role |
|------|------|
| `~/kazma-repos` | Clone base (`KAZMA_CLONE_DIR`) — intentional |
| `~/.kazma` | Legacy only — no new writes |

## Operator smoke

1. Restart Kazma.
2. Switch Repo → clone under `~/kazma-repos`.
3. Log: MCP start/rebind with absolute active root.
4. No L3 recursion warning; no circuit trip on MCP path policy alone.
