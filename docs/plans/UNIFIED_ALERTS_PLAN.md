# Unified alerts: one place, one card, one page per problem

Asked for by the owner on 2026-11-06, to follow the resilience plan's items
4-6 (Tavily and the backup model, the UPS and BIOS watch, the chaos drill
and soaks), which are done. Written 2026-11-09.

The owner's words, in short: one "Kazma Alerts" place in Settings for both
senders (Kazma's own bot and Kazma Watch); the same card format from both;
no double pages; observer pairing, the guard's Telegram and test buttons in
Settings; Cloudflare's own alerts routed through the observer, never to
Kazma itself (that would go through the tunnel it is reporting on).

---

## What exists today

Four senders, each with its own settings, format and reach:

| Sender | Runs in | Sends through | Format | Settings |
|---|---|---|---|---|
| Kazma's ops alerts (`observability/ops_alerts.alert`) | the server | the chat apps chosen in Settings -> Adapters & Routes -> "Alerts go to", then Telegram direct | `alert_card`: `🔴 [Ops] Title` + detail | Settings (routes, test button) |
| The start card and lifecycle messages (`lifecycle_notifier`) | the server | the same routes | its own card | Settings -> "Server status messages" |
| The guard's pager (`Notifier` in `kazma_guard.py`) | the guard | Telegram direct, Kazma's bot token, `guard.telegram.chat_id` | a copy of `alert_card` (`🟠 [Guard] ...`) | environment or a settings key; no control, no test |
| Kazma Watch (`deploy/observer`) | Cloudflare | its own Telegram bot (email when set up) | `⚠️ Title / Since ...`, `⏰ Still: ...`, `✅ Kazma is back` | Wrangler secrets; paired from the CLI (`kazma observer pair`); a read-only card in Settings -> System |

Also: run incidents (section 52) go to the chat the work came from, with
buttons; the bell and the Dashboard's open problems list (section 58) show
alerts in the app; the daily digest and the weekly report go through the
ops route.

## What is wrong with it

1. **The same problem is paged twice or three times.** By the current rules:
   - a database outage: Kazma pages `database.away` at five minutes, the
     guard pages `health.dependency_down` when readiness answers 503 (it
     holds back only while Kazma says it watches the database, section 44),
     and Kazma Watch pages `dependency_down` three minutes after its probe
     reads the 503;
   - a critical disk: the guard's disk watch pages, and Kazma Watch pages
     `disk_critical` from the heartbeat;
   - a restart that takes more than three minutes: the guard pages the
     restart, Kazma Watch pages `kazma_restarting`.
   Each sender is right on its own; nothing tells one that another already
   told the owner.
2. **Two formats.** A page from Kazma Watch does not look like a page from
   Kazma, so the owner reads two styles for the same kind of news.
3. **No one place to set or test alerts.** Routes are in Adapters & Routes,
   lifecycle messages beside them, the outside watch in System (read only),
   the guard's chat in a settings key no page shows, Kazma Watch's
   recipients in Wrangler secrets. Only Kazma's route has a test button.
4. **Cloudflare's own alerts go nowhere Kazma sees.** Tunnel health, Access
   and Worker errors are Cloudflare notifications; today they reach the
   account's email at best.

## The design

### One catalog of alerts

`kazma_core/observability/alert_catalog.py` names every alert any sender can
raise: its key, which sender owns it, its severity, its card title (catalog
key, English and Arabic), the page it links to, and the alert that closes it
(the recovery note). Kazma Watch's conditions and the guard's page kinds are
in it too: the guard reads a JSON export of the table (it cannot import
`kazma_core`), and so does the observer (copied into `deploy/observer` at
build, checked by a test). Gate: every `alert(` key in product code, every
guard page kind and every observer condition is in the catalog, and every
catalog entry has a sender that raises it (both directions, planted
mismatches as the negative control).

### One card

The card is the one `alert_card` makes today, with the source `Watch` or
`Cloudflare` added:

```
🔴 [Watch] Kazma cannot be reached
The tunnel answers, the machine does not (no heartbeat for 4 min).
Since 14:02 (12 min) · Open: https://my.kazma.ai/dashboard
```

A reminder starts `⏰ Still:` after the icon; a recovery is `🟢 [Source]
<what> is back: it lasted 12 min.` Times are in the owner's zone
(`cron.timezone`; Kazma Watch is told it, see Settings below). One shared
fixture (`tests/fixtures/alert_card/cards.json`: inputs and the exact text)
is checked by the Python card, the guard's copy and the observer's
JavaScript, so the three cannot drift.

### One page per problem

Each condition has ONE owner, decided by who can see it and still speak:

| Condition | Owner | Why |
|---|---|---|
| A dependency down, the database away, a restart, a critical disk, low power | Kazma or its guard | They see it first and in detail |
| The machine unreachable, the tunnel down, the guard silent, Access refusing the observer | Kazma Watch | Nothing on the machine can say it |
| Cloudflare's own notifications | Kazma Watch | They are about the path to Kazma |

The rules that make it hold:

1. **Kazma and the guard agree first.** The guard already holds back while
   Kazma watches the database itself; the catalog makes that the rule for
   every condition both can see: the one listed as owner pages, the other
   records it. Gate: no catalog condition has two inside owners.
2. **Kazma Watch knows what was paged.** The heartbeat carries the inside
   page receipts: for each open inside incident, its catalog key, when it
   was paged and whether a channel took it (names and times only, no
   text). Kazma Watch stays quiet about a condition the heartbeat says was
   paged and delivered. It pages when:
   - the condition is one only it can see (the table above);
   - the inside page was not delivered within five minutes (a pager that
     fails is itself the problem);
   - the heartbeats stop during an inside incident (it then pages the
     machine, and says the inside incident was open).
3. **Recovery notes come from the sender that paged.** A sender that held
   back sends no recovery.

Tests: the observer's Node suite (pure `step` with heartbeats carrying
receipts, each rule with the old behaviour as its negative control), and the
guard's and Kazma's halves in Python, plus the three live-shaped scenarios
above replayed end to end: one page each.

### Settings -> Alerts

A new Settings tab, "Alerts", replaces the alert parts of Adapters & Routes
and the read-only outside-watch card in System. Sections:

1. **Where Kazma's alerts go**: the routes (Telegram, Telegram group,
   Discord, Slack) as today, and **Send a test alert**.
2. **Server status messages**: the lifecycle events, as today.
3. **The guard's pager**: the bot it uses (Kazma's Telegram bot) and the
   chat (`guard.telegram.chat_id`, editable here), whether its last page
   was taken (from its state file), and **Send a test page**: the server
   writes a test request beside the reload request and the running guard
   sends it with its own notifier, so the test proves the path the guard
   uses when Kazma is down. The answer appears in the page (the guard's
   acknowledgement in its state file).
4. **Kazma Watch**: paired or not; **Pair** from the page (the owner pastes
   the observer's pairing token once; the pairing file is written as
   `kazma observer pair` writes it) and **Unpair**; the condition now, this
   week's uptime and incidents (today's card); **Send a test page** (a new
   signed `POST /v1/test-page`: the observer sends one card through its own
   bot and email, and answers which channels took it); and what Kazma Watch
   should know from Kazma (a signed `POST /v1/settings` it keeps in D1: the
   owner's time zone, the email recipient once email is set up). Secrets
   (the bot token, the email binding) stay in Wrangler.
5. **Notices**: whether info and success cards (a crawl finished, a backup
   recovered) also go to the chats, or only to the bell and the digest.
   Default: as today (they go).
6. **Recent alerts**: one list of the last 7 days from both senders, each
   with its source, severity, when, whether a channel took it and its link
   (Kazma's from the alert log the digest reads; Kazma Watch's from
   `/v1/summary`).

Every control writes a setting a reader uses (the settings-reader gate,
section 54); the tab is in English and Arabic and gets a browser test that
reaches it by the sidebar (pair, test, unpair against a stand-in observer).

### Cloudflare's alerts through Kazma Watch

A new observer route, `POST /v1/cloudflare`, takes Cloudflare's notification
webhooks (Cloudflare signs each with the destination's secret, sent in the
`cf-webhook-auth` header; the route refuses anything else). It maps the
notification to a catalog entry (tunnel health, Access, Worker errors;
anything unknown as a generic `[Cloudflare]` card with its own text, cut),
joins it to the incident Kazma Watch already has open for the same cause
(a tunnel-down notification while its own probe reads 530 is one incident
with two witnesses), and pages through the same incident rules. It is never
sent to Kazma: Kazma is behind the tunnel the notification is about.

The owner's part: in the Cloudflare dashboard, Notifications ->
Destinations -> Webhooks, add `https://status.kazma.ai/v1/cloudflare` with
a secret (I give the exact steps and set the same secret in the Worker);
then choose which notifications to send there.

### Email

When the owner onboards `alerts.kazma.ai` with Cloudflare Email Service
(the week of 2026-11-09, a paid account), Kazma Watch's existing email
channel turns on with `EMAIL_FROM watch@alerts.kazma.ai`. The recipient is
set from Settings -> Alerts (item 4) rather than by redeploying.

Built 2026-11-09: the channel uses Email Service's structured send (the
Email Routing form it had reaches only verified addresses), a binding
allowed to send only as `watch@alerts.kazma.ai`, no email when no
recipient is set, and the test page passes Cloudflare's refusal code on to
Settings -> Alerts, worded in both languages
(`deploy/observer/test/watch_settings.test.mjs`, `tests/test_settings_alerts.py`,
`tests/js/test_settings_alerts.js`).

## Stages

Each stage lands whole, with its gates, tested, pushed and deployed (the
observer redeployed with `npx wrangler deploy` from its deploy folder).

| Stage | What | Depends on |
|---|---|---|
| 1 | The catalog and its gates; the one card and its shared fixture; the observer and the guard switched to it | nothing |
| 2 | One page per problem: page receipts in the heartbeat, the observer's rules, the inside owners | 1 |
| 3 | Settings -> Alerts, with the guard's and Kazma Watch's test pages and pairing from the page | 1, 2 |
| 4 | Cloudflare's notifications through Kazma Watch | 1, 2; the owner's webhook destination |
| 5 | Email | the owner's Email Service onboarding |

## Not in this plan

- Alerts written in the install's language: the cards' titles come from the
  catalog in both languages, so this becomes possible, but the details are
  English text from hundreds of call sites. A later step.
- Quiet hours and per-route severity thresholds beyond the Notices switch.
- Paging services (PagerDuty and the like).
