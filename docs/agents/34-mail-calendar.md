<!-- AGENTS.md, piece 34-mail-calendar. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
### 34. Calendar / Gmail OAuth — vault is SoT, no silent sandbox

Live 2026-09-08: `list_events(provider=google)` returned
`Calendar: sandbox, No events found` after a successful Gmail reconnect.
The calendar router only read `GOOGLE_CALENDAR_TOKEN` from env; its
`_vault_get` stub always returned `""`. Gmail OAuth stores `email.gmail.*`
in the vault and does **not** automatically feed Calendar.

**Invariants:**
- Calendar tokens live in the vault (`calendar.google.*` /
  `calendar.microsoft.*`) via `kazma_skills.native.calendar.credentials`.
  Env vars are an override. Never reintroduce a vault stub that returns `""`.
- A Gmail-only token (`gmail.modify` without `auth/calendar`) must **not**
  be sent to Calendar. `google_access_token()` reuses the Gmail grant only
  when `email.gmail.scopes` includes Calendar.
- Explicit `provider=google` / `outlook` **fails closed**
  (`CalendarNotConnectedError`) — never silent sandbox — whether it is named
  in the call or by `KAZMA_CALENDAR_PROVIDER` (the env value was judged
  non-explicit until 2026-09-25). Sandbox is auto fallback only when no
  account is connected.
- **Email fails closed the same way** (`EmailNotConnectedError`,
  `email_manager/router.py`, since 2026-09-26). A provider named in the call
  or by `EMAIL_DEFAULT_PROVIDER`, or an account alias, that is not connected
  is refused with the Settings step to fix it; an unknown provider or a
  typo'd alias is refused too (both used to default to the sandbox, which
  answered "Sandbox sent to …" for a send that never happened). The sandbox
  answers only `auto` with nothing connected, `sandbox` itself, or an
  account whose TYPE is `sandbox`. Every email tool returns the refusal as
  its answer (`tests/test_email_fail_closed.py` enumerates the tools).
- **A Gmail listing asks for each header by its own parameter**
  (`gmail_api._LIST_HEADERS`, 2026-10-20): Gmail read
  `metadataHeaders=From,To,Subject,Date` as one header's name, so every
  listed mail had no sender or date and its preview as the subject; the agent
  opened mails one by one to answer "what's in my inbox today".
  `tests/test_gmail_list_headers.py` (a fake Gmail that answers only the
  headers named; the comma-joined form as the negative control).
  A listed mail's subject and sender are whole (150 and 80 characters, they
  were cut at 50 and 40): the agent opened each long one to answer.
- Connect with Google requests Calendar as a **soft** extra (like
  `drive.file`): Gmail connect still succeeds if Calendar API is off.
  Settings → Email → **Connect Google Calendar** is the dedicated grant (same
  OAuth client, same `/api/email/oauth/gmail/callback` — dispatch on
  the attempt's provider, `google_calendar`).
- Microsoft mail OAuth requests `Calendars.ReadWrite` and copies tokens
  to `calendar.microsoft.*`. Device-flow client id reads vault via `cred()`,
  not env-only. The calendar card's **Connect Outlook Calendar** is its own
  sign-in (POST `/api/calendar/oauth/microsoft/start`, the attempt's
  `purpose=calendar`, the Microsoft callback): it keeps calendar tokens only
  (`store_microsoft_calendar_tokens`) -- the mail sign-in would reconnect a
  mailbox the owner disconnected.
- **A disconnected calendar is OFF** (`calendar.<provider>.off`, 2026-09-28):
  the mail grant covers Calendar, so deleting the calendar's copy left it
  reading through Gmail and the next Gmail refresh wrote the copy back --
  "Disconnect Calendar" changed nothing, and Outlook had no disconnect at
  all. While off, the token readers return nothing and `persist_*_tokens`
  keep nothing; only a sign-in from the calendar card lifts it
  (`turn_calendar_on`, which fails the sign-in if the mark stays). An
  Outlook token refresh updates mail only while mail holds the very grant
  it refreshed; mail and calendar disconnects each clear their own side.
  `tests/test_calendar_disconnect.py` (live shapes, old code fails 9).
- Connector health (`check_connectors`) probes Gmail **and** Calendar
  independently (Testing-mode 7-day expiry). Do not probe Drive as a
  stand-in for Gmail.
- **More accounts than the main two** (`email_manager/accounts.py`,
  2026-09-29): Settings → Email → Other accounts adds Google and Microsoft
  accounts by sign-in (`?account=<name>` on the start routes; state carries
  it; `prompt=select_account`) and IMAP/app-password ones after a tried
  login. The list is the setting `email.accounts`; secrets are
  `email.account.<name>.*` (install-scoped). Chat names an account by name
  or address (`accounts.resolve_account`, for mail and calendar;
  `email_accounts` lists them). **Every token write names its account:** a
  backend with a refresh takes `account_alias` and an extra account's
  refresh writes its own keys only (`persist_account_tokens`,
  `refresh_*_access_token(persist=False)`). Until then the .env-only
  version's refresh wrote a second account's tokens over the MAIN account's.
  Gate: `tests/test_email_extra_accounts.py::test_every_refreshing_backend_takes_an_account`
  plus the per-backend isolation test (old Graph code as the control).
- **A refreshed sign-in is kept only where it is still held**
  (`email_manager/refreshed_grants.py`, 2026-09-29): a refresh writes its
  new tokens to each place holding the grant it refreshed -- compared by
  refresh token, read env-first as the backends read it: Gmail and Google
  Calendar, the Microsoft mailbox and Outlook Calendar, an extra account
  (`persist_account_tokens(replaces=)`, a required keyword) -- and nowhere
  else. The Gmail refresh (run by every connector-health pass) copied its
  grant over a calendar signed in as another Google account, and every
  refresh undid a disconnect or a new sign-in made while it ran. The
  Microsoft mail refresh asks `openid profile` too (the bare mail scopes
  when refused) and records the address its id_token names; its narrowed
  token is kept for mail only. Gate: `tests/test_refreshed_grants.py`
  (every refreshing backend, enumerated from source, main and extra, with
  nothing / a disconnect / a new sign-in in between).

Tests: `tests/test_calendar_connector.py`, `tests/test_connector_health.py`,
`tests/test_email_extra_accounts.py`, `tests/test_refreshed_grants.py`,
`tests/test_calendar_disconnect.py`, `tests/test_calendar_card_providers.py`.

