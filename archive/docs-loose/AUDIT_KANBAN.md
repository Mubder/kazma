# Kazma Re-Audit Kanban — 25 Findings

**Base:** `e5ad3a9` | **Auditors:** Claude Code + GLM 5.2  
**Verdict:** 5 genuinely FIXED. 7 CRITICAL, 10 HIGH, 8 MEDIUM remain.

---

## ✅ DONE (5 — prior sprints, verified by auditors)

| # | Finding | Commit |
|:--|:---|:---|
| PW-01 | PBKDF2-SHA256 + salt + timing-safe | `265ca4b` |
| BIND-01 | `0.0.0.0` → `127.0.0.1` default, gated behind `KAZMA_SECRET` | `3fd758b` |
| SHELL-01 | `create_subprocess_shell` → `create_subprocess_exec` in worker | `1f50243` |
| WS-01 | WS `/ws/chat` + `/ws/dashboard` authenticates + gates danger tools | `3fd758b` |
| TOKEN-01 | API tokens SHA-256 hashed, raw token returned only once | `265ca4b` |

---

## 🔴 TODO — Sprint 1: CRITICAL (7 bugs, ~1 day)

| # | Bug | File:Lines | Root Cause | Fix |
|:--|:---|:---|:---|:---|
| **RACE-ENG-01** | `_task_lock` never acquired | `engine.py:68` | `asyncio.Lock()` declared but never `async with`'d. Source of 5 pre-existing test failures. | Acquire lock around every `_task_history` access. |
| **SEC-TR-02** | SafetyMiddleware never called in tool exec | `tool_registry.py:execute()` | Zero calls to `get_safety().check()` in LocalToolRegistry execute path. Danger tools ungated. | Call `safety.check()` before tool execution. |
| **SEC-TR-01** | Allowlist permits `python`, `curl`, `docker` | `tool_registry.py:645-657` | `_SAFE_BINARIES` includes `python`, `python3`, `pip`, `curl`, `wget`, `docker`, `node`, `sed`, `awk`. Trivial RCE via any interpreter. | Remove interpreters/network/download tools. Keep read-only + build tools. |
| **BUG-VEC-01** | `vec0` requires integer PK | `sqlite_vec.py:130` | `id TEXT PRIMARY KEY` rejected by vec0. Table creation silently fails. | `id INTEGER PRIMARY KEY`. |
| **BUG-VEC-02** | Wrong query API for vec0 | `sqlite_vec.py:175` | Uses `vec_distance_cosine()` manual scalar. vec0 requires `WHERE embedding MATCH ? ORDER BY distance`. | Fix query to vec0 API. |
| **BUG-VEC-03** | `load_extension` unauthorized | `sqlite_vec.py:88` | `load_extension("vec0")` needs `enable_load_extension(True)` first. Swallowed → `available=False`. | Call `enable_load_extension(True)`. |
| **SUB-01** | Sub-agent spawn raises TypeError | `app.py:350`, `agent_runner.py:466` | `get_streaming_graph()` accepts no params, but spawn passes `tool_whitelist=` and `hitl_config=`. | Remove kwargs from the lambda. |

---

## 🟠 TODO — Sprint 2: HIGH (10 bugs, ~1 day)

| # | Bug | File:Lines | Root Cause | Fix |
|:--|:---|:---|:---|:---|
| **AUTH-01a** | Hub submit auth fail-open | `hub/api.py:25-32` | `_require_auth()` returns when `KAZMA_SECRET` unset. Write endpoints open by default. | Deny writes when secret unset. |
| **AUTH-01b** | Download endpoint unauthenticated | `hub/api.py:317-318` | `download_skill` has no `_require_auth` call. | Add `_require_auth` check. |
| **AUTH-01c** | Route gating misses 5 prefixes | `auth.py:44-51` | `/api/agents`, `/api/providers`, `/api/connectors`, `/api/chat/*`, `/api/gateway/*` not in `SENSITIVE_PREFIXES`. | Add missing prefixes. |
| **DISC-01** | Disclosure HMAC key predictable | `disclosure.py:438-444` | `kazma-<hostname>-<uid>` guessable fallback. | Use `secrets.token_hex(32)` when env var unset. |
| **RACE-REG-01** | WorkerRegistry not threadsafe | `registry.py` | Docstring claims "Thread-safe" but no lock. Fresh `WorkerRegistry()` per-call = last-writer-wins. | Singleton + `threading.Lock()`. |
| **SEC-TR-03** | `file_search` unscoped | `tool_registry.py` | `root.rglob()` on any path. Workspace check never applied. | Add workspace scoping. |
| **SEC-TR-04** | `file_read`/`file_write` scoping silently disabled | `tool_registry.py` | `except (ImportError, OSError): pass` on workspace check failure. | Deny-by-default on import failure. |
| **ORCH-01** | `handle_timeout` uncorrelated | `orchestrator.py:328` | Matches ANY pending sub-task, not the specific task that timed out. | Correlate by `request_id`. |
| **GAP-SI-01** | System prompt grows without bound | `self_improvement.py:140` | `+= delta` on every pipeline stage. No cap. | Cap at 5 deltas, compact. |
| **BUG-ORCH-01** | Delegation executor returns `PENDING` | `protocol.py:273-281` | No executor wired into DelegationProtocol. | Wire WorkerRegistry as default executor. |

---

## 🟡 TODO — Sprint 3: MEDIUM (10 bugs, ~0.5 day)

| # | Bug | File:Lines | Root Cause | Fix |
|:--|:---|:---|:---|:---|
| **BUG-FTS-01** | `fts5.py.available` is coroutine | `fts5.py:54` | `async def available` returns truthy coroutine, always True. | Make sync or fix callers. |
| **GAP-TPL-01** | Pipeline routes by `role.value` string mismatch | `topology.py:217` | Workers referenced by `worker_name` not `role.value`. | Route by worker name. |
| **GAP-REG-01** | Semantic router swallows exceptions | `registry.py:189` | `except Exception: pass` — zero observability. | Log warning on failure. |
| **RACE-SR-01** | ChromaDB collection rebuilt every `route()` | `semantic_router.py:243` | Delete-all + re-add every call. Concurrent routes corrupt. | Rebuild only on worker changes. |
| **CKP-01** | `list_checkpoints` broken JSON path | `checkpoint.py:225` | Passes LangGraph binary blob to `json.loads`. | Use LangGraph deserialization. |
| **GAP-ADP-01** | L4 queries only "default" worker | `adapter.py:139` | Per-worker `worker_vectors_<name>` tables never read. | Query all worker tables. |
| **ORCH-02** | `_active_orchestrations` never evicted | `orchestrator.py:73` | Unbounded memory growth. | Evict old entries. |
| **GAP-ADP-02** | `health()` fts5 is object-presence | `adapter.py:53` | `fts5: True` based on object-not-None, not backend init. | Check `await backend.available`. |
| **SAF-01** | `_approval_timeout` dead config | `safety.py:31` | Stored in stats, never passed to `request_approval()`. | Pass timeout to bus. |
| **DOC-01** | README test badge stale | `README.md` | Claims 3,309, actual 3,306. | Fix badge. |

---

## 📊 SUMMARY

| Sprint | Severity | Count | Est. |
|:---|:---|:---:|:---:|
| Sprint 1 | 🔴 CRITICAL | 7 | 1 day |
| Sprint 2 | 🟠 HIGH | 10 | 1 day |
| Sprint 3 | 🟡 MEDIUM | 10 | 0.5 day |
| **Total** | | **27** | **2.5 days** |

---

## 🎯 EXECUTION ORDER

```
Sprint 1 (CRITICAL) ───────► Sprint 2 (HIGH) ───────► Sprint 3 (MEDIUM)
  RACE-ENG-01                  AUTH-01a/b/c               BUG-FTS-01
  SEC-TR-02                    DISC-01                    GAP-TPL-01
  SEC-TR-01                    RACE-REG-01                GAP-REG-01
  BUG-VEC-01/02/03             SEC-TR-03/04               RACE-SR-01
  SUB-01                       ORCH-01                    CKP-01
                               GAP-SI-01                  GAP-ADP-01/02
                               BUG-ORCH-01                SAF-01
```

**Alternative:** SEC-TR-01 + SEC-TR-02 can run in parallel with BUG-VEC-01/02/03 since they touch different files.
