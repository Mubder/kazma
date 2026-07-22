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
| **Gmail / Workspace** | **OAuth (recommended):** save Google OAuth Client ID + secret → **Connect with Google** → Google consent → redirect back Connected. Uses **Gmail API** (works when App Passwords are disabled). Optional fallback: app password. |
| **Microsoft 365** | **OAuth (recommended):** save Azure Client ID (+ secret if confidential) → **Connect with Microsoft** → browser consent → redirect back. Optional: device code. |

Status shows **Active provider (auto)**. Disconnect clears vault/env for that provider.

### Gmail OAuth setup (Workspace-friendly)

1. [Google Cloud Console](https://console.cloud.google.com/) → APIs & Services → enable **Gmail API**.  
2. OAuth consent screen → add scopes `gmail.modify`, `gmail.send`, `userinfo.email`.  
3. Credentials → **OAuth client ID** → type **Web application**.  
4. Authorized redirect URI (must match your host):

```text
http://127.0.0.1:9090/api/email/oauth/gmail/callback
https://your.domain/api/email/oauth/gmail/callback
```

5. Settings → Email → paste Client ID + secret → **Save OAuth client** → **Connect with Google**.

Env alternative:

```bash
EMAIL_GMAIL_CLIENT_ID=...
EMAIL_GMAIL_CLIENT_SECRET=...
# After OAuth, tokens are stored automatically:
# EMAIL_GMAIL_ACCESS_TOKEN / EMAIL_GMAIL_REFRESH_TOKEN (also vault)
```

App passwords still work for personal Gmail if your admin allows them; OAuth is preferred for Workspace.

### Microsoft Graph OAuth setup

1. Azure app registration → Web redirect URI:

```text
http://127.0.0.1:9090/api/email/oauth/microsoft/callback
```

2. Delegated permissions: `Mail.Read`, `Mail.ReadWrite`, `Mail.Send`, `offline_access`.  
3. Settings → Email → Client ID (+ secret if confidential client) → **Connect with Microsoft**.  

Device code remains available under “Alternative: device code”.

```bash
EMAIL_MS_CLIENT_ID=...
EMAIL_MS_CLIENT_SECRET=...   # if required
EMAIL_MS_TENANT_ID=common
EMAIL_MS_REDIRECT_URI=http://127.0.0.1:9090/api/email/oauth/microsoft/callback  # optional override
```

Set `KAZMA_PUBLIC_URL=https://your.domain` behind a reverse proxy so redirect URIs resolve correctly.

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
