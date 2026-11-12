// One page per problem (the unified alerts plan, Stage 2) and Cloudflare's
// notifications (Stage 4). Run: node --test test/ (Node 22.5+).
//
// Stage 2: ``step`` is pure, so each rule is shown on a run of minutes with
// heartbeats carrying page receipts, and its negative control is the same
// run read the old way (a heartbeat with no receipts: Kazma Watch pages what
// it sees). Stage 4: the route through ``handle`` against the D1 stand-in.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import { d1 } from "./d1_standin.mjs";
import { handle, runMinute } from "../src/index.js";
import { OPEN_AFTER_S, settle, step } from "../src/incidents.js";
import { decide, INSIDE_GRACE_S, sanitizePages, sameCause } from "../src/receipts.js";
import { cut, mapNotification, PAGES_PER_HOUR } from "../src/cloudflare.js";
import { sanitize } from "../src/diagnose.js";
import { CATALOG } from "../src/catalog.js";

const here = dirname(fileURLToPath(import.meta.url));
const VECTOR = JSON.parse(readFileSync(
  join(here, "..", "..", "..", "tests", "fixtures", "observer", "heartbeat_vector.json"), "utf8"));

const T = 1_800_000_000 - (1_800_000_000 % 3600);
const away = (at, delivered = true) => ({ key: "database.away", paged_at: at, delivered });

/** Minutes of ``step`` from up; ``diagAt(now, i)`` says each minute. */
function run(minutes, diagAt, { delivered = true } = {}) {
  let prev = { condition: "up", detail: null, bad_since: null, incident: null };
  const pages = [];
  const closed = [];
  for (let i = 0; i < minutes; i += 1) {
    const now = T + i * 60;
    let { state, pages: out } = step(prev, diagAt(now, i), now);
    if (state.closed) closed.push(state.closed);
    for (const p of out) {
      pages.push({ ...p, at: now, i });
      state = settle(state, p, delivered, now);
      if (state.closed) closed.push(state.closed);
    }
    prev = { condition: state.condition, detail: state.detail, bad_since: state.bad_since, incident: state.incident };
  }
  return { pages, closed, state: prev };
}

const dependency = (pages, extra = {}) => ({ condition: "dependency_down", detail: "database", pages, ...extra });
const up = (pages = []) => ({ condition: "up", pages });

// -- Stage 2: the receipts --------------------------------------------------------

test("the receipts are cut as the guard cuts them (the shared vector)", () => {
  assert.deepEqual(sanitizePages(VECTOR.pages.in), VECTOR.pages.out);
  assert.deepEqual(sanitize({ pages: VECTOR.pages.in }).pages, VECTOR.pages.out);
  // Control: what came in carries a title, a path and a run-time name.
  const raw = JSON.stringify(VECTOR.pages.in);
  assert.ok(raw.includes("CANARY") && raw.includes("someone") && raw.includes("deepseek"));
  // A heartbeat with no receipts at all (an older guard) is null, not [].
  assert.equal(sanitize({}).pages, null);
  assert.deepEqual(sanitize({ pages: [] }).pages, []);
});

test("a condition Kazma paged and delivered is held: no page, no recovery", () => {
  const held = run(25, (now, i) => (i < 20 ? dependency(i >= 2 ? [away(T + 120)] : []) : up()));
  assert.deepEqual(held.pages, []);
  assert.equal(held.closed.length, 1);
  assert.equal(held.closed[0].held_by, "database.away");
  assert.equal(held.closed[0].paged_at, null);
  // Control: the same outage from a guard that sends no receipts pages
  // once and recovers once, as before Stage 2.
  const old = run(25, (now, i) => (i < 20 ? dependency(null) : up(null)));
  assert.deepEqual(old.pages.map((p) => p.kind), ["open", "recovered"]);
  assert.ok(old.pages[0].at - T >= OPEN_AFTER_S);
});

test("an inside page not delivered within five minutes is paged by Kazma Watch", () => {
  const undelivered = run(15, () => dependency([away(T + 120, false)]));
  assert.equal(undelivered.pages.length, 1);
  const p = undelivered.pages[0];
  assert.equal(p.kind, "open");
  assert.equal(p.at, T + 120 + INSIDE_GRACE_S);
  assert.match(p.text, /Kazma's own page has not reached anyone/);
  // Control: delivered, the same receipt holds it.
  assert.deepEqual(run(15, () => dependency([away(T + 120, true)])).pages, []);
});

test("no inside page five minutes after Kazma Watch would have paged: it pages", () => {
  const silent = run(15, () => dependency([]));
  assert.equal(silent.pages.length, 1);
  assert.ok(silent.pages[0].at - T >= OPEN_AFTER_S + INSIDE_GRACE_S);
  assert.match(silent.pages[0].text, /Kazma has not paged it/);
  // ...and a wait that ends before then says nothing, at the end either.
  const brief = run(10, (now, i) => (i < 5 ? dependency([]) : up()));
  assert.deepEqual(brief.pages, []);
  // Control: the old rule pages at three minutes.
  const old = run(15, () => dependency(null));
  assert.ok(old.pages[0].at - T < OPEN_AFTER_S + 60);
});

test("a condition only Kazma Watch can see is paged whatever was paged inside", () => {
  const machine = run(6, () => ({ condition: "machine_unreachable", pages: [away(T, true)] }));
  assert.deepEqual(machine.pages.map((p) => p.kind), ["open"]);
  assert.ok(machine.pages[0].at - T < OPEN_AFTER_S + 60);
});

test("heartbeats that stop during an inside incident page the machine, and say what was open", () => {
  const r = run(30, (now, i) => {
    if (i < 10) return dependency([away(T + 60)]);
    if (i < 25) return { condition: "machine_unreachable", detail: "HTTP 530", pages: null, inside: ["database.away"] };
    return up();
  });
  assert.deepEqual(r.pages.map((p) => [p.kind, p.i]), [["open", 10], ["recovered", 25]]);
  assert.match(r.pages[0].text, /Paged before by: Kazma's database is not answering\./);
  assert.match(r.pages[0].text, /Open on Kazma when its heartbeats stopped: Kazma's database is not answering\./);
  // Control: a machine that just went away, with nothing open inside.
  const plain = run(6, () => ({ condition: "machine_unreachable", detail: "HTTP 530", pages: null, inside: [] }));
  assert.doesNotMatch(plain.pages[0].text, /Paged before|Open on Kazma/);
});

test("a database page answers for Kazma down while it boots (ANSWERED_BY)", () => {
  const boot = { key: "child.waiting_for_database", paged_at: T + 30, delivered: true };
  const r = run(12, () => ({ condition: "kazma_restarting", pages: [boot] }));
  assert.deepEqual(r.pages, []);
  // Control: a page of another condition answers for nothing.
  const disk = { key: "disk.critical", paged_at: T + 30, delivered: true };
  assert.equal(run(12, () => ({ condition: "kazma_restarting", pages: [disk] })).pages.length, 1);
  assert.ok(CATALOG.answered_by.kazma_restarting.includes("database_away"));
});

test("the guard's Docker page answers for Kazma not ready or restarting (2026-11-12)", () => {
  const docker = { key: "deps.engine_down", paged_at: T + 180, delivered: true };
  for (const condition of ["dependency_down", "kazma_restarting"]) {
    assert.deepEqual(run(15, () => ({ condition, detail: "code_execution, docker", pages: [docker] })).pages, [],
      condition);
    assert.ok(CATALOG.answered_by[condition].includes("docker_away"), condition);
  }
  // Control: the catalog of the morning, with no condition on the Docker
  // page, answered for neither: Kazma Watch paged "Kazma has not paged it".
  const before = { key: "deps.container_start_failed", paged_at: T + 180, delivered: true };
  assert.equal(run(15, () => dependency([before])).pages.length, 1);
});

test("no hourly reminder once a page from inside answers for what Kazma Watch paged", () => {
  // 2026-11-12: Kazma Watch paged at 7 min; the guard's Docker page came at
  // 12; Kazma Watch reminded every hour for five hours anyway.
  const late = { key: "deps.engine_down", paged_at: T + 720, delivered: true };
  const diag = (now, i) => ({ condition: "dependency_down", detail: "code_execution, docker",
    pages: i >= 12 ? [late] : [] });
  const r = run(5 * 60, diag);
  assert.deepEqual(r.pages.map((p) => p.kind), ["open"]);
  // Control: with no inside page the hourly reminders are sent.
  const alone = run(5 * 60, () => dependency([]));
  assert.ok(alone.pages.filter((p) => p.kind === "remind").length >= 4,
    alone.pages.map((p) => p.kind).join(","));
  // ...and so they are when the inside page was not delivered.
  const lost = { ...late, delivered: false };
  const r2 = run(5 * 60, (now, i) => ({ ...diag(now, i), pages: i >= 12 ? [lost] : [] }));
  assert.ok(r2.pages.filter((p) => p.kind === "remind").length >= 4);
});

test("a Cloudflare notice Kazma Watch paged holds its own incident of the same cause", () => {
  const notice = { key: "cloudflare.tunnel_down", condition: "tunnel_down", delivered: true };
  const held = run(8, () => ({ condition: "machine_unreachable", pages: null, notices: [notice] }));
  assert.deepEqual(held.pages, []);
  // Control: without the notice it pages; a notice of another cause holds nothing.
  assert.equal(run(8, () => ({ condition: "machine_unreachable", pages: null, notices: [] })).pages.length, 1);
  const other = { key: "cloudflare.access_expiring", condition: "observer_refused", delivered: true };
  assert.equal(run(8, () => ({ condition: "machine_unreachable", pages: null, notices: [other] })).pages.length, 1);
  assert.ok(sameCause("tunnel_down", "machine_unreachable") && !sameCause("tunnel_down", "guard_gone"));
});

test("the decision table", () => {
  const at = { now: T + 600, badSince: T, openAfter: OPEN_AFTER_S };
  assert.deepEqual(decide("dependency_down", { ...at, pages: null }), { act: "page" });
  assert.deepEqual(decide("dependency_down", { ...at, pages: [away(T + 590)] }), { act: "hold", by: "database.away" });
  assert.equal(decide("dependency_down", { ...at, pages: [away(T + 590, false)] }).act, "wait");
  assert.equal(decide("guard_gone", { ...at, pages: [] }).act, "page");
  assert.equal(decide("tunnel_down", { ...at, pages: [away(T)] }).act, "page");
});

// -- Stage 4: Cloudflare's notifications ---------------------------------------------

const SECRET = "cf-webhook-secret-0123456789abcdef";
const INSTALL = "a1b2c3d4e5f6a7b8";
const KEY = "pairing-key-0123456789abcdefghijklmnopqrstuv";

class Net {
  constructor() {
    this.live = 200;
    this.messages = [];
  }

  fetcher = async (url, init = {}) => {
    const u = String(url);
    if (u.startsWith("https://my.kazma.example/health/")) {
      return new Response(this.live === 200 ? "{}" : "", { status: u.endsWith("/live") ? this.live : 200 });
    }
    if (u.startsWith("https://api.telegram.org/")) {
      this.messages.push(JSON.parse(init.body).text);
      return Response.json({ ok: true });
    }
    if (u.startsWith("https://hc-ping.example/")) return new Response("OK");
    throw new Error(`unexpected fetch: ${u}`);
  };
}

function env(extra = {}) {
  return {
    DB: d1(),
    PULL_URL: "https://my.kazma.example",
    PAIRING_TOKEN: "p".repeat(40),
    TELEGRAM_BOT_TOKEN: "123:abc",
    TELEGRAM_CHAT_ID: "42",
    CF_WEBHOOK_SECRET: SECRET,
    ...extra,
  };
}

async function notify(e, net, body, now, secret = SECRET) {
  const headers = { "Content-Type": "application/json" };
  if (secret !== null) headers["cf-webhook-auth"] = secret;
  const req = new Request("https://status.kazma.example/v1/cloudflare", {
    method: "POST", headers, body: typeof body === "string" ? body : JSON.stringify(body) });
  return handle(req, e, now, { fetcher: net.fetcher });
}

const tunnel = (status, extra = {}) => ({
  name: "Tunnel health", text: `Tunnel kazma is ${status}`, ts: T, alert_type: "tunnel_health_event",
  account_id: "acc", policy_id: "pol", data: { tunnel_id: "t-1", tunnel_name: "kazma", new_status: status },
  ...extra,
});

test("the route needs the destination's secret, and refuses while none is set", async () => {
  const net = new Net();
  assert.equal((await notify(env({ CF_WEBHOOK_SECRET: "" }), net, tunnel("TUNNEL_STATUS_TYPE_DOWN"), T)).status, 503);
  assert.equal((await notify(env({ CF_WEBHOOK_SECRET: "short" }), net, tunnel("x"), T)).status, 503);
  const e = env();
  assert.equal((await notify(e, net, tunnel("TUNNEL_STATUS_TYPE_DOWN"), T, null)).status, 401);
  assert.equal((await notify(e, net, tunnel("TUNNEL_STATUS_TYPE_DOWN"), T, `${SECRET}x`)).status, 401);
  assert.equal((await notify(e, net, "not json", T)).status, 400);
  assert.equal((await notify(e, net, JSON.stringify({ text: "x".repeat(20000) }), T)).status, 413);
  assert.deepEqual(net.messages, [], "nothing refused is paged");
  assert.equal((await notify(e, net, tunnel("TUNNEL_STATUS_TYPE_DOWN"), T)).status, 200);
  assert.equal(net.messages.length, 1);
});

test("known notifications map to the catalog, anything else is the generic card", () => {
  const keys = (body) => mapNotification(body, "d").key;
  assert.equal(keys(tunnel("TUNNEL_STATUS_TYPE_DOWN")), "cloudflare.tunnel_down");
  assert.equal(keys(tunnel("TUNNEL_STATUS_TYPE_INACTIVE")), "cloudflare.tunnel_down");
  assert.equal(keys(tunnel("TUNNEL_STATUS_TYPE_DEGRADED")), "cloudflare.tunnel_degraded");
  assert.equal(mapNotification(tunnel("TUNNEL_STATUS_TYPE_HEALTHY")).event, "end");
  assert.equal(keys({ alert_type: "tunnel_update_event", data: { event: "TUNNEL_EVENT_TYPE_DELETED" } }),
    "cloudflare.tunnel_deleted");
  assert.equal(keys({ alert_type: "tunnel_update_event", data: { event: "TUNNEL_EVENT_TYPE_CREATED" } }),
    "cloudflare.tunnel_changed");
  assert.equal(keys({ alert_type: "expiring_service_token_alert", text: "token expires" }), "cloudflare.access_expiring");
  assert.equal(keys({ alert_type: "access_custom_certificate_expiration_type" }), "cloudflare.access_expiring");
  assert.equal(keys({ alert_type: "workers_observability_alert", data: { status: "PENDING" } }), "cloudflare.worker_errors");
  assert.equal(keys({ alert_type: "workers_observability_alert", alert_event: "ALERT_STATE_EVENT_END" }),
    "cloudflare.worker_errors_resolved");
  assert.equal(keys({ alert_type: "advanced_ddos_attack_l7_alert", text: "attack" }), "cloudflare.notification");
  assert.equal(keys({ text: "Hello World! This is a test message sent from https://cloudflare.com." }),
    "cloudflare.notification");
  // Every key the mapping names is in the catalog, sent by "cloudflare".
  const cf = new Set(CATALOG.alerts.filter((a) => a.sender === "cloudflare").map((a) => a.key));
  for (const k of ["cloudflare.tunnel_down", "cloudflare.notification_resolved"]) assert.ok(cf.has(k), k);
});

test("an unknown notification is paged with its own text, cut short and cleaned", async () => {
  const e = env();
  const net = new Net();
  const text = `Attack on kazma.ai‮\u0007 ${"x".repeat(600)}`;
  const res = await notify(e, net, { name: "DDoS", text, ts: T, alert_type: "advanced_ddos_attack_l7_alert" }, T);
  assert.equal(res.status, 200);
  assert.equal(net.messages.length, 1);
  const card = net.messages[0];
  assert.match(card, /^🟡 \[Cloudflare\] A Cloudflare notification\nDDoS: Attack on kazma\.ai/);
  assert.ok(!card.includes("‮") && !card.includes("\u0007"));
  assert.ok(card.split("\n")[1].length <= 300);
  assert.equal(cut("a\u0000b"), "a b");
});

test("a replayed or stale request is answered and never paged twice", async () => {
  const e = env();
  const net = new Net();
  const body = { name: "x", text: "once", ts: T, alert_type: "pages_event_alert" };
  assert.equal((await (await notify(e, net, body, T)).json()).paged, 1);
  const again = await (await notify(e, net, body, T + 30)).json();
  assert.equal(again.duplicate, true);
  assert.equal(net.messages.length, 1);
  const stale = await (await notify(e, net, { ...body, ts: T - 4 * 86400, text: "old" }, T)).json();
  assert.equal(stale.stale, true);
  assert.equal(net.messages.length, 1);
  // Control: a different body is a new notice.
  await notify(e, net, { ...body, text: "twice" }, T + 60);
  assert.equal(net.messages.length, 2);
});

async function pair(e) {
  await handle(new Request("https://status.kazma.example/v1/pair", {
    method: "POST", headers: { Authorization: `Bearer ${"p".repeat(40)}` },
    body: JSON.stringify({ install: INSTALL, key: KEY }) }), e, T);
}

async function minutes(e, net, from, count) {
  for (let i = 0; i < count; i += 1) await runMinute(e, from + i * 60, { fetcher: net.fetcher });
}

// The site alone (nothing paired) reads the tunnel's 530 as "unreachable"
// and 200 as up: the cases below need no heartbeat.

test("a tunnel-down notice joins the incident Kazma Watch has open: one page, two witnesses", async () => {
  const e = env();
  const net = new Net();
  net.live = 530;
  await minutes(e, net, T, 4);
  assert.equal(net.messages.length, 1);
  assert.match(net.messages[0], /\[Watch\] Kazma is not reachable/);
  await notify(e, net, tunnel("TUNNEL_STATUS_TYPE_DOWN"), T + 250);
  assert.equal(net.messages.length, 1, "joined, not a second page");
  const notes = JSON.parse(e.DB.raw.prepare("SELECT notes FROM incident_notes").get().notes);
  assert.deepEqual(notes.witnesses, ["cloudflare.tunnel_down"]);
  // The hourly reminder carries Cloudflare's word.
  await minutes(e, net, T + 3780, 1);
  assert.match(net.messages[1], /Cloudflare says so too: Cloudflare says the tunnel is down\./);
  // Back: Kazma Watch's recovery only; Cloudflare's "healthy" closes the joined notice quietly.
  net.live = 200;
  await notify(e, net, tunnel("TUNNEL_STATUS_TYPE_HEALTHY"), T + 3850);
  await minutes(e, net, T + 3900, 2);
  assert.equal(net.messages.length, 3);
  assert.match(net.messages[2], /\[Watch\] Kazma is back/);
});

test("a notice that comes while the probe is about to open its incident waits for it", async () => {
  const e = env();
  const net = new Net();
  net.live = 530;
  await minutes(e, net, T, 1);          // bad since T, no incident yet
  await notify(e, net, tunnel("TUNNEL_STATUS_TYPE_DOWN"), T + 30);
  assert.deepEqual(net.messages, [], "waits for Kazma Watch's own incident");
  await minutes(e, net, T + 60, 3);
  assert.equal(net.messages.length, 1);
  assert.match(net.messages[0], /\[Watch\]/);
  const row = e.DB.raw.prepare("SELECT incident_id, paged_at FROM cloudflare_notices").get();
  assert.ok(row.incident_id && !row.paged_at);
});

test("a notice while Kazma Watch reads Kazma up is paged; its end sends one recovery", async () => {
  const e = env();
  const net = new Net();
  await notify(e, net, tunnel("TUNNEL_STATUS_TYPE_DEGRADED"), T);
  assert.equal(net.messages.length, 1);
  assert.match(net.messages[0], /^🟡 \[Cloudflare\] Cloudflare says the tunnel is degraded\nTunnel kazma is degraded/);
  // A worse state of the same tunnel speaks; the same state again does not.
  await notify(e, net, tunnel("TUNNEL_STATUS_TYPE_DOWN"), T + 60);
  await notify(e, net, tunnel("TUNNEL_STATUS_TYPE_DOWN", { text: "again" }), T + 90);
  assert.equal(net.messages.length, 2);
  assert.match(net.messages[1], /\[Cloudflare\] Cloudflare says the tunnel is down/);
  await notify(e, net, tunnel("TUNNEL_STATUS_TYPE_HEALTHY"), T + 600);
  assert.equal(net.messages.length, 3);
  assert.match(net.messages[2], /^🟢 \[Cloudflare\] Cloudflare says the tunnel is healthy again\nIt lasted 9 min\./);
  // An end with nothing open says nothing.
  await notify(e, net, tunnel("TUNNEL_STATUS_TYPE_HEALTHY", { text: "x" }), T + 700);
  assert.equal(net.messages.length, 3);
});

test("a notice Kazma Watch paged holds its incident, and Kazma Watch closes it", async () => {
  const e = env();
  const net = new Net();
  await notify(e, net, tunnel("TUNNEL_STATUS_TYPE_DOWN"), T);   // Kazma Watch still reads up
  net.live = 530;
  await minutes(e, net, T + 60, 6);
  assert.equal(net.messages.length, 1, "Kazma Watch's own incident is held by the notice");
  net.live = 200;
  await minutes(e, net, T + 420, 2);
  assert.equal(net.messages.length, 2);
  assert.match(net.messages[1], /^🟢 \[Cloudflare\] Cloudflare says the tunnel is healthy again/);
  // Cloudflare's own "healthy" afterwards closes nothing more.
  await notify(e, net, tunnel("TUNNEL_STATUS_TYPE_HEALTHY"), T + 600);
  assert.equal(net.messages.length, 2);
});

test("at most twenty notices an hour are paged; the rest are recorded", async () => {
  const e = env();
  const net = new Net();
  for (let i = 0; i < PAGES_PER_HOUR + 5; i += 1) {
    await notify(e, net, { name: "n", text: `notice ${i}`, ts: T, alert_type: "pages_event_alert" }, T + i);
  }
  assert.equal(net.messages.length, PAGES_PER_HOUR);
  assert.equal(e.DB.raw.prepare("SELECT COUNT(*) AS n FROM cloudflare_notices").get().n, PAGES_PER_HOUR + 5);
});

test("Kazma itself is never called: a notice reaches Telegram only", async () => {
  const e = env();
  const calls = [];
  const fetcher = async (url, init) => {
    calls.push(String(url));
    return Response.json({ ok: true });
  };
  await handle(new Request("https://status.kazma.example/v1/cloudflare", {
    method: "POST", headers: { "cf-webhook-auth": SECRET }, body: JSON.stringify(tunnel("TUNNEL_STATUS_TYPE_DOWN")) }),
  e, T, { fetcher });
  assert.ok(calls.length >= 1);
  assert.ok(calls.every((u) => u.startsWith("https://api.telegram.org/")), calls.join(", "));
});

test("a forget removes the notices and notes kept about the install", async () => {
  const e = env();
  const net = new Net();
  await pair(e);
  net.live = 530;
  await minutes(e, net, T, 4);
  await notify(e, net, tunnel("TUNNEL_STATUS_TYPE_DOWN"), T + 250);
  await handle(new Request("https://status.kazma.example/v1/unpair", {
    method: "POST", headers: { Authorization: `Bearer ${"p".repeat(40)}` },
    body: JSON.stringify({ install: INSTALL, forget: true }) }), e, T + 300);
  for (const table of ["incident_notes", "cloudflare_notices", "incidents"]) {
    assert.equal(e.DB.raw.prepare(`SELECT COUNT(*) AS n FROM ${table}`).get().n, 0, table);
  }
});

