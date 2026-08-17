# Plan: Leftovers except G

**Date:** 2026-08-17  
**Status:** implemented 2026-08-17 (P0–P3; P4 inspectors already soft-nav). Follow-up 2026-08-17: Settings/Dashboard/Agents/Skills/MCP soft-nav, document GC on Postgres metadata, Majlis orchestrator on greetings, skill cert+lint on install, `/api/security/hardening` + `/api/security/deps`. Chat/IDE/Swarm stay hard. KB+beliefs one-table merge still wontfix.  
**Not this plan:** G (WhatsApp, BrightData/Oxylabs, soak, Windows/Postgres CI gates, Ruff/Bandit as gates, phone sign-off, hosted embed fleet #78, Gmail `pageToken`)

## Context

The A–F audit lanes shipped. Memory graph tools are now in `_PROF` so merge/delete/link work after a server restart. What remains (except G) is leftover **mouths**, **enforcement honesty**, and **memory scale** — not another architecture rewrite.

Do **not** flip `enforce_unknown_mutators` off. Do **not** start/restart the server from the agent.

## Non-goals (leave as they are)

- Loopback WebSocket auto-auth (local TUI/Web still need it).
- Balanced outbound with an empty allowlist (HITL still applies; Strict already clarifies).
- Soul confirm off on a single-operator lab (already on in production / multi-user).
- `/health` and `/api/status` staying reachable (payloads already stripped of workspace paths / init-error strings).
- Document metadata remaining SQLite unless the operator turns on the Postgres metadata backend.
- A new memory engine, a new intent router, or a React rewrite.
- Starting or restarting `kazma serve`.

## Approach

One mouth, one brain. Anything the TUI still does in-process that the Web already does via HTTP should call `request_json` / `request_json_async` / `POST /api/chat/stream` — same pattern as `/replay`, `/fork`, `/swarm`, `/memory`, Documents.

Permissions stay one list. If we enforce YAML `permissions.py`, it must sit **inside** `LocalToolRegistry.execute` next to HITL + `authorize_effect`, not as a parallel executor.

Memory scale uses the existing V2 stack + adapters. No third recall path.

---

## Patch 0 — Archive leftover season twins

A take-over used to mint `Telegram · bAlfaris` next to `/yolo` on the **same**
`thread_id`. New turns no longer do that. Old twin rows still clutter the
sidebar until something archives them.

- `prune_twin_sessions()` groups SessionManager rows by `thread_id`, keeps
  `canonical_web_session` (named / longer wins), **archives** the rest
  (not hard-delete — recoverable).
- Run from `SessionManager.list_all` (same cadence as empty-web prune).
- Never archive the last remaining row for a thread.

---

## Patch 1 — TUI leftover mouths

Turn the remaining local TUI commands into server inspectors / the same stream.

| Surface | Today | Target |
|---------|--------|--------|
| `/personality` | in-process personality store | live API or a graph turn (same personality the Web uses) |
| `/config` | “use Settings tab” stub | read/write via `/api/settings` (protected keys stay denied) |
| `/export` | writes `kazma-data/exports` from TUI memory | `GET` session messages from the server, then write locally |
| `/context` | estimates from TUI `_messages` | session `?stats=1` + server window config |
| Settings / theme | local ThemeManager | persist via ConfigStore API if one exists; otherwise stay local and **say so** |
| Traces | local TraceStore | live `/api` traces if mounted; otherwise honest “server traces on Web” |
| Dashboard CPU/RAM | process-local | keep local (this process) — **do not** pretend they are the server’s |

**Reuse:** `kazma_core.runtime.local_api.request_json_async`, `kazma_tui.chat.ChatPanel._api`, `/api/chat/sessions/{id}/messages?stats=1`.

**Files:** `kazma-tui/kazma_tui/chat.py`, settings/traces panels, tests under `kazma-tui/kazma_tui_tests/` + `tests/test_audit_*`.

---

## Patch 2 — YAML permissions on the live tool path

`kazma_core/permissions.py` + `kazma-permissions.yaml` are tested but **not** the runtime gate.

- Call `PermissionManager.is_allowed(tool, user)` from `LocalToolRegistry.execute` **after** the commitment gate, **before** the function runs.
- Default file stays empty-allow `[]` unless the operator grants tools — that would break the product. **Default must remain `allowed: ["*"]` for the single-operator `default` user** (today’s implicit “all tools”). Enforcement is fail-closed only when a real allow/deny list is present **or** `KAZMA_PERMISSIONS_ENFORCE=1`.
- Store/load errors: if enforce is on → deny; if off → allow and log (honest).
- Do not invent a second danger list.

**Files:** `kazma-core/kazma_core/permissions.py`, `kazma-core/kazma_core/agent/tool_registry.py`, `kazma-permissions.yaml` (only if the default `*` is missing), `tests/test_permissions.py`.

---

## Patch 3 — Memory scale leftovers (not #78)

From `docs/plans/MEMORY_REMAINING.md`:

- **#76** Full Postgres-primary recall (not just dual-mirror / ILIKE assist).
- **#77** Multi-region + conflict policy.

Ship only what already has an adapter seam (`VectorBackend`, GraphBackend). Fail-closed honestly if the primary is configured and down — no silent SQLite lie.

**Out of this patch:** hosted embed fleet (#78 = G), physical KB+beliefs one-table merge (wontfix).

---

## Patch 4 — Soft-nav leftover shells (only if they actually work)

Chat / IDE / Swarm / Settings / Dashboard / Agents / Skills / MCP stay **hard** reload. Do **not** force those onto soft-nav.

Optional: if a remaining hard page has a working Alpine factory + script in `PAGE_SCRIPT_RE` and a smoke test can prove it, move it. Otherwise leave it.

**Files:** `kazma-ui/kazma_ui/static/js/modules/nav.js`, `tests/test_audit_f_remaining.py`.

---

## Explicitly not wiring (library stays library)

Keep as library unless a later product call says otherwise:

- `authorization_flow.py`, `division_sandbox.py`
- `security/certification.py`, `linter.py`, `dependency_scanner.py`, `disclosure.py`, `hardening.py`
- Majlis as a full orchestrator (pacing/tone already live)

`swarm_notify` is already opt-in (`SWARM_BOT_TOKEN`).

---

## Verification

1. `py_compile` every edited Python file; `node --check` every edited JS file.
2. Targeted pytest: TUI command source uses `/api/` not a second LLM; `PermissionManager.is_allowed` is called from `execute`; memory tools still registered; existing commitment/auth tests stay green.
3. Never start/restart the server. Tell the operator to `git pull` on `C:\Users\balfa\kazma` and restart.

## Order

1 → 2 → 4. Patch 3 (#76/#77) last — largest and most independent.

## Goal one-liner (for `/goal`)

Close leftover TUI mouths (`/personality` `/config` `/export` `/context`, honest Settings/traces), wire YAML permissions into `LocalToolRegistry.execute` without breaking single-operator `*`, optionally finish soft-nav only for proven pages, then Postgres-primary recall (#76) and multi-region policy (#77). Do not do G. Do not disable `enforce_unknown_mutators`. Do not start the server.
