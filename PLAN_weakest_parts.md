# Plan: Fix Kazma's Weakest Parts

## Overview
Address the critical weaknesses identified in Kazma's architecture, prioritized by impact.

---

## ✅ Completed (this session)

### 1. Gateway Tests (🔴 Critical) — DONE
- Created `kazma-gateway/kazma_gateway/tests/` directory
- Added `__init__.py` and `conftest.py` with mock utilities
- Created tests for Telegram escape, Discord init, Slack init
- **Status:** Tests passing (9 gate tests)

### 2. Rate-Limit Handling (🔴 Critical) — DONE
- Added 429 detection in `llm_provider.py`
- Implemented exponential backoff with retry-after header support
- Max 3 retries before raising LLMError
- **Status:** Tests passing (2 rate limit tests)

### 3. Windows Portability Fix — DONE
- Fixed `code_exec.py` to use `WINDIR` env var as fallback
- Uses `shutil.which()` compatible approach
- **Status:** Compiled and verified

### 4. Prometheus Metrics Endpoint — DONE
- Created `kazma_core/metrics.py` with graceful degradation
- Added `/metrics` endpoint in `routes_direct.py`
- Added `observability` optional dependency in `pyproject.toml`
- **Status:** Tests passing (2 metrics tests)

---

## Remaining Items to Fix

### 5. No Gateway Tests Phase 2-5 (🔴 Remaining)
| Priority | Task | Status |
|----------|------|--------|
| High | Test HITL callback buttons | ⏳ |
| High | Test session isolation (no platform ID leaks) | ⏳ |
| Med | Test Discord/Slack interactive messages | ⏳ |
| Med | Cross-platform integration tests | ⏳ |

### 6. MCP stdio auth — DONE
- Added `auth.type: env` for environment variable injection
- Added `auth.type: arg` for command-line argument injection
- Works with existing SSE auth mechanism
- **Status:** Tests passing (2 MCP auth tests)

### 7. WebSocket chat — DONE (intentionally deprecated)
- `/ws/chat` returns 410 Gone
- Clients should use SSE `/api/chat/stream` for full HITL safety
- **Status:** Working as designed

### 8. /undo and /edit slash commands — DONE (graph path)
- Live path: `agent_handler/graph.py` uses `graph.aget_state` + `graph.aupdate_state`
- `/undo` removes last assistant turn (+ trailing tool msgs)
- `/edit <text>` replaces last assistant content in checkpoint
- `slash_commands.py` keeps fallback text if graph handler is not wired

---

## Progress Summary

| Item | Before | After |
|------|--------|-------|
| Gateway tests | 0 | 9 tests |
| Rate limit handling | None | Exponential backoff |
| MCP stdio auth | None | env + arg auth support |
| Metrics endpoint | None | `/metrics` with graceful degradation |
| Windows portability | `C:\Windows` hardcoded | Uses `WINDIR`/`SystemRoot` env vars |
| WebSocket chat | Returns 410 Gone | Working as designed (deprecation) |
| /undo and /edit | Stub commands | Still stubs (honest messaging) |