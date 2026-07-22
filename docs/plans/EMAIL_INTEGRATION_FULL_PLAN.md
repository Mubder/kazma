# Full Email Integration Plan — Gmail · Microsoft Graph · IMAP/SMTP · Sandbox

**Status:** Approved direction (implementation plan)  
**Owner call:** Defaults below are **decided** — no open product questions  
**Product ask:** Fully integrate email (Gmail + Microsoft) with read / write / delete / categorize / analyze  
**Package shape:** Native skill `email-manager` + thin core helpers  
**Related:** HITL (`safety/hitl.py`), Secret Vault, `paths.data_dir()`, NativeSkillLoader, product knowledge  

---

## 1. Goal

Add **full mailbox capabilities** to Kazma so the agent can:

| Capability | Tools (LLM-callable) |
|------------|----------------------|
| **Read** | `email_list`, `email_get` |
| **Write** | `email_send` (send · reply · forward · draft) |
| **Delete** | `email_delete` (trash / permanent) |
| **Categorize** | `email_categorize` (labels/folders, star/flag, read/unread) |
| **Analyze** | `email_analyze` (summary, actions, deadlines, sentiment, phishing) |

**Providers (all in this plan — not deferred):**

1. **Sandbox** — local SQLite mailbox, demo without credentials  
2. **Gmail** — IMAP/SMTP with app passwords (primary Google path for v1)  
3. **Microsoft** — **Microsoft Graph API** (primary M365/Outlook path)  
4. **Generic IMAP/SMTP** — self-hosted / other providers with host/port overrides  

**UX:** Natural chat (*“list my inbox”*, *“draft a reply”*) — no required `/email` slash command (optional later).  
Mode banner always present: `[sandbox]` | `[gmail]` | `[microsoft_graph]` | `[imap]`.

---

## 2. Decisions (locked)

| Topic | Decision |
|-------|----------|
| Default provider | **`auto`**: explicit tool arg → ConfigStore/YAML → first configured real account → **sandbox** |
| Never silent Gmail/Outlook | If no credentials → sandbox only; never invent “sent” without tool success |
| Microsoft | **Graph is in-scope in this plan** (OAuth device/code or client credentials + delegated scopes as configured) |
| Gmail | IMAP SSL **993** + SMTP STARTTLS **587** (app password); OAuth Gmail API optional stretch, not blocking |
| Ports | Secure defaults + **optional** `EMAIL_*_HOST/PORT` overrides |
| Secrets | Env **or** Vault (`vault_store` / `vault_retrieve`); never log secrets or full bodies at INFO |
| Mutating tools | **HITL danger:** `email_send`, `email_delete`, `email_categorize` |
| Read/analyze | Safe/read by default; body size-capped before LLM |
| Sandbox DB | `kazma_core.paths.data_dir() / "sandbox_emails.db"` (project data, not user git workspace) |
| Real mail writes | Always go through backends; drafts stored provider-side when supported, else sandbox/local only |

---

## 3. Architecture

```
                    ┌─────────────────────────────────────┐
  Chat / Swarm      │  Native skill: email-manager         │
  LLM tool calls ──►│  tools.py (thin wrappers)            │
                    └─────────────────┬───────────────────┘
                                      │
                    ┌─────────────────▼───────────────────┐
                    │  EmailRouter / get_backend(provider) │
                    │  auto | sandbox | gmail | microsoft  │
                    │  | imap                              │
                    └─┬──────────┬──────────┬─────────────┘
                      │          │          │
           ┌──────────▼──┐  ┌────▼────┐  ┌──▼──────────────┐
           │ Sandbox     │  │ Gmail   │  │ Microsoft Graph │
           │ SQLite      │  │ IMAP/   │  │ (Outlook/M365)  │
           │ seed data   │  │ SMTP    │  │ + Generic IMAP  │
           └─────────────┘  └─────────┘  └─────────────────┘
                      │
           ┌──────────▼──────────┐
           │ email_analyze       │
           │ → ModelRegistry /   │
           │   active LLM        │
           └─────────────────────┘
```

### 3.1 Package layout

```text
kazma-skills/kazma_skills/native/email_manager/
  skill_manifest.yaml
  tools.py                 # 6 tools only — thin
  models.py                # EmailMessage, ListQuery, SendRequest, AnalyzeResult
  router.py                # provider resolution (auto)
  analyze.py               # prompts + JSON schema parse
  seed/
    sandbox_seed.json      # realistic threads, phishing sample, boss mail
  backends/
    base.py                # Protocol EmailBackend
    sandbox.py
    imap_smtp.py           # Gmail + generic IMAP/SMTP
    microsoft_graph.py     # Graph mail + mailFolders + sendMail
  tests/                   # optional co-located; primary suite under tests/
```

Optional tiny core helper (if shared outside skill):

```text
kazma-core/kazma_core/email/   # only if needed by multiple packages
  __init__.py                  # re-exports; prefer keep logic in skill first
```

**Preference:** keep logic in the native skill unless a second consumer appears (YAGNI).

### 3.2 Backend protocol (`EmailBackend`)

```python
class EmailBackend(Protocol):
    name: str  # sandbox | gmail | microsoft_graph | imap

    async def list_messages(self, query: ListQuery) -> list[EmailMessage]: ...
    async def get_message(self, message_id: str) -> EmailMessage: ...
    async def send(self, req: SendRequest) -> SendResult: ...  # send|reply|forward|draft
    async def delete(self, message_id: str, permanent: bool = False) -> None: ...
    async def categorize(self, req: CategorizeRequest) -> None: ...
    async def list_folders(self) -> list[Folder]: ...  # optional helper for categorize
```

All tools call the protocol — **zero** provider branches in `tools.py` beyond `get_backend()`.

---

## 4. Provider details (same plan)

### 4.1 Sandbox

| Item | Spec |
|------|------|
| Store | SQLite WAL under `data_dir()/sandbox_emails.db` |
| Schema | `folders`, `messages`, `labels`, `message_labels`, `threads` (optional) |
| Seed | ≥15 messages: inbox, starred, spam/phishing lottery, boss@corp urgent, reply chain, draft |
| Banner | Every tool response starts with `[sandbox mode]` |
| Mutate | Full local semantics (trash folder, labels, drafts table) |
| Analyze | Works on sandbox bodies without network |

### 4.2 Gmail (IMAP/SMTP)

| Item | Spec |
|------|------|
| Auth | Address + **App Password** (env or vault) |
| IMAP | `imap.gmail.com:993` SSL |
| SMTP | `smtp.gmail.com:587` STARTTLS |
| List/search | IMAP SEARCH / UID FETCH; page with `limit`/`offset` or cursor |
| Labels | Gmail IMAP `X-GM-LABELS` where available; fallback folders |
| Drafts | APPEND to `[Gmail]/Drafts` when possible |
| Secrets | `EMAIL_GMAIL_ADDRESS`, `EMAIL_GMAIL_APP_PASSWORD` or vault keys `email.gmail.address` / `email.gmail.app_password` |

### 4.3 Microsoft Graph (in-scope)

| Item | Spec |
|------|------|
| API | Microsoft Graph v1.0 `https://graph.microsoft.com/v1.0` |
| Auth | OAuth 2.0:  
  - **Delegated** (preferred for personal mailbox): auth code / device code → refresh token in vault  
  - **App-only** optional later for org mailboxes (application permissions) |
| Scopes (delegated baseline) | `Mail.Read`, `Mail.ReadWrite`, `Mail.Send`, `MailboxSettings.Read` (minimize; document least privilege) |
| List | `GET /me/messages` + `$filter` / `$search` / `$top` / `$skip` |
| Get | `GET /me/messages/{id}` (+ `$select`, body prefer text) |
| Send | `POST /me/sendMail` or create draft `POST /me/messages` + send |
| Reply/forward | `createReply` / `createForward` / `forward` actions |
| Delete | `DELETE` or move to `deleteditems` |
| Categorize | categories, `isRead`, flag, move to `mailFolders` |
| Secrets | `EMAIL_MS_CLIENT_ID`, `EMAIL_MS_CLIENT_SECRET` (if confidential), `EMAIL_MS_TENANT_ID`, refresh token in vault `email.microsoft.refresh_token` |
| Config | `EMAIL_MS_REDIRECT_URI` or device-code flow for CLI/headless operators |

**Honest note in product knowledge:** Graph is the supported M365 path; basic IMAP to Outlook is **optional fallback** only when the tenant allows it — Graph remains the primary Microsoft implementation in this plan.

### 4.4 Generic IMAP/SMTP

Same as Gmail client with configurable hosts/ports/security for Fastmail, corporate, etc.

---

## 5. Tool contracts

### 5.1 Common args

```text
provider: "auto" | "sandbox" | "gmail" | "microsoft" | "imap"   # default auto
account: optional alias when multi-account (v1.1)
```

Every success/error string: prefix mode banner + structured enough text for the LLM (and optional JSON in `details` if registry supports it — plain markdown tables OK for v1).

### 5.2 `email_list`

```text
folder: str = "INBOX" | "inbox" | Graph folder id/name
query: str = ""          # free text / subject from:
limit: int = 20          # max 50
offset: int = 0
unread_only: bool = false
```

Returns: id, from, subject, date, unread, snippet, labels.

### 5.3 `email_get`

```text
message_id: str
include_body: bool = true
max_body_chars: int = 32000
```

### 5.4 `email_send` (**HITL danger**)

```text
action: "send" | "reply" | "forward" | "draft"
to: list[str] | str
cc: optional
subject: str
body: str
body_format: "text" | "html" = "text"
in_reply_to / message_id: for reply/forward
```

Idempotency: optional `client_request_id` stored short-term to reduce double-send on retry.

### 5.5 `email_delete` (**HITL danger**)

```text
message_id: str
permanent: bool = false   # false → trash
```

### 5.6 `email_categorize` (**HITL danger**)

```text
message_id: str
mark_read: optional bool
star: optional bool
add_labels: optional list[str]
remove_labels: optional list[str]
move_to_folder: optional str
```

### 5.7 `email_analyze`

```text
message_id: str | None
raw_text: str | None      # one of message_id or raw_text
focus: "full" | "security" | "actions" = "full"
```

Output JSON (and short markdown summary):

```json
{
  "summary": "...",
  "action_items": [{"text": "...", "deadline": null}],
  "sentiment": "neutral|positive|negative|urgent",
  "security": {
    "risk_level": "low|medium|high",
    "phishing_signals": [],
    "notes": "..."
  },
  "mode": "sandbox|gmail|microsoft_graph|imap"
}
```

Uses **active** model from ModelRegistry / agent config; body truncated before prompt.

---

## 6. Security & HITL

### 6.1 Danger list

Add to `CANONICAL_DANGER_TOOLS` + `kazma.yaml` `safety.hitl.require_approval_for`:

- `email_send`
- `email_delete`
- `email_categorize`

Swarm bus extended danger list if present should include the same.

### 6.2 Rules

- No secrets in tool results or traces (mask).  
- Sandbox clearly labeled.  
- Production: YOLO does not auto-approve email send (same as other danger tools).  
- Multi-user later: mailbox ops = operator/admin only (note for SaaS phase).  
- Audit: log tool name, message_id, action, provider, success — not body — via existing audit logger if easy.

### 6.3 OAuth / tokens (Microsoft)

- Store refresh tokens **only** in vault encrypted at rest.  
- Document consent screen / admin consent for org tenants.  
- Provide a small CLI or settings path: `email connect microsoft` (can be Settings UI later; v1 = env + documented device-code helper script under `scripts/`).

---

## 7. Configuration

### 7.1 Env (examples)

```bash
# Provider preference (optional)
EMAIL_DEFAULT_PROVIDER=auto

# Gmail
EMAIL_GMAIL_ADDRESS=
EMAIL_GMAIL_APP_PASSWORD=

# Microsoft Graph
EMAIL_MS_TENANT_ID=common
EMAIL_MS_CLIENT_ID=
EMAIL_MS_CLIENT_SECRET=          # if confidential client
EMAIL_MS_REDIRECT_URI=http://127.0.0.1:9090/api/email/oauth/callback

# Generic IMAP/SMTP
EMAIL_IMAP_HOST=
EMAIL_IMAP_PORT=993
EMAIL_SMTP_HOST=
EMAIL_SMTP_PORT=587
EMAIL_ADDRESS=
EMAIL_PASSWORD=

# Sandbox
EMAIL_SANDBOX_DB=                # optional override; default paths.data_dir()/sandbox_emails.db
```

### 7.2 `kazma.yaml` (optional section)

```yaml
email:
  enabled: true
  default_provider: auto   # auto | sandbox | gmail | microsoft | imap
  list_max: 50
  analyze_max_body_chars: 32000
```

### 7.3 Vault key names (canonical)

| Key | Purpose |
|-----|---------|
| `email.gmail.address` | Gmail address |
| `email.gmail.app_password` | App password |
| `email.microsoft.refresh_token` | Graph refresh |
| `email.microsoft.client_id` | Optional if not env |
| `email.imap.password` | Generic |

---

## 8. Delivery plan (one plan, sequenced PRs)

All providers including **Microsoft Graph** are in the **same plan**. Implementation is sequenced so each PR is shippable and testable; Graph is **not** optional wishlist — it is PR-3 in this track.

| PR | Name | Deliverable | Exit criteria |
|----|------|-------------|-----------------|
| **PR-1** | Sandbox + contracts | Models, backend protocol, sandbox backend + seed, 6 tools wired (sandbox only), HITL entries, unit tests | Chat: “list my inbox” → sandbox mail; mutate + analyze on sandbox |
| **PR-2** | Gmail IMAP/SMTP | `imap_smtp` backend, vault/env resolution, list/get/send/delete/categorize against Gmail | Live test with app password; HITL on send |
| **PR-3** | **Microsoft Graph** | OAuth helper + `microsoft_graph` backend implementing full protocol; list/get/send/reply/draft/delete/categorize | Live test against Outlook/M365 test tenant; HITL on send |
| **PR-4** | Analyze polish + docs | Robust analyze prompts, phishing fixtures, product_knowledge, guide `docs/docs/guide/email-integration.md`, env catalog, tools catalog | Docs + analyze tests green |
| **PR-5** | Generic IMAP + multi-account (if needed) | Host/port overrides, second account alias | Optional if PR-2 already covers generic |

**Dependency order:** PR-1 → PR-2 and PR-3 can be parallel after PR-1 (both implement `EmailBackend`). Prefer PR-2 then PR-3 if one engineer.

**Do not ship** “Microsoft” advertised in product knowledge until PR-3 merges.

---

## 9. Tests

| Suite | Coverage |
|-------|----------|
| `tests/test_email_manager_sandbox.py` | Schema, seed, list/get/send/delete/categorize, analyze mock LLM |
| `tests/test_email_router.py` | auto resolution: no creds → sandbox; gmail env → gmail |
| `tests/test_email_imap_unit.py` | Mocked imaplib/smtplib |
| `tests/test_email_graph_unit.py` | Mocked httpx Graph client (list/send/delete/folders) |
| `tests/test_email_hitl.py` | Danger tools in CANONICAL / requires_approval |
| Manual | Sandbox chat; Gmail app password; Graph device-code against test mailbox |

```powershell
& '.venv\Scripts\python.exe' -m pytest tests/test_email_manager_sandbox.py tests/test_email_router.py tests/test_email_graph_unit.py -v
```

---

## 10. Docs & agent knowledge (PR-4)

- New guide: `docs/docs/guide/email-integration.md` (connect Gmail, connect Microsoft, sandbox, HITL, privacy)  
- Env vars table  
- Tools catalog rows  
- FAQ: “How do I connect Gmail / Outlook?”  
- `product_knowledge.py`: email tools, sandbox vs real, never claim send without tool result, Arabic brand rules unchanged  

---

## 11. Out of scope (explicit)

| Item | Why |
|------|-----|
| Full calendar / Teams | Separate product |
| Attachment binary malware scanning | Metadata + size limits only in v1 |
| Guaranteed deliverability / spam inbox placement | Provider-side |
| Reading other users’ mailboxes in multi-tenant SaaS without RBAC | Requires multi-user email ACL design |
| Replacing vault with plaintext `.env` only | Env allowed; vault preferred |

---

## 12. Success criteria (full plan done)

1. **Sandbox:** full CRUD + analyze without any cloud credentials.  
2. **Gmail:** list/read/send/trash/label with app password + HITL on mutators.  
3. **Microsoft Graph:** same capability surface on Outlook/M365 with OAuth.  
4. **Auto provider** never uses a real account unless configured.  
5. **Tests** green offline (mocks); manual path documented.  
6. **Docs + product knowledge** teach the agent and operators.

---

## 13. Implementation kickoff checklist (first coding session)

- [ ] Scaffold `email_manager/` + `skill_manifest.yaml`  
- [ ] `models.py` + `backends/base.py`  
- [ ] Sandbox schema + seed + `backends/sandbox.py`  
- [ ] Wire 6 tools → router → sandbox  
- [ ] Add HITL danger tools + tests  
- [ ] Chat smoke: list / get / analyze / draft  

Then Gmail backend → Graph backend → docs.

---

## 14. Summary

| Ask | Plan answer |
|-----|-------------|
| Full Gmail + Microsoft | **Yes** — Gmail IMAP/SMTP + **Microsoft Graph in the same plan** |
| Read/write/delete/categorize/analyze | **Yes** — six tools |
| Demo without keys | **Sandbox** SQLite |
| Safe defaults | **auto → sandbox**; HITL on mutators |
| One document | **This file** — sequenced PRs, Graph not deferred to a vague future |

**Next step when you say implement:** start **PR-1 (Sandbox + contracts + HITL)** immediately.
