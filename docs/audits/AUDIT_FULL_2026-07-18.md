# Kazma Full Audit — 2026-07-18

**Version:** 0.5.0  
**Scope:** Architecture, security, technical debt, tests, ops, docs  
**Method:** Code inspection + three parallel deep-dives (security / architecture / tests-ops) + live verification of critical claims  
**Status of prior work:** Builds on Jul 13 remediation (36 items) and v0.4–v0.5 security/IDE work  

---

## Executive scorecard

| Dimension | Grade | Notes |
|-----------|:-----:|-------|
| Core architecture (brain / mouths / IDE) | **A−** | Clear monorepo; IDE + swarm refactors landed well |
| HITL design (3 gates) | **B+** | Graph + bus fail-closed + pipeline solid; **MCP name path fails open** |
| Auth model | **C** | Fine for localhost; **cookie auto-issue = open admin if network-exposed** |
| Test corpus | **A−** | ~3.6k+ root tests; IDE suite orphaned from CI |
| Documentation truth | **C+** | docs-v2 is honest; dual trees + stale roadmap rows |
| Ops / deploy consistency | **C** | Port 8000 vs 9090 drift; soft Bandit/coverage gates |
| Dead / dual systems | **C** | `delegation/` unwired; dual registries/memory entry points |
| Product completeness | **B** | IDE + swarm + i18n strong; `/undo`/`/edit` still stubs |

**Bottom line:** Kazma is a serious multi-platform agent framework with real safety architecture — not a toy. Localhost single-operator use is reasonable with HITL on. **Do not expose the web UI on a public interface** until cookie auth (C2) and MCP HITL (C1) are fixed.

---

## Size snapshot

| Area | Approx. |
|------|--------:|
| kazma-core | ~39k LOC / 175 py |
| kazma-ui | ~23k LOC / 71 files |
| kazma-gateway | ~11k LOC |
| kazma-tui | ~6k LOC |
| tests | ~43k LOC / ~3,649 collected (root) |
| Largest modules | `i18n.py`, `swarm/engine.py`, `app.py`, `commands.py`, `telegram.py`, `tool_registry.py` |

---

## What's strong (keep protecting)

1. **Platform isolation** — LangGraph state never holds `chat_id` / `user_id`; SessionStore restores targets on reply.
2. **Three HITL mechanisms designed** — Graph `interrupt()`, swarm bus fail-closed on NullBus, pipeline checkpoints. Streaming + app recompile both pass `hitl_config`.
3. **IDE as single source of truth** — Mutations go through `LocalToolRegistry` + HITL; workspace_scope + env_context closed the “blind worker” gap.
4. **Shell injection fixed** — `shlex.split` + `create_subprocess_exec` (not shell=True).
5. **Provider/model coupling** — `set_active_model` / `get_client` auto-correct provider mismatches.
6. **LLM resilience** — 429 backoff + NVIDIA 404 tool-definition fallback.
7. **ConfigStore singleton + WAL** — Multi-key atomic writes; YAML only seeds missing keys.
8. **Honest unwired inventory** — `docs/audits/UNWIRED_INVENTORY.md` tracks library-only code intentionally.
9. **Large regression suite** — Swarm, HITL, auth, model registry heavily tested; 207 security-adjacent tests re-run green in this audit (`test_hitl*`, `test_auth_middleware`).
10. **Packages tab** — Completed under Settings (sidebar removed; `/packages` redirects).

---

## CRITICAL findings

### C1 — MCP danger tools bypass HITL (fail-open) ✅ LIVE-VERIFIED

**Where:** `mcp/manager.py` classifies MCP tools (`write_file` → danger) then calls `safety.check(tool_name=…)`  
**Bug:** `SafetyMiddleware.is_danger_tool()` only knows the static `_EXTENDED_DANGER` set (`file_write`, `shell_exec`, …). MCP names like `write_file` / `run_command` are **not** in that set → `check()` returns **True immediately**.

**Live proof (2026-07-18):**
```
write_file   tier=danger  check_sync_allows=True   ← FAIL OPEN
run_command  tier=danger  check_sync_allows=True   ← FAIL OPEN
file_write   tier=danger  check_sync_allows=False  ← correct
shell_exec   tier=danger  check_sync_allows=False  ← correct
```

**Impact:** Untrusted MCP servers can run write/exec tools without approval.  
**Tests hide it:** `test_mcp_hitl.py` mocks `safety.check` instead of using real `SafetyMiddleware`.

**Fix:** When tier is `danger`/`unknown`, force approval (e.g. `add_danger_tool` first, or `check_classified(tool, tier)` that ignores the static allowlist). Add integration test with real SafetyMiddleware + NullBus + MCP name.

---

### C2 — Auth cookie auto-issued on open routes

**Where:** `kazma-ui/auth.py` ~246–255  
Any `GET /` (always open) receives `Set-Cookie: kazma-secret=<correct secret>`. Subsequent `/api/settings`, `/api/ide`, `/api/approve` succeed via cookie.

**Impact:** Anyone who can reach the HTTP port is fully authenticated.  
**Safe only if:** bound to `127.0.0.1` and not reverse-proxied without real login.  
**Dangerous if:** Docker/`0.0.0.0`/Fly public without edge auth.

**Fix:** Never set the secret cookie on unauthenticated open routes; require explicit login / paste-secret once; fail-startup if non-loopback bind without real auth.

---

## HIGH findings

| ID | Finding | Path / note |
|----|---------|-------------|
| H1 | `shell_exec` allowlist includes `python`/`node`/`bash`/`sh` — HITL is single rubber-stamp to host RCE | `agent/tool_registry.py` vs stricter swarm `ShellTool` |
| H2 | `python_exec`/`code_exec` sandbox incomplete (no network/FS jail beyond temp cwd) | `tools/code_exec.py` |
| H3 | Settings HTML can embed raw API keys; `/settings` page not in `SENSITIVE_PREFIXES` | `settings.py` |
| H4 | `config_read` tool returns secrets unmasked; not danger-tier | `tool_registry.py` |
| H5 | Tenant JWT extracted without signature verification | `auth.py` `extract_tenant_from_jwt` |
| H6 | Cross-thread HITL: if `target_ctx` missing, authz block skipped | `agent_handler/hitl.py` |

---

## MEDIUM findings

| ID | Finding |
|----|---------|
| M1 | Dual danger lists (YAML graph vs `_EXTENDED_DANGER`) can drift |
| M2 | Non-streaming graph path may use raw YAML HITL, not `get_hitl_config()` |
| M3 | Workspace scope allows system temp dirs (weak boundary on Windows) |
| M4 | `trust: trusted` MCP fully skips HITL (documented footgun) |
| M5 | Provider discovery allows private URLs (Ollama intent; metadata SSRF risk if attacker sets base_url) |
| M6 | API keys often plaintext in `settings.db`; vault optional |
| M7 | Port matrix: CLI/AGENTS **9090** vs Docker/yaml **8000** vs loadtest **8090** |
| M8 | `ConfigStore()` still constructed in `app.py` / TUI instead of always `get_config_store()` |
| M9 | IDE tests under `kazma-core/tests/` **not in pytest testpaths / CI** |
| M10 | Bandit CI soft-fail (`\|\| true`); coverage has no fail_under |

---

## LOW / product incompleteness

| Item | Status |
|------|--------|
| `/undo`, `/edit` slash | **Still stubs** — PLAN_weakest_parts wrongly marks DONE |
| Soft-nav SPA | Intentionally disabled (`SOFT_NAV_ENABLED = false`) |
| Packages in-app install | Copy-paste commands only (OK) |
| Hub skill install/update | Stub |
| Local STT | Not implemented |
| TUI full agent chat | Not wired to SSE |
| ModelRouter | Built, not passed from agent_runner default path |
| `delegation/` package | Fully tested, **zero production importers** |
| Security scanners / permissions YAML | Library-only (see UNWIRED_INVENTORY) |
| Playwright E2E | Optional; not CI |
| `SECURITY.md` | Still says “0.1.x supported” while product is 0.5.0 |
| Root `architecture.md` | **Missing** — AGENTS.md references it; real doc is `docs-v2/docs/architecture.md` |

---

## Architecture & debt

### Dual systems (product decision needed)

| Pair | Recommendation |
|------|----------------|
| `delegation/*` vs SwarmEngine | Wire one multi-agent model **or** archive delegation |
| Agent `LocalToolRegistry` vs swarm `ToolRegistry` wrapper | Finish deprecation narrative |
| `memory/*` vs `swarm/memory/*` vs `kazma-memory` | Keep unifying; document single entry for chat |
| `docs/` Docusaurus vs `docs-v2/` | Merge docs-v2 into site; mark old tree legacy |
| Multiple “routers” (dialect / model / unified) | Rename for clarity; wire or drop ModelRouter default |

### Unwired-but-kept (intentional)

See `docs/audits/UNWIRED_INVENTORY.md` — certification, linter, dependency_scanner, disclosure, majlis shell, permissions YAML, swarm_notify, etc. Do not delete without product call.

---

## Tests & CI

| Strength | Gap |
|----------|-----|
| Root `tests/` ~3.6k tests in CI (`test-root-suite`) | `kazma-core/tests/` IDE suite **excluded** |
| Swarm + HITL + auth deep coverage | UI package tests are smoke-only |
| Package jobs parallelized | RAG tests skip without `[rag]` in CI |
| Docs sync script in core job | Playwright not gated; Bandit never fails |

**Quick CI wins:**
1. Add `kazma-core/tests` to `testpaths` + `test-core` job  
2. Fail Bandit on High severity  
3. Fix `tests/test_e2e.py` version assert (`0.2.0` → `0.5.0`)  
4. Align port defaults in one source of truth  

---

## Ops / deploy

| Surface | Port | Notes |
|---------|------|-------|
| CLI `kazma serve` / AGENTS.md | 9090 | Windows may block 9090 (svchost) — observed 2026-07-18 |
| Docker / compose / Fly / kazma.yaml ui | 8000 | Container path |
| Load tests CI | 8090 | Third value |

`.env.example` incomplete vs code (`KAZMA_PORT`, workspace, provider env vars).  
`DEEPSEEK_API_KEY` / `ANTHROPIC_API_KEY` in example but **not read by core** (ConfigStore provider list is real path).  
`models.router: litellm` is a **string only** — no LiteLLM dependency.

---

## Priority remediation plan

### P0 — Do before any network-exposed deploy

1. **Fix MCP HITL fail-open (C1)** + real SafetyMiddleware test  
2. **Stop auto-cookie on open routes (C2)**; require explicit auth  
3. **Mask `config_read` secrets (H4)** + never raw keys in settings HTML (H3)  
4. **Fail-closed cross-thread HITL when target_ctx missing (H6)**

### P1 — Correctness / maintainability

5. Align shell allowlist with strict `ShellTool` (H1) or document intentional RCE-after-HITL  
6. Wire IDE tests into CI (`kazma-core/tests`)  
7. Fix `/undo`/`/edit`: implement **or** demote PLAN/docs/help  
8. Unify port (9090 vs 8000) + `.env.example`  
9. Doc truth: AGENTS architecture path; refresh docs-v2 roadmap rows that lie  
10. Product decision memo: keep or archive `delegation/`

### P2 — Hardening

11. JWT tenant: verify signature or remove  
12. Single danger-tool schema + parity test (graph YAML vs bus)  
13. Soft-nav: delete or finish; don't leave half-SPA  
14. Optional: RAG in CI nightly with `[rag]`  
15. SECURITY.md version table → 0.5.x  

### P3 — Polish

16. Split largest modules (`app.py`, `telegram.py`, `github.py`) like swarm P2-1  
17. Hub install stubs → real or hide  
18. Local STT or remove option  
19. `generate_metrics.py --check` in CI  
20. Merge docs-v2 into Docusaurus  

---

## Verification run this audit

| Check | Result |
|-------|--------|
| `pytest tests/test_hitl_wiring.py tests/test_hitl.py tests/test_auth_middleware.py` | **207 passed** |
| MCP name vs SafetyMiddleware live probe | **C1 confirmed** |
| Packages in Settings | Present; orphan `packages.html` removed in session work |
| Server bind 9090 on this host | **Blocked by Windows** — used 9091 |

---

## Suggested next session

Pick one track:

| Track | First ticket |
|-------|----------------|
| **Security** | C1 MCP HITL fix + regression test |
| **Auth** | C2 cookie model redesign |
| **CI** | Wire `kazma-core/tests` + Bandit fail-closed |
| **Docs** | Root architecture link + roadmap truth pass |
| **Product** | `/undo`/`/edit` implement or strip from help |

---

*Generated 2026-07-18. Critical claims C1 verified against live SafetyMiddleware. Prior remediation notes: `REMEDIATION_NOTES_2026-07-13.md`, `UNWIRED_INVENTORY.md`, `docs-v2/AUDIT_SUMMARY.md`.*

---

## Remediation status (same day)

| Finding | Status |
|---------|--------|
| C1 MCP HITL fail-open | **Fixed** — `force_danger=True` in MCP path + regression test |
| C2 cookie auto-issue | **Fixed** — loopback-only auto-cookie; remote needs header |
| H3 settings raw API key | **Fixed** — masked in HTML template path |
| H4 config_read secrets | **Fixed** — mask secret-key patterns |
| H5 JWT tenant forgery | **Fixed** — requires verified HS256 + `KAZMA_JWT_SECRET` |
| H6 cross-thread HITL | **Fixed** — deny when target session missing |
| H1 shell interpreters | **Fixed** — removed python/node/bash/sh from allowlist |
| IDE tests in CI | **Fixed** — `kazma-core/tests` in testpaths + CI |
| Bandit fail High | **Fixed** — `-lll -ii` fails job |
| Port/version drift | **Fixed** — yaml 9090, version 0.5.0, .env.example |
| /undo /edit honesty | **Fixed** — stubs labeled accurately |
| ConfigStore singleton | **Fixed** — app.py + TUI use `get_config_store()` |
| get_hitl_config on run path | **Fixed** — agent_runner `_ensure_graph` |
| Danger-list dual source | **Fixed** — `CANONICAL_DANGER_TOOLS` single source + yaml/bus parity tests |
| `/undo` `/edit` checkpoint mutation | **Fixed** — LangGraph `aget_state`/`aupdate_state` in agent_handler |
| Remote auth login UX | **Fixed** — `/login` + `POST /api/auth/login|logout` + status |
| code_exec sandbox depth | **Hardened** — import blocklist + scrubbed env (not a full jail) |
| Vault-default for API keys | **Fixed** — ConfigStore sensitive keys → vault when `KAZMA_VAULT_KEY` set |
| Root architecture.md | **Fixed** — pointer to docs-v2 |
| docs-v2 roadmap stale rows | **Updated** — 429, metrics, MCP stdio, undo/edit, vault, login |
| Packages one-click install | **Fixed** — Settings Install → allowlisted extras via API |
| Docker host port | **Fixed** — compose maps 9090→8000 by default |
| kazma-web default port | **Fixed** — 9090 / KAZMA_PORT |
| delegation keep decision | **Documented** — DeprecationWarning + UNWIRED status |
| Soft-nav SPA | **Polished** — wait for scripts, serialize navs, progress bar, Alpine bind check |
| Keyboard shortcuts | **Fixed** — match sidebar ⌘1–6 + ⌘, |
| docs/README + docs-v2 version | **Updated** — docs-v2 as SoT, 0.5.0 anchor |
| code_exec OS jail | **Docker** — `--network none` when docker available; local fallback |
| Full Docusaurus merge of docs-v2 | **Done** — `docs/docs/guide/*` + Guide sidebar + Mermaid; build succeeds |
