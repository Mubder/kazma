---
id: x-publisher
title: X publisher (official API)
sidebar_label: X publisher
description: Post to X through the official API v2 with OAuth 1.0a, vaulted keys, X Studio, always-on chat HITL, and ToU fail-safes.
---

Kazma tweets **only** through the [official X API v2](https://developer.x.com/en/docs/twitter-api) using **OAuth 1.0a user context**. There is no scrape path, no Playwright/computer-use poster, and no app-only Bearer posting (Bearer can read; it cannot `POST /2/tweets`).

## What is official

| You do | Kazma does |
|--------|------------|
| X Developer account + Project + App | Nothing until keys exist |
| App **User authentication = Read and write** | Refuses 403 with that hint |
| Generate the **four** OAuth 1.0a values | Stores them in the vault via **Settings → X** |
| Label the account [Automated](https://help.x.com/en/using-x/automated-account-labels) | Reminds you; cannot set the label via API |
| Approve outbound tweets | Chat tools are **always HITL**. On the Web, **your click** on X Studio (`/x`) or `/api/scheduled/x` is the approval |

Do **not** paste keys in chat. `vault_store` would put them in history. Settings → X writes `connectors.x.*` through ConfigStore, which vault-encrypts `api_key` / `*_secret` / `access_token`.

## Two surfaces

| Surface | Path | What it is |
|---------|------|------------|
| **X Studio** | `/x` | First-class composer + X-only planner. Post now, schedule, reschedule, thread hops, delete a live tweet, load a saved draft. |
| **Scheduled** | `/scheduled` | Mixed clock: cron jobs **and** X posts. X Studio's **All clocks** button opens this page on purpose. |
| **Chat tools** | Telegram / Discord / Slack / Web chat | `x_post` / `x_schedule_post` / `x_delete_post` / `x_cancel_scheduled_post` — always HITL, even under YOLO. |

Settings → X is credentials and caps only. Compose and plan on `/x`.

## Setup

1. [developer.x.com](https://developer.x.com) → Project + App (Free tier: 1 project / 1 app).
2. User authentication settings → **Read and write**.
3. Keys and tokens → API Key, API Key Secret, Access Token, Access Token Secret.
4. Kazma **Settings → X** → paste the four values + your `@handle` → Save → Test (`GET /2/users/me`).
5. Enable posting. Open **X Studio** (`/x`) to write and schedule.

Optional env (overrides ConfigStore when set): `X_API_KEY`, `X_API_KEY_SECRET`, `X_ACCESS_TOKEN`, `X_ACCESS_TOKEN_SECRET`. Kill-switches: **`KAZMA_X_POST=0`** (all posting), **`KAZMA_X_SCHEDULE=0`** (scheduling only).

## X Studio (`/x`)

The Web operator surface for posting. Alpine page `xStudioPage()` (`static/js/x_studio.js`); EN/AR strings in `i18n/catalog/x_studio.py`. Mutating calls send `X-Requested-With` and go through CSRF Origin checks.

**Composer**

- Live ToU preview from `evaluate_post` (280 chars, `@` / `#` / `$` caps) — no network, no ledger write (`POST /api/x/preview`).
- **Post now** → `publish_x_post` (`POST /api/x/post`). Your click is the approval.
- **Schedule** → `book_x_post` (`POST /api/scheduled/x`). Caps and the 30-day duplicate hash are reserved at booking.
- **Reply-to** accepts a tweet id or an `x.com/…/status/…` URL. After a successful Post now, the composer hops onto the new tweet id so the next send is a thread hop. **Clear thread** drops it.
- **Use** on a saved `save_proposal` draft stamps `proposal_id`. On post or schedule the **stored** text wins over whatever is in the box; the proposal is then marked posted. Editing the box after Use clears the id on the client so a later type-in is not rewritten. An unknown id returns **400**.

**Planner (X-only)**

- Upcoming X posts with a datetime-local field + **Reschedule** (`PUT /api/scheduled/x/{id}`) and **Cancel**.
- **Posted** rows: open on X, **Reply** (loads the id into the composer), **Delete** (`POST /api/x/delete` — confirm first). Chat `x_delete_post` is still always-HITL.
- **Drafts** from `GET /api/x/drafts` (artifact store `list_proposals`).
- **Audit** from `GET /api/x/audit` (`x_audit.db`).
- **All clocks** → `/scheduled` (cron + X together). The Studio queue stays X-only so a reminder job does not sit next to a tweet.

Not in this version: media, polls, quote tweets, likes, follows, DMs, scrape, recurring identical tweets, native X schedule API.

## Fail-safes (X rules / ToU)

These are Kazma caps **on top of** X's own quota. They fail closed.

| Guard | Default | Why |
|-------|---------|-----|
| Always-HITL (`ALWAYS_HITL_TOOLS`) | `x_post`, `x_delete_post`, `x_schedule_post`, `x_cancel_scheduled_post` | Chat/agent tools: human sees the exact text. YOLO / grants / HITL-off cannot skip. Web Studio does not call these tools — the operator click *is* the approval. |
| Official API only | `POST /2/tweets` | Automation must use the API, not the website. |
| No extra verbs | no like / follow / DM / search | Stays inside posting. |
| No write retry | network drop ≠ second POST | Avoids double-posts. |
| Length | 280 chars | Free-tier text tweets. |
| @mentions | max 2 (excluding your handle) | Unsolicited mention spam. |
| `$cashtags` | max 1 | Ticker spam. |
| Hashtags | max 4 | Hashtag stuffing. |
| Duplicate hash | 30 days | Identical automated copies. |
| Volume | 8/day, 80/30d | Well under Free-tier ~500/month. Raise only if your dashboard quota allows. |
| User-Agent | `Kazma/<ver> (self-hosted; official X API v2)` | X requires a UA. |

Media, polls, quote tweets, and v1.1 upload are **not** in this version (Free-tier media is often blocked; adding them later still goes through the same HITL + ledger).

## Tools

| Tool | HITL | What |
|------|------|------|
| `x_status` | no | Configured?, handle, remaining caps. Never returns secrets. |
| `x_post` | **always** | `text`, optional `reply_to_id` for a thread hop. Chat path requires a resolvable `proposal_id` (stored draft wins). |
| `x_delete_post` | **always** | `tweet_id` from a previous post. |
| `x_schedule_post` | **always** | `text` + `when` ('5m', '1h', 'daily at 9am', ISO) + optional `reply_to_id`. Approve once at booking. |
| `x_list_scheduled` | no | List scheduled X posts and their status. |
| `x_cancel_scheduled_post` | **always** | Cancel a scheduled post before it fires (releases reserved quota). |

These tools are the **chat** path. X Studio posts through `publish_x_post` / `book_x_post` / `delete_x_post` directly. Swarm workers stay capped by the commitment swarm-scope (outbound CRITICAL denied by default). A `proposal_id` is required on chat `x_post` / `x_schedule_post` — the commitment resolver rewrites `text` to the stored draft.

## Scheduled posts

X has **no native scheduled-post API** — the `/2/broadcasts/scheduled` endpoint schedules live *video* streams (it needs an RTMP `source_id`), and `POST /2/tweets` has no scheduling field. So Kazma owns the clock: a scheduled post is stored in `kazma-data/x_scheduled.db` and the scheduler fires `POST /2/tweets` directly at the appointed time (the same client-side pattern X's own docs describe).

- **Approve once at booking.** `x_schedule_post` is always-HITL; you approve the exact draft + time when you book it. The fire is deterministic (no LLM re-reading the post).
- **Caps + dedupe are reserved at booking.** Pending scheduled posts count toward the daily/monthly caps and the 30-day duplicate rule, so the schedule cannot be used to exceed them.
- **Double-post guard.** A failed fire is never auto-retried on an ambiguous error (we can't know whether it reached X). A 429 is deferred by the Retry-After window (bounded); anything else is marked failed and you're notified.
- **Kill-switch.** `KAZMA_X_SCHEDULE=0` disables scheduling (and `KAZMA_X_POST=0` disables posting entirely).

**Honest limitation:** a scheduled post fires only while the Kazma server is running. If the server is down at fire time, the post is caught up on the next boot. X cannot hold the schedule for you.

Manage the X content calendar in **X Studio** (`/x`). Open **All clocks** (or `/scheduled`) when you also need cron jobs. Chat tools stay in sync with both stores.

## Audit log (2026-08)

Every X API call is recorded in an append-only audit trail —
`kazma-data/x_audit.db` (SQLite WAL): local date-and-time with timezone,
action (`post` / `reply` / `delete` / `verify_credentials`), endpoint, HTTP
status, tweet id, the **full request payload and full response body**
(success, HTTP error, and network failure alike), and duration. The hook
lives at `XClient._request` — the single choke point covering the native
skill, `/api/x/*`, and scheduled tweets. Recording is best-effort and never
blocks the call. Inspect with any SQLite browser or
`query_x_audit(action="post", limit=50)`; nothing is auto-pruned
(`purge_x_audit(older_than_days=…)` exists if you ever want retention).

## Honest limits

- Free tier is tight. Your X developer dashboard is the source of truth for quota.
- An Automated label is **your** action in X settings; Kazma cannot flip it.
- If keys leak, regenerate them in the dashboard, then Settings → X → Disconnect and save the new four.
