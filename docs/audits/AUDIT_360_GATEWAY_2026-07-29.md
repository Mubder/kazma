# 360° Audit — Gateway (Platform Adapters)

**Date:** 2026-07-29
**Scope:** Telegram/Discord/Slack adapters, slash commands, /ide commands,
SessionStore, platform-ID isolation, webhook ingress, outbound sanitization.
**Method:** Invariant AGENTS.md §2 (platform-ID isolation) verified rigorously.
Each finding tagged VULNERABILITY / SAFE / CONDITIONAL.

---

## Executive Summary

**Platform-ID isolation (the #1 invariant) holds** — SupervisorState is clean,
reply target is re-derived from inbound platform, no cross-platform/cross-user
reply injection. Telegram is well-defended end-to-end. Slash-command and
shell-exec paths are HITL-gated.

**However, Discord and Slack have real access-control bypasses on interactive
(HITL approval) payloads**, plus no outbound mention-sanitization. These are
the highest-severity findings in this report. **Overall: 67/100.**

---

## Findings

### ✅ G1 — Platform-ID isolation (§2) — SAFE
- `SupervisorState` (`agent/state.py:64-148`) has no `chat_id`/`user_id`/`message_id`/`channel_id`/`guild_id`.
- `_build_initial_state` (`store.py:126-201`) puts platform ctx in SessionStore, only `thread_id`/`platform` into state. Defense-in-depth strip of `_PLATFORM_KEYS` (`store.py:197-199`).
- `_build_target_id` (`store.py:204-224`) re-prefixes with **inbound** `msg.platform` — a stored foreign ctx can't cause a cross-platform reply.
- SessionStore keyed by `thread_id` ← `sender_id` = `<platform>:<user/chat>` (`store.py:86-88`); no cross-user collision; parameterized SQL.

### 🚨 G2a — Discord interaction allowlist bypass — VULNERABILITY (High)
- `adapters/discord.py:341-362`: HITL/personality/model interactions enqueue a synthetic `IncomingMessage` **without checking `self._allowed_users`**. Contrast Telegram which checks on callbacks (`telegram.py:985-990`).
- A non-allowlisted user clicking an approval button fires `hitl approve <thread_id>`. The `hitl.py:157` same-thread bypass (`target_thread == thread_id`) skips the sender check, and the synthetic `sender_id` is `discord:<channel_id>`, not the user.
- **Fix:** apply `self._allowed_users` before enqueuing synthetic commands from interactions.

### 🚨 G2b — Slack `block_actions` has NO user allowlist — VULNERABILITY (High)
- `adapters/slack.py:432-517`: processes interactive payloads with no `allowed_users`/actor check. Slack only has `allowed_teams`/`allowed_channels` (applied to *events*, `slack.py:547`, not interactive payloads). Any member who can see an approval card can click Approve (`sender_id = slack:<user_id>`).
- **Fix:** apply channel + actor checks to `block_actions`; add `allowed_users`.

### 🟧 G2c — Slack has no per-user allowlist primitive — Medium
- `SlackAdapter.__init__` (`slack.py:75-90`) accepts only `allowed_teams`/`allowed_channels`. Anyone in an allowed channel reaches the agent + runs `/ide`, `/swarm`, etc.

### ✅ G2d — Telegram allowlist — SAFE
- Enforced before the agent runs and on all entry paths (`telegram.py:338,483,985,1044`). Unpaired users dropped before enqueue.

### ✅ G3 — Webhook / ingress — SAFE
- Telegram webhook secret validated with `hmac.compare_digest` (`telegram.py:457-471`); auto-generates an ephemeral secret if unset. Discord/Slack use authenticated WSS (Gateway/Socket Mode) — no HTTP webhook to verify.
- **Smell (low):** ephemeral Telegram secret is in-memory (lost on restart). Operators should set `TELEGRAM_WEBHOOK_SECRET`.

### ✅ G4 — Slash-command injection from agent output — SAFE
- Commands parsed only from `msg.text` (inbound user message), never re-parsed from agent/tool output. `is_slash_command` is broad (`/`-prefix) but unrecognized commands fall through to the LLM (no exec). No agent-output→command loop.

### ✅ G5 — Shell via slash commands — SAFE (HITL-gated)
- `/ide run|edit|delete|git|runfile` → `IdeService` → `get_tool_registry().execute()` (same HITL gate as agent tools). `shell_exec` has a binary allowlist (no arbitrary shell). `/ide repo clone` uses argv list, no shell.

### 🟢 G6 — Message splitting / flood
- Telegram: HTML-aware split + outbound rate limit (30/s). Discord: naive fixed split (can break mid-entity — Low), rate-limited (5/s). Slack: **no splitting** — long output fails to send (Low robustness gap).

### ✅ G7 — Credential storage/logging — SAFE
- Tokens never logged (deliberate: `telegram.py:262-267,576` avoids logging exception strings with token URLs). `/config export` redacts secrets (`slash_commands.py:602-614`). No plaintext token on disk in gateway (persistence is ConfigStore/core).

### ✅ G8 — Error disclosure (main path) — SAFE
- Graph exceptions send sanitized `"⚠️ ... Processing error"`; full traceback logged server-side only (`graph.py:1040-1054`).
- **Smell (low):** some command helpers echo `{exc}` to chat (`/undo`, `/edit`, `/replay`, `/fork`, `/kb`, `/research`) — minor info disclosure (Python exception strings).

### ✅ G9a — Telegram outbound escaping — SAFE
- `md_to_tg_html` escapes **first** (`html.escape`), then applies markup. Agent output can't break into Telegram HTML.
- **Smell (low):** link URL uses `quote=False`; a crafted URL could inject an attribute, but Telegram's parser rejects it (400 → fallback). Use `quote=True`.

### 🚨 G9b — Discord/Slack outbound: NO mention sanitization — VULNERABILITY (Medium)
- `discord.py:424` posts `{"content": chunk}` verbatim; `slack.py:161-165` sends `text` with `mrkdwn: True` verbatim. If the agent (or untrusted knowledge/tool output flowing into the reply) emits literal `@everyone`/`@here`/`<@&role>` (Discord) or `<!everyone>`/`<!here>`/`<@U123>` (Slack), it **pings/broadcasts**.
- **Fix:** strip/escape `@(everyone|here|channel)`, `<@...>`, `<!...>` in `discord_send`/`slack_send` before posting.

---

## Roadmap

### ⚡ Phase 1 (immediate — the access-control holes)
1. **G2a** — Discord: check `self._allowed_users` before enqueuing synthetic HITL/picker commands from interactions.
2. **G2b** — Slack: apply channel + actor checks to `block_actions`; verify the clicking user.
3. **G9b** — Discord/Slack: sanitize outbound content for `@everyone`/mentions.

### 🏗️ Phase 2
4. **G2c** — Add `allowed_users` to Slack.
5. **G6** — Slack message splitting; Discord entity-aware split.
6. **G8-smell** — Sanitize `{exc}` in command helpers.
7. **G9a-smell** — `quote=True` in Telegram link escaping.

### ✅ Preserve
- The platform-ID isolation design (state clean, target re-derived from inbound platform) is correct — don't let a future refactor copy ctx wholesale into state.
- Telegram is the reference implementation (allowlist on all paths, escape-first outbound, HMAC webhook). Backport its patterns to Discord/Slack.
