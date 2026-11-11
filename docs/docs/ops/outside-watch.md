---
title: Outside Watch
---

# Outside watch

> This page is published at [kazma.ai/docs/ops/outside-watch/](https://kazma.ai/docs/ops/outside-watch/) ([Arabic](https://kazma.ai/ar/docs/ops/outside-watch/)).

Kazma's guard restarts a server that stops, and pages you when it cannot.
But the guard runs on the same machine as Kazma. When the machine loses
power, its network, goes to sleep, or its tunnel stops, the guard goes
silent with it, and nobody is told.

The outside watch is a small service that runs somewhere else — a
Cloudflare Worker on your own account — and watches the install from
there. It pages you through a Telegram bot of its own, so a fault in
Kazma's own bot cannot silence it either.

## How it tells what broke

Every minute it does two things:

- **It asks the site from outside.** It opens your install's health page
  through the tunnel, the way a browser would.
- **It reads the guard's heartbeat.** Once paired, the guard sends it a
  short signed message every minute: whether Kazma is running and ready,
  the database, Docker, the tunnel's connector, free disk space and, on a
  machine with a battery or a UPS, the power.

Together the two say what happened:

| What it sees | What it tells you |
|---|---|
| The site answers, the heartbeat arrives | Everything is up |
| The site does not answer, no heartbeat | The machine is not reachable: power, network or sleep |
| The site does not answer, the heartbeat arrives | The tunnel is down; Kazma itself is running |
| The tunnel answers but Kazma does not, the heartbeat says Kazma is down | Kazma is down; the guard is restarting it |
| The site answers but is not ready | Kazma is up; a dependency (the database, Docker) is away |
| The site answers, no heartbeat | The guard stopped; Kazma is unsupervised |

A problem has to last three minutes before you are paged, so a restart or
a short network hiccup pages nobody. You get one message when it starts, a
reminder every hour while it lasts, and one message when it is over that
says how long it lasted. Planned work — a `kazma update`, or the guard
paused for maintenance — pages nothing.

## What it is sent

Only states and numbers: no chat, no files, no paths, no names, no error
text. The guard builds the message from a fixed list of fields and signs it
with a key only this install holds; the observer refuses a message that is
not signed with it, is more than five minutes old, or was already received.
The signature covers the address it was sent to as well as the message, so
a signed request is refused at any other of the observer's addresses (see
`deploy/observer/README.md`, "Signatures"). When Kazma and the observer are
both updated, deploy the observer first, then Kazma.

## Setting it up

The observer runs on your Cloudflare account, so setting it up needs your
Cloudflare, Telegram and (optionally) Healthchecks.io accounts. The steps,
from creating its database to deploying it, are in
`deploy/observer/README.md`. Then pair this install, in **Settings → Alerts
→ Kazma Watch** (paste the pairing token you gave the observer and press
**Pair**), or on the machine Kazma runs on:

```bash
kazma observer pair
```

Either way the token is used once and never kept: Kazma registers this
install and writes the pairing key into Kazma's own folder, readable by your
account only, then tells the observer your time zone (Settings → Memory's
reminders zone), so its messages give times in it. Within a minute the guard
is sending heartbeats.

To see what it sees:

```bash
kazma observer status
```

The same is in **Settings → Alerts → Kazma Watch**: the last heartbeat the
observer took, what it concludes now, this week's uptime and incidents, and
the time zone its messages use. **Send a test page** has the observer send
one message through its own bot (and email, once set up) and says which
took it; **Tell Kazma Watch** sends it the time zone again and the email
address to page. The daily digest gains a line too ("Outside watch: up
99.9 % this week, 1 incident (machine not reachable, 14 min)").

To stop it, press **Unpair** there (tick "Also delete" to delete everything
the observer kept about this install), or:

```bash
kazma observer unpair --forget
```

The page needs no token for these: each request is signed with the
install's own pairing key (`POST /v1/settings`, `/v1/test-page`,
`/v1/leave`). An observer deployed before them needs `schema.sql` applied
once more (it adds the `install_settings` table) and `npx wrangler deploy`.

## The status page

`status.kazma.ai` (behind Cloudflare Access: you only) shows the condition
now, the latest heartbeat, 90 days of uptime bars, every incident with its
cause and length, and the last day's response times.

## Who watches the watcher

The observer pings Healthchecks.io every minute; if the pings stop,
Healthchecks emails you. Cloudflare's own tunnel health notification is a
second, independent signal for the tunnel.

## The guard's settings

Set in the guard's environment (the `KazmaAgent` task), not `.env`:

| Variable | Default | What it does |
|---|---|---|
| `KAZMA_GUARD_OUTSIDE_WATCH` | `auto` | `auto` sends heartbeats once paired; `off` never does |
| `KAZMA_GUARD_OUTSIDE_WATCH_URL` | the paired observer's | Where heartbeats go (https only) |
| `KAZMA_GUARD_DISK_WATCH` | `auto` | The guard's own free-space check, which also pages from the machine |
