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

## Connect Gmail

1. Enable 2FA and create a [Google App Password](https://myaccount.google.com/apppasswords).  
2. Set env (or vault keys `email.gmail.address` / `email.gmail.app_password`):

```bash
EMAIL_GMAIL_ADDRESS=you@gmail.com
EMAIL_GMAIL_APP_PASSWORD=xxxx-xxxx-xxxx-xxxx
```

3. Restart Kazma. Chat: *List my Gmail inbox* or `provider=gmail`.

IMAP `993` / SMTP STARTTLS `587` by default (`EMAIL_IMAP_*` / `EMAIL_SMTP_*` overrides).

## Connect Microsoft (Graph) — device code

1. Azure app registration (public client recommended for device code):  
   - Allow public client flows  
   - API permissions: `Mail.Read`, `Mail.ReadWrite`, `Mail.Send`, `offline_access` (delegated)  
2. Set:

```bash
EMAIL_MS_CLIENT_ID=<app-client-id>
EMAIL_MS_TENANT_ID=common   # or your tenant id
```

3. Call the API (authenticated with `KAZMA_SECRET` like other APIs):

```http
POST /api/email/oauth/microsoft/device/start
```

Response includes `user_code` and `verification_uri`. Open the URI, enter the code, approve.

4. Poll until authorized:

```http
POST /api/email/oauth/microsoft/device/poll
Content-Type: application/json

{"device_code":"<from start>"}
```

On success, tokens are stored in **env for this process** and in the **encrypted vault** (`email.microsoft.access_token` / `refresh_token`). Token **refresh** also re-persists to vault.

5. Disconnect:

```http
POST /api/email/oauth/microsoft/disconnect
```

Manual tokens (alternative):

```bash
EMAIL_MS_ACCESS_TOKEN=...
EMAIL_MS_REFRESH_TOKEN=...
EMAIL_MS_CLIENT_ID=...
EMAIL_MS_CLIENT_SECRET=...   # if confidential client
```

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
