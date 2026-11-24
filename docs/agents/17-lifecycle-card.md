<!-- AGENTS.md, piece 17-lifecycle-card. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
### 17. Lifecycle Status Notifier (`kazma-core/kazma_core/lifecycle_notifier.py`)

Server lifecycle status notifications — ONE card per boot (2026-09-29, the
owner's request: a reload sent three messages, and the restart card named the
adapters without saying whether any had connected), plus a card when startup
fails. The start card waits for the chat apps and says how long Kazma was
down (or that the last run did not shut down cleanly), each adapter ✅ or ❌
with the connection's reason, the build and the model. Four invariants:

**A. Notifications route through the SwarmMessageBus — no parallel path.**
`notify_lifecycle(event)` sends a `BusMessage` through the adapters
selected by `notifications.ops.channels` (same Settings checkboxes as
ops alerts; empty = every configured platform). `bus_send_targets()`
filters a `FanOutBusAdapter`; `telegram-group` is not a bus adapter and
uses the ops-alerts group route. Do NOT construct new adapters or
introduce a second recipient config. `NullBusAdapter` (no platform
configured, or under pytest, where `app.py` wires no platform sender) silently drops
the message; the feature self-disables.
The bus adapters are standalone `httpx` clients independent of
`gateway.start()`/`stop()`, so notifications work during early startup
(before the inbound poller is up) and late shutdown (after `gateway.stop()`,
which tears down inbound adapters, not the bus).

**B. Four sites in `app.py`, each in its own try/except — a notification
must NEVER break boot or shutdown.**
- `_on_startup()` top → `notify_lifecycle("starting")`: RECORDS the boot
  (`_record_boot`: reads how the last run ended, then stamps
  `system.lifecycle.last_boot_epoch`); announced only when switched on.
- `_on_startup()` end → `spawn_background(announce_started(
  gateway.connection_report, model=, build=get_build_info()["commit"]))`.
  In the background because it waits (up to `CONNECT_WAIT_S`, 45 s; live
  the adapters connect ~4 s after the gateway starts) for every adapter to
  connect or fail; it gives up when Kazma begins shutting down.
- the gateway-start failure `except` → `startup_failed` (the error as
  `detail`).
- `_on_shutdown()` top → `notify_lifecycle("shutting_down")`: RECORDS the
  stop (`system.lifecycle.last_shutdown_epoch`); announced only when on.
A shutdown stamp at or after the previous boot's = the last run stopped
cleanly ("Down for 34.8 s"; "restarted" within `restart_window_seconds`,
default 60, `0` = no detection); one before it = it did not ("The last run
did not shut down cleanly"). The markers are written whether or not the
event is announced — never gate a stamp on the events list. The downtime
runs to the END OF STARTUP (`announce_started` takes the time before it
waits), never to the send: Slack's 30 s of reconnects turned a 35 s reload
into "started" (live 2026-09-30,
`test_the_wait_for_the_adapters_is_not_downtime`).

**C. An adapter's connection is what its connection said.**
`BaseAdapter.connection_state()` reads the adapter's `ReceiveLog` (§42):
`connected`, `connecting` (with the last problem), `down` (task never
started or ended), `running` (no record). `GatewayManager.connection_report()`
is what the card polls, and `get_status()` (the Gateway Monitor, `kazma
gateway status`) maps the same states -- it used to say "connected" for any
running task.

**D. Config is live-re-read and has a control.**
`get_lifecycle_config()` mirrors `get_hitl_config`/`get_proxy_provider`,
never raises, and is blocking (async callers use `to_thread`). Events
default to `[started, startup_failed]`; an EMPTY list means every message
off; unknown names are logged and ignored (all-unknown = the defaults).
Settings → Alerts → Server status messages writes
`notifications.lifecycle.events` (`PUT /api/settings/alerts`, §33); `PUT /api/settings/single` refuses a name
Kazma does not send (`parse_lifecycle_events`). The old four-event default
pinned in every install's database is retired through §8's
`RETIRED_DEFAULTS`. Tests: `tests/test_lifecycle_start_card.py`.

