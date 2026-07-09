# FULL REMEDIATION & FIXING PLAN

**Last Updated:** 2026-07-09 (HEAD post–worker_dispatch extract)  
**Sources:** AUDIT_FRESH_2026-07-09, code extracts S0–S5  

---

## Executive summary (current)

| Area | Status |
|------|--------|
| HITL 3 mechanisms / MCP classify / fail-closed bus / workspace scope | ✅ PASS |
| MCP IDE danger map + secret fail-closed | ✅ Done |
| Web approve ownership (require session_id when owner known) | ✅ Done |
| Auth prefixes (chaos/config/git/github/workspaces/…) | ✅ Done |
| Chaos `KAZMA_CHAOS_ENABLED` kill-switch | ✅ Done |
| Disclosure key `token_hex` + ConfigStore | ✅ Done |
| Root `tests/` + TUI in CI; package confests renamed | ✅ Done |
| GeminiProvider `close()` | ✅ Done |
| `shell_exec` alias on swarm ToolRegistry | ✅ Done |
| Secret resolution unified via `config_store.get_kazma_secret` | ✅ Done |
| SwarmService public-API only (no private fallbacks) | ✅ Done |
| FastAPI lifespan (no `on_event`) | ✅ Done |
| Large-module splits | 🟡 engine ~1k (dispatch_inner extracted); more optional |
| Broad `except Exception` sweep | 🟡 Hot paths logged (telegram typing, UI to_dict, SSE getter, …) |
| Chaos wired into LLM/engine hot paths | ❌ Optional product work |
| Full UnifiedToolSurface facade | ❌ Optional |

**Residual risk:** Low–moderate localhost single-op; elevated multi-user without reverse-proxy identity (shared-secret model by design).

---

## Module size progress

| Module | Approx LOC | Extracted pieces |
|--------|----------:|------------------|
| `swarm/engine.py` | ~**1020** | + **dispatch_inner**, worker_dispatch, lifecycle, SSE, factory, handoff guards |
| `adapters/telegram.py` | ~950–1000 | keyboards, stt, send, parse, **callbacks** |
| `settings_manager.py` | ~830 | mcp + providers services |
| `model_registry.py` | ~770 | model_registry_store |

### Still large / optional further splits

1. **`engine._dispatch_inner`** — pipeline/fanout/broadcast routing body  
2. **`telegram` listen loop + voice download + send retry** — remaining adapter density  
3. **model_registry get_client / discovery** — client cache path  
4. **settings_manager** appearance/agent profiles surface  

---

## Completed sprint tracker

| Sprint | Status |
|--------|--------|
| S0 Security | ✅ |
| S1 Correctness | ✅ |
| S2 CI / badges / TUI tests | ✅ |
| S3 Architecture extracts | ✅ Core extracts landed |
| S4 Coverage waves | ✅ Multiple unit files added |
| S5 Debt | 🟡 Large files reduced; global except-sweep / chaos-wiring optional |

---

## Optional backlog (not blocking)

| ID | Item |
|----|------|
| OPT-1 | Wire `@chaos_injection` into LLM/gateway under env flag only |
| OPT-2 | `UnifiedToolSurface` facade over both tool registries |
| OPT-3 | Global silent-`except` audit with logging |
| OPT-4 | Archive obsolete `AUDIT_KANBAN` / duplicate audit MD files |
| OPT-5 | Windows Job Object tests for `code_exec` |
| OPT-6 | Further peel `_dispatch_inner` into pattern-specific modules |

---

## Verification

```powershell
uv run python scripts/check_docs_sync.py
uv run --extra dev pytest tests/test_swarm_handoff.py tests/test_swarm_reliability.py tests/test_auth_middleware.py tests/test_service_facade.py -q
```

**Canonical docs:** this file + `AUDIT_FRESH_2026-07-09.md` (historical findings).
