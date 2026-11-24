<!-- AGENTS.md, piece 48-live-chat-settings. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
### 48. A saved chat-app setting reaches the running adapter (`kazma_gateway/chat_adapters.py`, 2026-10-01)

Live 2026-10-01: the owner revoked the old Slack app-level tokens and saved a
new one. The running adapter kept the revoked token and retried with
`invalid_auth`, and the Test, run right after the save, said "Kazma's Slack
connection is down right now" for a token that worked. Only a separate
Refresh applied it, and that Refresh rebuilt the adapters with its own copy
of the boot code. The copy had drifted: it started a platform switched off,
dropped Telegram's webhook secret and the allow-all posture, and sat out a 5 s
stop grace per adapter. Boot read the on/off switch from kazma.yaml, so the
Settings switch did nothing anywhere, and the swarm bus kept the bot token and
channel it booted with.

- **One builder.** `ChatAdapters` builds Telegram, Discord and Slack and the
  swarm bus's senders at boot (`build()`), on Refresh
  (`apply(force=True)`) and on a saved setting (`apply()`). Nothing else
  constructs an adapter or a sender: `tests/test_chat_adapters_follow_settings.py`
  scans the product code (git-listed) for one, with a negative control.
  Settings come from the store with the environment as the token fallback,
  and the switch `connectors.<platform>.enabled` is read from the store.
- **The settings store announces every write.** Each mutator of both stores
  carries a change-notice marker (`_announces`: the keys written, or None
  for "any": a raw transaction, an import, a reset), and the notice runs
  after the commit, outside the lock, where a listener's failure is logged
  and never fails the write. `add_change_listener` returns the call that stops
  it. The gate is in the same test file: every method that passes
  `refuse_write` or implements a protocol mutator must announce, unless it is
  declared with its reason. A new store write path therefore cannot skip the
  gateway.
- **What a change does.** `ChatAdapters` listens for `connectors.<chat
  platform>.<field>` keys and applies them half a second after the last write
  (a save sets five or six keys), on the loop, in the background:
  - an adapter field (`ADAPTER_FIELDS`: switch, token, app token, webhook
    secret) rebuilds that platform's adapter through
    `GatewayManager.replace_adapter`. The old adapter is halted first
    (`BaseAdapter.halt`: cancel now; `stop()` waits for a shutdown event a
    replacement never sets) and the others stay connected;
  - an allowlist field is applied to the running adapter in place;
  - a bus field (switch, bot token, destination) rebuilds that platform's
    sender, and an unchanged sender is kept with the approvals it is waiting
    for.
- **The Test applies before it tests.** It calls `apply([platform])`, then
  `first_outcome`, which waits up to 10 s while a fresh adapter has neither
  connected nor recorded a problem. A long-failing adapter is not waited
  for.
- **The Telegram webhook follows the running adapter**
  (`telegram_webhook_router(resolve)`, mounted always, 503 with no adapter).
  A router bound to the boot adapter kept its allowlist and secret after a
  rebuild.
- The page no longer calls `refresh-adapters` after a Save (it restarted every
  adapter a second time). Refresh Gateway remains for "reconnect
  everything". `/api/settings/connectors` (GET, PUT, /test) was a third,
  unused copy of the save and Test, and is gone.

