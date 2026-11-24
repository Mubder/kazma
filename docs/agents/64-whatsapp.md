<!-- AGENTS.md, piece 64-whatsapp. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
### 64. WhatsApp (`kazma_gateway/adapters/whatsapp.py`, 2026-11-14)

The fourth chat app, through Meta's WhatsApp Cloud API. It is built by the one
builder (`chat_adapters`, §48) like the others, from Settings only (no
environment fallback): `connectors.whatsapp.token`, `.phone_number_id`,
`.app_secret`, `.verify_token`, `.api_version`, `.allowed_users`, `.enabled`.
The three credentials are install-scoped in the vault
(`vault.INSTALL_SCOPED_SECRETS`, `cfg:connectors.whatsapp.`).

- **Meta calls Kazma; nothing polls.** The webhook
  (`whatsapp_webhook_router`, mounted at `WEBHOOK_PATH` =
  `/api/webhooks/whatsapp`, open in `auth.ALWAYS_OPEN_PREFIXES`) answers GET
  with the challenge only for the saved verify token (compared in constant
  time), and POST only with Meta's signature: `whatsapp_parse.signature_ok`
  checks `X-Hub-Signature-256` (HMAC-SHA256 of the raw body under the app
  secret) before the body is parsed. No app secret saved: 503 to every call
  (fail-closed); a body past 1 MiB: 413. It answers 200 at once and the
  adapter reads each sender's messages in order behind it (`_chains`), so a
  slow download or transcription never makes Meta resend; a resend is counted
  (`duplicate`) and never handed on twice, and the first copy's fate stays
  under its id. `settled()` waits for what was taken (tests).
- **Every message a person sent is counted, then judged** (§42): a message to
  another number of the account (`not_this_number`), the sender rule
  (`SenderGate`; a stranger is told once a day, `sender_rule.private_notice`
  names the number and "Allowed numbers"), a kind Kazma does not read
  (`unsupported_type`), a file it could not download (`media_failed`).
  WhatsApp's reasons word the shared refusals by its own field
  (`whatsapp_receive.WHATSAPP_REASONS`).
- **A number is a user id in any spelling**: `wa_digits` (the adapter) and
  `allowlists._platform_user_id` (owner ids, `KAZMA_GATEWAY_ADMINS`
  `whatsapp:+965...`, the admin check) turn "+965 9999 1234" into the
  `wa_id` WhatsApp sends. The exact-text rule the other platforms keep would
  not match it (the negative control in `tests/test_whatsapp.py`).
- **Calls to Meta** (`whatsapp_api.WhatsAppClient`): one httpx client, no
  redirects, the token in the Authorization header only; a send is retried
  only after a 429 (a 5xx may have delivered it), a read after a 5xx too;
  downloads capped (`MAX_DOWNLOAD_BYTES`); `explain` words the codes the
  owner can act on (131047 the 24-hour window, 131030 the test number's
  recipients, 190 the token). What a call can fail with is
  `_META_FAILURES`; no handler catches more.
- **Sending**: `md_to_whatsapp` (WhatsApp's marks, code untouched) and
  `split_for_whatsapp` (4,096 characters, a fenced block kept whole when it
  fits); files are uploaded and sent as media; each part reports to the chat
  outbox (`send_report`). Links never get a preview.
- **Buttons are one table for every chat app** (`adapters/markup.py`):
  `MARKUP_KEYS` names each platform's send-context field (WhatsApp's is
  `whatsapp_buttons`), `attach_markup` puts a markup there, and
  `approval_markup` / `semantic_markup` build the approval card's and a
  clarify question's buttons. The approval card (`agent_handler/hitl.py`,
  both card shapes and the resume path) and the gateway's in-turn card
  (`agent_handler/graph.py`) and the run supervisor's cards (`run_cards.py`)
  ask it; they named three fields at each site before. A new chat app is a
  row there. A press is the shared callback scheme
  (`platform_callbacks.parse_callback_data`); approvals, swarm and run
  buttons need a gateway admin (`is_gateway_admin`), as on every platform.
  `agent_handler/store.py` keeps `whatsapp_buttons` and `blocks` out of the
  stored session context.
- **No swarm sender**: WhatsApp lets a business message a person only within
  24 hours of their last message (templates after that), so a swarm card or
  reminder could not reliably reach the owner there (`BUS_FIELDS["whatsapp"]`
  is empty and `_build_swarm_sender` returns None for it).
- **The Test** (`whatsapp_diagnose.diagnose`, `POST
  /api/connectors/whatsapp/test`): Meta's answer for the number, the app
  secret, the webhook address (`KAZMA_PUBLIC_URL` + `WEBHOOK_PATH`, also the
  connector list's `endpoint`, which the card shows) and whether Meta called
  it, the last message's fate, the 24-hour window, allowed numbers, the
  connection. `connector_test.allowlist_check` takes the field's name
  (`field`, `id_word`). The card-title gate finds every `*_diagnose.py`
  (`tests/test_telegram_slack_receive_and_test.py`), so a new chat app's
  checks need titles the day it is added.
- Gate: `tests/test_whatsapp.py` (a stand-in Meta behind
  `httpx.MockTransport` and the real router, adapter, builder, routes and
  Test; an unsigned call, another secret's signature, a resend, a stranger,
  a non-admin's button and the old admin rule as negative controls). Not
  verified against Meta's real servers until the owner's first live test.

