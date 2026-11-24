<!-- AGENTS.md, piece 42-chat-apps. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
### 42. Chat apps: every message accounted for, a Test that diagnoses (`kazma_gateway/receive_log.py`, `connector_test.py`, `adapters/*_diagnose.py`)

Live 2026-09-29: every reconnect Discord asked for ended the session (5 of 5
in one night), no Discord message had reached Kazma in eight days of logs,
and Settings' Test said "Connected". The owner then asked for the Discord
Test on every adapter; Telegram and Slack had the same blind spots.

- **Every adapter keeps a `ReceiveLog`** (`kazma_gateway/receive_log.py`):
  events by type, connection state and its last problem, and each message's
  fate by id -- handed on, or dropped with a reason (the adapter's
  `*_REASONS` = `COMMON_REASONS` + its own). `ReceiveLog.log_drop` is the one
  rule for saying it (a setting to fix: WARNING, throttled; a bot: DEBUG;
  else INFO). A new way for an adapter to leave a message needs a reason and
  a record, or the "unaccounted for" gates fail. Each adapter exposes
  `diagnostics()`; the Test reads it through the service container's
  gateway (`providers._live_adapter_diagnostics`).
- **Each Test is a diagnosis** built from `connector_test` (`Checks`,
  `judge_message`, `listening`): Discord's below; Telegram's (token, webhook
  vs polling and waiting updates, group privacy, delivery chat, group route,
  the last message a person sent -- Telegram lets a bot read no history --,
  allowed, connection); Slack's (token, app-level token, the token's scopes,
  delivery channel and membership, latest message there and in each allowed
  user's DMs, allowed, connection). The card renders every platform's checks
  from one macro, `connector_checks`, with a title per key
  (`connectorCheckTitle`) and links only through `connectorLinkOk`
  (discord.com, slack.com).
- **Slack's Socket Mode events go through `_accept_event`** (dedupe, channel,
  allowlist -- the old inline order); the polling path records too; a taken
  message is logged at INFO ("Enqueued from …"), as on Telegram and Discord.
- **Slack's control messages say why** (`_on_hello`, `_on_disconnect`,
  2026-09-30): a `disconnect` is logged with its reason and Slack's host
  (one boot reconnected ten times in 30 s with no reason in the log);
  `link_disabled` (Socket Mode switched off, or the app-level token the
  connection used revoked -- a revoke sent it live on 2026-10-01) is a
  WARNING and the Test's problem, with the fix. `hello`'s `num_connections`
  is logged, and kept
  for the Test when taken 15 s or more after Kazma's previous connection
  ended (`_SETTLED_AFTER_S`): Slack hands each event to ONE connection, so
  a second program connected to the app silently takes some of Kazma's
  messages -- a WARNING. But the count also includes a connection that ended
  without closing, until Slack notices (live 2026-10-01: 2 for hours on a
  token used nowhere else, every message arriving), so the warning names
  both, and the Test's `listening` check decides from the messages it
  checked (`slack_diagnose._reached`): one that never reached Kazma, yellow
  with "another program"; all reached, green, "a connection that ended
  without closing". **`too_many_websockets` is a refusal** (2026-10-03):
  the app already holds Slack's limit of 10 connections, so Kazma is not
  connected until a slot frees. The first refusal of a run is a WARNING and
  the connection's problem, and the hello that ends the run says how many
  there were. A count taken inside the settle window is kept when it shows a
  connection besides Kazma's own two. Two reloads on 2026-10-02 were refused
  4 and 9 times and then counted 10, and that rule kept nothing, so nothing
  showed above INFO.
- **The gateway's flood guard accounts for what it leaves**
  (`BaseAdapter.note_left_unanswered`, 2026-10-01). Past
  `gateway.rate_limits` (messages per person per minute, from the settings
  store; 30 on every platform -- the old Discord 5 and Slack 1 were the
  platforms' own send limits and held the owner to one Slack message a
  minute, retired in `config_defaults`) the message is recorded
  `rate_limited` under the id its adapter gave it (`context_metadata
  ["receive_key"]`, set where the adapter hands it on) and logged by the
  record's rule; it used to vanish with nothing recorded.
  `tests/test_chat_adapters_follow_settings.py` (a floor on the shipped
  limits, with the old values as the negative control).

- **A socket whose session will be resumed closes with 4000**
  (`_close_for_resume`, after op 7 and a resumable op 9). Leaving
  `async with websockets.connect(...)` closes with 1000, which Discord reads
  as the end of the session; Resume goes to READY's `resume_gateway_url`.
  Shutdown alone closes with 1000. `tests/test_discord_gateway_resume.py`
  (its fake Discord applies both rules).
- **Every MESSAGE_CREATE goes through `_accept_message`**: handed on, or
  dropped with a reason from `discord_receive.DROP_REASONS` -- recorded by
  message id and logged (a setting problem as a WARNING, throttled; a bot's
  message at DEBUG). `drop_reason` mirrors every refusal of
  `parse_message_create`. A new way to leave a message needs a reason.
- **The Test is a diagnosis** (`discord_diagnose.diagnose`, from
  `/api/connectors/discord/test`): token, Message Content Intent (app
  flags), servers, delivery channel, the newest message a person wrote there
  (who and when, never the text) and whether it reached Kazma (the live
  record, `DiscordAdapter.diagnostics()`, through the service container's
  gateway), each allowed user's newest direct message to the bot (the DM
  channel is opened with `POST /users/@me/channels`, which sends nothing --
  the Test's only write), allowed users, connection. The card lists every
  check.
- **Guild ID** (`connectors.discord.guild_id`) limits server messages
  (`set_allowed_guilds`, boot and `apply_adapter_allowlists`).
- **A check may carry a `link`** (the DM check: the one conversation between
  the user and this bot); the card renders only `https://discord.com/` links.
- **A "💡" tool hint follows what the agent did** (`gateway._tool_hints`,
  2026-10-20): sent only with `gateway.suggestions.enabled` on (it was never
  read) and only after a turn that used no tool -- the handler records the
  turn's tools (`context_metadata["turn_tools"]`). "I can read and summarize
  that" came under a swarm's summary. Gate: `tests/test_gateway_tool_hints.py`.
- **An example's placeholder is no token** (2026-11-07). setup copies
  `.env.example` to `.env`, and it shipped
  `TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here`: every fresh install
  started Telegram with it and logged "Bot token validation failed" at every
  start (the drill VM's only errors besides npx). The example sets no
  credential now, and the chat apps' token variables
  (`env_files.CHAT_TOKEN_VARIABLES`) are read only through
  `env_files.env_credential`, which treats a placeholder (`your_...`,
  `..._here`, `<...>`, `changeme`) as unset and says so once. Gate:
  `tests/test_env_placeholders.py` (the shipped line and a direct read as
  negative controls).
- **No chat app shows a markdown table**: `GatewayManager.send` rewrites
  tables outside code fences as lines (`kazma_gateway/chat_tables.py`) for
  Telegram, Discord and Slack alike. `tests/test_chat_tables.py`.
- **A file sent with words reaches the agent with both** (2026-11-01). Live,
  a .docx sent on Telegram with the caption "Keep this file as reference"
  never arrived: Telegram puts a file's words in its `caption`, and the text
  parser read a captioned message as plain text before the file path ran.
  `TelegramAdapter._read_message` reads a voice note, then a file or photo,
  then text; the caption is the file's text, and the voice, file and text
  paths share `telegram_parse.message_metadata` (the file path had dropped a
  forum topic's thread). An edit is a `duplicate`, never a new turn. On
  Slack a shared file is subtype `file_share`, which every path left as "not
  a new message": `slack_parse.is_new_message` is the one rule, asked by the
  parser, the receive record, the polling path and the Test. Gate:
  `tests/test_chat_files_reach_the_agent.py` (the parse-first order and the
  old subtype rule as negative controls; no Slack module reads `subtype`
  itself).
- **A chat app with an empty allowlist is private** (2026-11-11, the owner's
  decision; `kazma_gateway/sender_rule.py`). Every adapter is a
  `sender_rule.SenderGate` and every accept path -- messages, the polling
  path, buttons, native commands -- asks its `sender_refusal`; nothing else
  reads `_allowed_users` or `_allow_all`. A set allowlist decides alone;
  empty, only the owner's own accounts Kazma already knows pass
  (`allowlists.known_owner_ids`: a positive Telegram chat id in
  `allowlists.OWNER_ID_KEYS`, a `platform:id` entry of
  `KAZMA_GATEWAY_ADMINS`), and they are admin too (`is_gateway_admin`); the
  rest are `no_allowlist`. A refused sender in a DIRECT conversation is told
  once a day per sender per platform, in their language, that the bot is
  private and their id (`SenderGate.tell_refused_sender`; the record is the
  runtime settings key `sender_rule.NOTICE_KEY`, so a restart does not tell
  them again); never in a group or channel. `KAZMA_GATEWAY_OPEN_TO_EVERYONE`
  (`boot_guard.env_flag`) lets everyone in again, warned at every boot;
  `KAZMA_GATEWAY_STRICT_ALLOWLIST` set too wins. Each build logs one WARNING
  per adapter with an empty allowlist naming the fix, and the Test's
  `allowed` check shows it as a problem (`connector_test.allowlist_check`).
  The owner's settings are watched (`chat_adapters._watched_key`) and
  re-applied in place. Gate: `tests/test_chat_apps_private_by_default.py`
  (the real accept paths; a planted second rule and the old builder's
  open default as negative controls).
- Gates: `tests/test_discord_receive_and_test.py`,
  `tests/test_telegram_slack_receive_and_test.py` (nothing a connection reads
  unaccounted for, with negative controls; each drop reason at its level;
  each check of each Test; a title on the card for every check key).

