# First-Time Audit 2026-09-09 — Fix Log

Every finding from the 2026-09-09 first-time deep audit, mapped to its fix.
All changes are in the dev repo only; the server was never restarted.

## HIGH

| # | Finding | Fix |
|---|---------|-----|
| H1 | Bedrock provider dead — `boto3.client("bedrock")` has no `converse` | `bedrock_llm.py` `_SERVICE = "bedrock-runtime"` |
| H2 | Slack polling path skipped the user allowlist | `slack.py:_handle_message` now calls `actor_allowed()` (fail-closed) |
| H3 | `/session` take-over had no ownership; in-thread HITL approve checked no sender | Ownership registry in `sessions/directory.py` (`record_thread_owner` / `thread_owner` / `sender_may_take_over`, deterministic `gw-<plat>-<id>` derivation, `KAZMA_SESSION_OPEN_TAKEOVER` opt-in); enforced in `session_commands.py` switch + `/fork` records owner; `hitl.py` in-thread approve now verifies the bound sender |
| H4 | stdio MCP children inherited the entire parent env (all secrets) | `manager.py:_build_child_env` — allowlist + server-config env + auth; `KAZMA_MCP_INHERIT_ENV=1` escape hatch |
| H5 | Swarm template/autoscaler routes had no admin gate; unclamped `max_instances` | `_require_admin` on POST/DELETE templates + reap (`routes_general.py`); `WorkerTemplate.from_dict` clamps instances (≤50) and truncates name/role/model/prompt (≤20K) |
| H6 | Azure broken by forced `/v1`; empty endpoint → api.openai.com; `api_version` dropped | `azure_llm.py` strips the appended `/v1`, raises a clear `LLMError` with no endpoint, reads `LLMConfig.api_version` (new field, wired through `from_dict`) |
| H7 | Loopback auto-login had no Host validation (DNS rebinding) | `auth.py:_host_is_local_name` required for cookie auto-issue AND the WS loopback branch; `KAZMA_AUTOLOGIN_HOSTS` for LAN names; regression test added |
| H8 | `allow_all` default made admin surfaces open to everyone | New `is_gateway_admin` (`allowlists.py`, env `KAZMA_GATEWAY_ADMINS` authoritative / platform allowlist); gated Telegram `sys_install`+`install_dep`, Discord/Slack install actions, `/config model|memory|tools`, `/_models_select`, `/skill install` |
| H9 | Swarm crash recovery decorative (no running rows, no PENDING consumer, no watchdog) | `engine.dispatch` persists the RUNNING row immediately; `redispatch_recovered_tasks()` consumes requeued rows at boot; `start_maintenance_loop()` (60s reap + idle reap) wired in app start/stop |
| H10 | Base env-key fallback sent `OPENAI_API_KEY` to native providers; `KAZMA_API_KEY` to any vendor | `_resolve_api_key` gates env fallback to the generic provider; `model_registry` only applies `KAZMA_API_KEY` to Bearer presets |

## MEDIUM — LLM layer

| Finding | Fix |
|---------|-----|
| Anthropic stream fallback duplicated the prefix | `_emitted_any` guard + `response_format` forwarded (`anthropic_llm.py`) |
| `Retry-After` unbounded | clamped to [1, 60]s (`llm_provider.retry_after_seconds`) |
| `discover_models` built `Authorization: x-api-key <key>` | per-auth-style headers (`x-api-key`+`anthropic-version`, `api-key`, Bearer) |
| `google_llm` hard vertexai import / sync refresh / gcloud no timeout | `_import_vertexai` guarded lazy import; ADC refresh via `to_thread`; gcloud `timeout=30` |
| LiteLLM gateway forwarded the provider key to a remote URL | remote keyless gateways route direct; only loopback gateways receive the pass-through key |
| `openai/` local prefix never stripped | `_strip_routing_prefix(model, base_url)` strips it for non-LiteLLM local servers |
| Anthropic `max_tokens` over model caps | `_clamp_max_tokens` per-model table (3/3.5 family) |
| Anthropic stream undercounted input tokens | `message_start` usage read |
| Anthropic dropped http(s) image URLs | `{"type":"url"}` image sources emitted |
| timeout 0 disabled all timeouts | `LLMConfig.__post_init__` clamps ≤0 → 60 |
| bridged_event_stream queue unbounded | `maxsize=1000` + drop-oldest |
| Cost drift on truncation retry / nudge | `_merge_usage` + cost accumulation at both sites |

## MEDIUM — Web/UI

| Finding | Fix |
|---------|-----|
| Dashboard HITL cards sent no `interrupt_id` | cards carry it (`data-interrupt-id`), both approve paths send it |
| Optimistic "Approved ✓" paint before the fetch | honest "Sending decision…" inflight state; confirmed state painted in `.then()` |
| Approve `actor` client-supplied | derived server-side from the session cookie role + id (`misc.py`) |
| No SSE/WS prompt size cap | 512,000-char cap on both paths with a clear error |
| Backup delete/archive/download lacked admin gate | `_require_admin` on all three routes |
| XFF leftmost parsing spoofable | rightmost-untrusted-hop parse in `_client_host` |
| Upload MIME trusted | magic-byte sniff (`_sniff_mime`) overrides confident mismatches |

## MEDIUM — Gateway

| Finding | Fix |
|---------|-----|
| Slack `allowed_teams` dead config | enforced on socket-mode interactive+events envelopes (with ACK) |
| Slack socket-mode inline file prefetch stalled the reader | per-channel serial chains (`_chain_channel_work`/`_finalize_event`) |
| Discord per-message spawn swapped turn order | per-channel serial chains (`_channel_chains`) |
| Naive Discord/Slack chunking mid-token | boundary-aware split with fence close/reopen |
| Telegram media size checked post-download | Content-Length HEAD pre-check |
| Unknown callback data became an injected message | ignored (`text=""`) |

## MEDIUM — MCP/tools/skills

| Finding | Fix |
|---------|-----|
| MCP `tools/call` output unfenced | `fence_untrusted(source="mcp_tool:…")` |
| `email_get` returned raw attacker bodies | body + subject fenced |
| Browser extract/navigate/click raw page text | `_fence_page_text` at every return |
| Skill installer zipball uncapped | streamed download ≤100MB; ≤5k members; ≤500MB expanded; ratio ≤200; symlink members refused |
| MCP stdin write could block forever | bounded `_write_stdin` under `wait_for` |
| `trust: trusted` bypassed HITL in production | requires `KAZMA_MCP_TRUSTED_IN_PROD=1` when `KAZMA_PRODUCTION` is set |
| `shell_exec` skipped `--flag=value` paths | flag values extracted and containment-checked |
| file_write TOCTOU | pre-write symlink refusal + post-write access re-check with removal |

## MEDIUM — Swarm

| Finding | Fix |
|---------|-----|
| Approve-after-reap re-executed then discarded | terminal-state guard in `approve_checkpoint`; reaped tasks drop their `_paused` entry |
| Paused checkpoints consumed admission slots | admission counts non-paused only |
| Gateway flat 300s cap killed multi-step patterns | engine-formula budget (`timeout × steps + 30`); honest cancelled message |
| Unknown worker names registered forever | bounded ad-hoc pool (≤10) + reaped after dispatch (test updated to the new contract) |
| swarm_notify leaked an httpx client per call | closed in `finally` |

## MEDIUM — Agent graph

| Finding | Fix |
|---------|-----|
| checkpoints.db grew unbounded | `checkpoint_retention.py` daily sweep (newest 200/thread, dormant→10; `KAZMA_CHECKPOINT_RETENTION_DAYS=0` off; Postgres skipped honestly) |
| One raising tool killed the parallel batch | `gather(..., return_exceptions=True)` → error ToolResults |
| Tenant checkpoint filename from raw tenant id | sanitized + length-hashed; saver cache bounded at 32 |
| Dropped tool outputs unnamed in the summary net | `describe_dropped`/heuristic summary include tool names + heads |
| Two unwrapped savers on checkpoints.db | `checkpoints_shared.py` process-wide shared saver for the DEFAULT db only (custom paths keep owned connections); cached per event loop (LangGraph's saver holds a loop-bound lock); refcounted lifetime — the gateway retains forever, transient holders release on close, connection closes at zero |
| `KAZMA_AUTH_DISABLED` doc/code drift | production-guarded 503 like DEMO mode |

## MEDIUM — Persistence

| Finding | Fix |
|---------|-----|
| Nested secrets never vault-encrypted | `_encrypt_nested_sensitive` on the WRITE path (GET stays resolve-only — no ping-pong) |
| ConfigStore cache had no TTL | 15s TTL + timestamps; local writes still invalidate immediately |
| `atomic_update` unquoted strings poisoned reads | serializes exactly like `set()` (`json.dumps` always) |
| Task-queue sync SQLite pinned the loop | claim/ack/enqueue wrapped in `asyncio.to_thread` |
| Cron double-fire / stale-fire / lost-delivery | CAS `claim_job`; stale one-shot skip (24h default, env); deliver-before-finalize |
| Importer swapped live DBs | heartbeat gate (`system.heartbeat.epoch`, app stamps 60s) + opt-in port probe; `--force` escape |
| Plaintext vault fallback silent | once-per-process WARNING (vault off) / ERROR (vault store failure) |

## LOW (selected)

Dead tenacity decorators removed (`retry.py`, tests updated) · status sniffing pattern-anchored · `allowed_teams` enforced · WS `KAZMA_AUTH_DISABLED` guard · static rescan 10s cache · Google Fonts CDN → self-hosted IBM Plex Sans Arabic (OFL) · JS version cache · Arabic-aware token estimate (chars/2.5) · snapshot recorder created in `KazmaAgent.__init__` · `allow_interrupt` structural guard · `_InMemoryStore` protocol parity (`atomic_update`/`transaction`) · classifier words (mongo/mysql/amqp/…) · atomic nightly exports · singleton double-checked locks (workspace/knowledge) · path-rewrite boundary anchoring · macro_sleep batched executemany · pragma strays fixed · python_exec exec/eval/compile/breakpoint disabled in sandbox.

## Documented trade-offs (no portable fix; explicitly accepted)

- Documents parser sandbox has no network namespace (self-reported via `resource_limit_degraded_reason`; container image mitigates).
- `python_exec` local fallback remains "not a jail" (banned in production/multi-user; Docker jail preferred; exec/eval/compile now blocked as defense-in-depth).
- Cron `cancel`/`reschedule` cross-tenant guard remains API-auth-dependent (endpoints are default-deny; jobs carry no tenant column today).
- Postgres checkpoint retention is ops-owned (logged once).

## Verification

- All modified Python files pass `py_compile`; both modified JS files pass `node --check`.
- Targeted suites green per batch (LLM 102, auth/CSRF 190, gateway 58, skills/MCP/file 82, swarm core, cron 55, config-store 83, context/time-travel 101, migration 20, macro-sleep 6, checkpoint 12, agent unit+integration 10).
- Full suite via `scripts/fast_test.py` (final run): the only remaining failures are the 20 pre-existing failures that also fail on unmodified `main` (swarm UI panel `sort_at` schema family, telegram callbacks, turn-delivery CQRS, turn ledger, swarm logs, vision resize) — verified by a stash-diff baseline. No regressions introduced; the two files that hung during development (agent graph integration / agent unit exit-hang) were root-caused (loop-bound LangGraph saver lock, unclosed shared aiosqlite connection) and fixed.
