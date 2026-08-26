---
id: x-publisher
title: X publisher (official API)
sidebar_label: X publisher
description: Post to X through the official API v2 with OAuth 1.0a, vaulted keys, always-on HITL, and ToU fail-safes.
---

Kazma tweets **only** through the [official X API v2](https://developer.x.com/en/docs/twitter-api) using **OAuth 1.0a user context**. There is no scrape path, no Playwright/computer-use poster, and no app-only Bearer posting (Bearer can read; it cannot `POST /2/tweets`).

## What is official

| You do | Kazma does |
|--------|------------|
| X Developer account + Project + App | Nothing until keys exist |
| App **User authentication = Read and write** | Refuses 403 with that hint |
| Generate the **four** OAuth 1.0a values | Stores them in the vault via **Settings → X** |
| Label the account [Automated](https://help.x.com/en/using-x/automated-account-labels) | Reminds you; cannot set the label via API |
| Approve each tweet | `x_post` / `x_delete_post` **always** HITL — YOLO cannot skip |

Do **not** paste keys in chat. `vault_store` would put them in history. Settings → X writes `connectors.x.*` through ConfigStore, which vault-encrypts `api_key` / `*_secret` / `access_token`.

## Setup

1. [developer.x.com](https://developer.x.com) → Project + App (Free tier: 1 project / 1 app).
2. User authentication settings → **Read and write**.
3. Keys and tokens → API Key, API Key Secret, Access Token, Access Token Secret.
4. Kazma **Settings → X** → paste the four values + your `@handle` → Save → Test (`GET /2/users/me`).
5. Enable posting. Ask Kazma in chat to draft; you approve the HITL card; then it posts.

Optional env (overrides ConfigStore when set): `X_API_KEY`, `X_API_KEY_SECRET`, `X_ACCESS_TOKEN`, `X_ACCESS_TOKEN_SECRET`. Kill-switch: **`KAZMA_X_POST=0`**.

## Fail-safes (X rules / ToU)

These are Kazma caps **on top of** X's own quota. They fail closed.

| Guard | Default | Why |
|-------|---------|-----|
| Always-HITL (`ALWAYS_HITL_TOOLS`) | `x_post`, `x_delete_post` | Human sees the exact text. YOLO / grants / HITL-off cannot skip. |
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
| `x_post` | **always** | `text`, optional `reply_to_id` for a thread hop. |
| `x_delete_post` | **always** | `tweet_id` from a previous post. |

Swarm workers stay capped by the commitment swarm-scope (outbound CRITICAL denied by default).

## Honest limits

- Free tier is tight. Your X developer dashboard is the source of truth for quota.
- An Automated label is **your** action in X settings; Kazma cannot flip it.
- If keys leak, regenerate them in the dashboard, then Settings → X → Disconnect and save the new four.
