---
id: email-integration
title: Email integration
sidebar_label: Email
description: Gmail, Microsoft Graph, IMAP, and sandbox email for Kazma agents
---

# Email integration

Kazma’s native **`email-manager`** skill gives the agent full mailbox tools: list, get, send, delete, categorize, and analyze. There is **no** required `/email` slash command — use **chat**.

## Quick start (sandbox — zero config)

Restart the server and ask:

- *List my inbox*
- *Analyze the lottery / phishing email*
- *Draft a reply to boss@corp.com* (HITL approve for send/draft mutators)

Without credentials, every response is prefixed with **`[sandbox mode]`**. Data lives in `kazma-data/sandbox_emails.db`.

## Tools

| Tool | Purpose | HITL |
|------|---------|------|
| `email_list` | List/search folder | No |
| `email_get` | Full message body | No |
| `email_send` | send / reply / forward / draft | **Yes** |
| `email_delete` | trash or permanent | **Yes** |
| `email_categorize` | read/star/labels/move | **Yes** |
| `email_analyze` | summary, actions, phishing | No |

Common args: `provider` (`auto`\|`sandbox`\|`gmail`\|`microsoft`\|`imap`), optional `account` (multi-account alias).

## Provider resolution (`auto`)

1. Explicit `provider` / `account` on the tool call  
2. `EMAIL_DEFAULT_PROVIDER`  
3. First configured real account (Gmail → Microsoft → IMAP → multi-account aliases)  
4. **Sandbox**

## Connect email (Settings UI)

Open **Settings → Email** (`/settings?tab=email`).

| Card | Action |
|------|--------|
| **Sandbox** | Always on — no setup |
| **Gmail** | Enter address + [App Password](https://myaccount.google.com/apppasswords) → **Save Gmail** (vault + process env) |
| **Microsoft** | Save Azure **Client ID** (+ tenant) → **Connect Microsoft** → enter device code at Microsoft → wait until “Connected” |

Status shows **Active provider (auto)**. Disconnect clears vault/env for that provider.

### Gmail (env alternative)

```bash
EMAIL_GMAIL_ADDRESS=you@gmail.com
EMAIL_GMAIL_APP_PASSWORD=xxxx-xxxx-xxxx-xxxx
```

IMAP `993` / SMTP STARTTLS `587` by default.

### Microsoft Graph (API alternative)

1. Azure app: public client + `Mail.Read` / `Mail.ReadWrite` / `Mail.Send` / `offline_access`  
2. Settings UI or:

```http
POST /api/email/oauth/microsoft/client   {"client_id":"…","tenant_id":"common"}
POST /api/email/oauth/microsoft/device/start
POST /api/email/oauth/microsoft/device/poll   {"device_code":"…"}
POST /api/email/oauth/microsoft/disconnect
```

Tokens: process env + vault; refresh re-persists to vault.

## Multi-account aliases

```bash
EMAIL_ACCOUNTS=personal,work

EMAIL_ACCOUNT_PERSONAL_TYPE=gmail
EMAIL_ACCOUNT_PERSONAL_ADDRESS=me@gmail.com
EMAIL_ACCOUNT_PERSONAL_PASSWORD=app-password

EMAIL_ACCOUNT_WORK_TYPE=microsoft
EMAIL_ACCOUNT_WORK_REFRESH_TOKEN=...
EMAIL_ACCOUNT_WORK_CLIENT_ID=...
```

Use in chat: *List work inbox* → agent should pass `account=work`, or call tools with `account="work"`.

Status:

```http
GET /api/email/status
GET /api/email/accounts
```

## Analyze

`email_analyze` uses the **active LLM** when available; otherwise a **heuristic** phishing/action detector. Always check `security.risk_level` on lottery/bank-looking mail.

## Safety

- Mutating tools require **HITL** approval (same gates as file/shell).  
- Never paste app passwords into chat; use env or vault.  
- Sandbox never sends real mail.  
- Graph is the supported M365 path; basic IMAP to Outlook is only for tenants that still allow it.

## Related

- Plan: `docs/plans/EMAIL_INTEGRATION_FULL_PLAN.md`  
- [Environment variables](../reference/environment-variables)  
- [Tools catalog](../reference/tools-catalog)  
