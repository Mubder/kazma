# The outside observer

Everything that watches Kazma on its own machine — the guard, the
supervisor watch, the ops alerts — goes silent with that machine. A power
cut, a dead network, a machine that went to sleep or a stopped tunnel
silences all of them at once. The observer watches from outside: a
Cloudflare Worker on the kazma.ai account (resilience plan B1).

```text
            every minute                          every minute
 Cloudflare Worker  ── pull ──▶  my.kazma.ai/health/live   (through the tunnel)
 "kazma-observer"   ◀── push ──  the guard's heartbeat     (signed)
 status.kazma.ai        │
        │               └─▶ D1: readings, incidents, uptime
        ├─▶ Telegram ("Kazma Watch", its own bot) and email
        └─▶ status page (behind Access)
 Healthchecks.io ◀── the Worker pings it every run (watches the watcher)
```

## What it concludes

| Pull probe | Heartbeat | Condition | The page says |
|---|---|---|---|
| 200 | fresh | `up` | (nothing) |
| 530 / error 1033 / no answer | missing | `machine_unreachable` | Kazma's machine is not reachable (power, network or sleep) |
| 530 / error 1033 / no answer | fresh | `tunnel_down` | The tunnel is down; Kazma itself is running |
| 502 / 504 | fresh, Kazma not running | `kazma_restarting` | Kazma is down; the guard is restarting it |
| 200, ready 503 | fresh, not ready | `dependency_down` | Kazma is up; a dependency is away |
| 200 | missing | `guard_gone` | The guard stopped; Kazma is unsupervised |
| 200 | fresh, disk critical | `disk_critical` | Kazma's disk is almost full |
| 401 / 403 / Access login | any | `observer_refused` | The observer cannot pass Cloudflare Access |
| any | fresh, maintenance | `maintenance` | (nothing: planned work) |

A heartbeat is missing after 90 seconds (the guard sends one a minute). A
bad condition becomes an incident on its third failed minute: one page,
a reminder every hour while it lasts, one recovery page with how long it
lasted. A page no channel took is tried again the next minute.

## One page per problem

Kazma and its guard see some of these problems first: a dependency or the
database away, a restart, a full disk. Each page they send for an incident
still open travels in the heartbeat as a receipt (its catalog key, when it
was paged, whether a channel took it; never its text). The observer stays
quiet about a condition the heartbeat says was paged and delivered, and
sends no recovery for it: the sender that paged does. It pages anyway when:

- the condition is one only it can see (the machine unreachable, the tunnel
  down, the guard silent, Access refusing it);
- the inside page did not reach anyone within five minutes, or none came
  five minutes after the observer would have paged on its own;
- the heartbeats stop: it pages the machine, and its page names the inside
  pages that were open.

A heartbeat with no receipts at all (a guard older than this) is read as
before: the observer pages what it sees. The rules are `src/receipts.js`.

## What the heartbeat holds

States, numbers and the names of failing readiness checks: the guard's state
and uptime, Kazma's state, why it is not ready, its uptime and commit, the
database's state, Docker's, cloudflared's, the disk's level and free share,
the battery when there is one, and the page receipts (the catalog keys of
the inside pages still open, when, delivered or not). No chat, no content,
no path, no name, no error text: the guard builds the body through an allowlist
(`heartbeat_body` in `scripts/service/kazma_guard.py`) and the Worker cuts
what it receives to the same list again (`sanitize` in `src/diagnose.js`).
It is signed with the install's pairing key; a heartbeat older than five
minutes, or not newer than the last one taken, is refused.

## Signatures

Every request the guard and Kazma sign (the heartbeat, the summary, and
Settings → Alerts' settings, test page and leave) carries
`X-Kazma-Signature: v2=<hex>`, HMAC-SHA256 under the pairing key of

```text
v2\n<timestamp>\n<METHOD>\n<path>\n<body>
```

with `\n` a newline, `<path>` the request line's path (`/v1/heartbeat`) and
`<body>` the raw body (empty for a GET). The route is in what is signed, so a
signature made for one route is refused on another. The older `v1=<hex>`,
over `"<timestamp>." + body`, bound no route, so a heartbeat's signature
replayed to `/v1/leave` unpaired the install. v1 is retired since
2026-11-12 (`ACCEPT_V1 = false` in `src/verify.js`): every signed route
refuses it with `v1_retired`. A guard or Kazma too old to sign v2 is refused
too, and Kazma Watch pages its guard as gone; update that install.

## Setting it up (the owner, once)

**The easy way:** `kazma observer setup` on the machine Kazma runs on
(`kazma_core/observability/watch_setup.py`) does steps 1, 2, 5, 6 and 9 and
pairs the install: it signs in with `wrangler login`, makes the D1 database
and its tables, deploys the Worker to `<name>.<subdomain>.workers.dev` with
no custom domain, no status page and no email, sends the secrets on stdin
(`wrangler secret bulk`), keeps the pairing token in Kazma's vault and pairs.
An install with no public address is watched by heartbeats alone
(`PULL_URL` empty: `heartbeat_silent` when they stop). Its work folder is
`<data dir>/kazma-watch`. The steps below are for everything else: a domain
of its own, the status page behind Access, email, Cloudflare's notifications.

Everything below runs on the owner's Cloudflare account and needs the
owner's credentials; Kazma never holds them.

1. **The database.** From this folder:

   ```bash
   npm install
   npx wrangler d1 create kazma-observer
   ```

   Put the `database_id` it prints into `wrangler.jsonc`, then:

   ```bash
   npx wrangler d1 execute kazma-observer --remote --file=schema.sql
   ```

   Every statement is idempotent: run it again after an update that adds a
   table (one page per problem added `incident_notes`, Cloudflare's
   notifications `cloudflare_notices` and `cloudflare_seen`, Settings ->
   Alerts `install_settings`, which keeps the time zone and email address
   Kazma tells the observer).

2. **The watch bot.** In Telegram, create a bot with @BotFather and call it
   "Kazma Watch" (a bot of its own, so a fault in Kazma's bot cannot silence
   the observer). Send it any message, then read your chat id from
   `https://api.telegram.org/bot<token>/getUpdates`.

3. **Access for the probe.** In Cloudflare Zero Trust, create a service
   token. Add an Access application for `my.kazma.ai/health/*` with a
   Service Auth policy allowing that token (and nothing else on that path).
   Kazma's health paths skip Kazma's own sign-in.

4. **Access for the status page.** Add an Access application for
   `status.kazma.ai` allowing the owner's email, and a second application
   for `status.kazma.ai/v1/*` with a Bypass policy (the guard and
   `kazma observer` cannot sign in to Access; those routes check their own
   signatures and token). Copy the status page application's AUD tag.

5. **The variables.** In `wrangler.jsonc` set `ACCESS_TEAM_DOMAIN` (the
   team's `<team>.cloudflareaccess.com`) and `ACCESS_AUD`. Unset, the status
   page refuses every request.

6. **The secrets:**

   ```bash
   npx wrangler secret put PAIRING_TOKEN
   npx wrangler secret put TELEGRAM_BOT_TOKEN
   npx wrangler secret put TELEGRAM_CHAT_ID
   npx wrangler secret put ACCESS_CLIENT_ID
   npx wrangler secret put ACCESS_CLIENT_SECRET
   npx wrangler secret put HEALTHCHECKS_URL
   ```

   `PAIRING_TOKEN` is any random string of 32 characters or more (for
   example `openssl rand -hex 32`); keep it, `kazma observer pair` asks for
   it.

7. **Watch the watcher.** At healthchecks.io create a check with a 1-minute
   period and a few minutes' grace; its ping URL is `HEALTHCHECKS_URL`. If
   the Worker stops running, Healthchecks emails you. In Cloudflare, turn on
   the Tunnel health notification too: a second, independent signal for the
   tunnel.

8. **Email (optional; Cloudflare Email Service, Email Sending, on Workers
   Paid).** Send from a subdomain of its own, so kazma.ai's own mail records
   are never touched: in the dashboard, **Compute -> Email Service -> Email
   Sending -> Onboard Domain**, choose `alerts.kazma.ai`. Cloudflare adds its
   sending records to that subdomain only (a bounce MX and SPF on
   `cf-bounce.alerts.kazma.ai`, a DKIM key, and a DMARC record for the
   subdomain if it has none); wait until it shows as verified. Then in
   `wrangler.jsonc` keep the `send_email` binding (`EMAIL`, allowed to send
   only as `watch@alerts.kazma.ai`) and `EMAIL_FROM`, and deploy. The
   recipient is the address in Kazma's Settings -> Alerts -> Kazma Watch
   ("Email the pages to", then "Tell Kazma Watch"); `EMAIL_TO` is only a
   fallback for an install that told none. Settings' "Send a test page" says
   whether the email went, and if Cloudflare refused it, its reason
   (`E_SENDER_NOT_VERIFIED`: the subdomain is not verified yet).

9. **Deploy:**

   ```bash
   npx wrangler deploy
   ```

10. **Pair the install.** On the machine Kazma runs on:

    ```bash
    kazma observer pair
    ```

    It asks for the pairing token (or reads `KAZMA_OBSERVER_PAIRING_TOKEN`),
    registers the install, and writes `<install>/.kazma/observer.key`,
    readable by this account only. The guard sends its first heartbeat
    within a minute. `kazma observer status` shows what the guard and the
    observer see; Settings → Alerts → Kazma Watch shows the same, and can
    pair (and unpair) from the page too.

## Cloudflare's notifications

Tunnel health, Access and Worker errors are Cloudflare's own notifications.
They come to the observer, never to Kazma: Kazma sits behind the tunnel they
report on. The observer turns each into a card with the source Cloudflare
(the same card as every other page), or adds it to the incident it already
has for the same cause: a "tunnel down" notice while its own probe reads the
tunnel's 530 is one incident with two witnesses, and its page says
"Cloudflare says so too". Anything it does not know is paged as "A Cloudflare
notification" with Cloudflare's own text, cut short. A notice already taken
(a retry, a replay) is never paged twice, and at most twenty an hour are.

The owner's part, once:

1. **The secret.** Make one of 32 or more random characters, for example
   `openssl rand -hex 32`, and give it to the Worker from this folder:

   ```bash
   npx wrangler secret put CF_WEBHOOK_SECRET
   ```

   Until it is set, `/v1/cloudflare` answers 503 and pages nothing. (The
   `/v1/*` paths already bypass Access, step 4 below.)

2. **The destination.** In the Cloudflare dashboard: Notifications ->
   Destinations -> Webhooks -> Create. Name: `Kazma Watch`. URL:
   `https://status.kazma.ai/v1/cloudflare`. Secret: the same string as
   step 1 (Cloudflare sends it in the `cf-webhook-auth` header on every
   request; a request without it is refused). Save and test: the test
   message arrives in Telegram as "A Cloudflare notification".

3. **What to send there.** Notifications -> All Notifications -> Add, and
   choose the `Kazma Watch` webhook as the destination of:
   - **Tunnel Health Alert** for the tunnel that serves Kazma (down,
     degraded and healthy: healthy closes the card);
   - **Tunnel Creation or Deletion Event**;
   - **Expiring Access Service Token Alert** (the observer's own service
     token; when it expires the observer can no longer pass Access) and
     **Access mTLS Certificate Expiration Alert** if you use one;
   - **Workers observability alerts** on the `kazma-observer` Worker's
     errors, if your plan has them.

   Anything else you route there is paged as the generic card.

## Problem reports

Kazma's **?**, `kazma report` and `/report` send to `POST /v1/report` here by
default (`src/reports.js`, Kazma's `kazma_core/support/problem_report.py`):
JSON of kind `kazma-problem-report`, at most 64 KB, five an hour from one
address and 300 a day in all, kept 180 days. The owner is told on Kazma
Watch's channels (at most 30 a day). `GET /reports` lists them and
`/reports/<number>` shows one, behind the same Access application as the
status page (it covers everything but `/v1/*`) and the Worker's own check of
its token. A sender's address is kept only as an HMAC, to count. The
`problem_reports` table is in `schema.sql`: apply it with
`npx wrangler d1 execute kazma-observer --remote --file=schema.sql` before
deploying the Worker that reads it.

## Tests

```bash
npm test
```

The Worker's handlers run in Node against a D1 stand-in over Node's own
SQLite (`node:sqlite`, Node 22.5 or newer), so `schema.sql` and every
statement run as written; the network is a fake. Kazma's test suite runs
them too (`tests/test_outside_watch.py`), and the guard's and Kazma's
signers are held to the same signature vector
(`tests/fixtures/observer/heartbeat_vector.json`, its `v2` routes).

## Storage and privacy

D1 keeps one reading a minute for 30 days, hourly summaries for 400 days and
incidents for good; about 7,000 writes a day, inside D1's free allowance.
The observer reads the owner's own install and stores no visitor data.
`kazma observer unpair --forget` deletes everything it kept about the
install.
