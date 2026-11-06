// The outside observer (resilience plan B1): a Cloudflare Worker that
// watches the owner's Kazma from outside the machine.
//
//   every minute   pull probe: PULL_URL/health/live (and /health/ready every
//                  5 minutes), through the tunnel, with an Access service token
//   every minute   push: the guard's signed heartbeat (POST /v1/heartbeat)
//   every minute   diagnosis + incidents + pages (Telegram, email)
//   GET /          the status page (Cloudflare Access, the owner only)
//   GET /v1/summary  the install's own summary, signed with its key
//                  (Settings and the daily digest read it)
//   POST /v1/pair, /v1/unpair  `kazma observer pair` / `unpair` (PAIRING_TOKEN)
//
// Each run pings Healthchecks.io, which emails the owner if the runs stop.

import { checkAccess } from "./access.js";
import { page } from "./alerts.js";
import { diagnose, GOOD, sanitize } from "./diagnose.js";
import { settle, step } from "./incidents.js";
import { ninetyDays, statusPage, uptimePct } from "./page.js";
import { Store } from "./store.js";
import { INSTALL_RE, sameText, signingHeaders, verify } from "./verify.js";

const DAY = 86400;
const MAX_BODY = 4096;
const PROBE_TIMEOUT_MS = 10000;
const KEY_RE = /^[A-Za-z0-9_-]{32,128}$/;
// The pseudo-install the probe alone is recorded under while none is paired.
export const SITE = "site";

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
  });
}

async function readBody(request) {
  const declared = Number(request.headers.get("Content-Length") || "0");
  if (declared > MAX_BODY) return null;
  const raw = await request.arrayBuffer();
  return raw.byteLength > MAX_BODY ? null : raw;
}

// -- the heartbeat ------------------------------------------------------------

async function handleHeartbeat(request, env, now) {
  const h = signingHeaders(request, now);
  if (h.error) return json({ error: h.error }, 401);
  const raw = await readBody(request);
  if (!raw) return json({ error: "too_large" }, 413);
  const store = new Store(env.DB);
  const key = await store.installKey(h.install);
  if (!key) return json({ error: "unknown_install" }, 401);
  if (!(await verify(key, h.ts, raw, h.signature))) return json({ error: "bad_signature" }, 401);
  let body;
  try {
    body = JSON.parse(new TextDecoder().decode(raw));
  } catch {
    return json({ error: "bad_body" }, 400);
  }
  if (!body || body.v !== 1 || body.install !== h.install) return json({ error: "bad_body" }, 400);
  if (!(await store.acceptHeartbeat(h.install, h.ts, sanitize(body), now))) {
    return json({ error: "replayed" }, 409);
  }
  return json({ ok: true, at: now });
}

// -- pairing ------------------------------------------------------------------

function pairingAllowed(request, env) {
  const token = (env.PAIRING_TOKEN || "").trim();
  if (token.length < 32) return { status: 503, error: "pairing_not_configured" };
  const auth = request.headers.get("Authorization") || "";
  if (!sameText(auth, `Bearer ${token}`)) return { status: 401, error: "bad_pairing_token" };
  return null;
}

async function handlePair(request, env, now, unpair) {
  const refused = pairingAllowed(request, env);
  if (refused) return json({ error: refused.error }, refused.status);
  const raw = await readBody(request);
  let body;
  try {
    body = JSON.parse(new TextDecoder().decode(raw || new ArrayBuffer(0)));
  } catch {
    return json({ error: "bad_body" }, 400);
  }
  if (!body || !INSTALL_RE.test(String(body.install || ""))) return json({ error: "bad_install" }, 400);
  const store = new Store(env.DB);
  if (unpair) {
    await store.unpair(body.install, body.forget === true);
    return json({ ok: true, unpaired: body.install, forgotten: body.forget === true });
  }
  if (!KEY_RE.test(String(body.key || ""))) return json({ error: "bad_key" }, 400);
  await store.pair(body.install, body.key, now);
  return json({ ok: true, paired: body.install });
}

// -- reading back -------------------------------------------------------------

async function view(store, install, now) {
  const state = await store.state(install);
  const heartbeat = install === SITE ? null : await store.heartbeat(install);
  const days = ninetyDays(await store.daily(install, now - 90 * DAY), now);
  return {
    name: install === SITE ? "Kazma (no install paired: the site only)" : `Install ${install}`,
    state,
    heartbeat,
    days,
    up7: uptimePct(days.slice(-7)),
    up30: uptimePct(days.slice(-30)),
    up90: uptimePct(days),
    incidents: await store.incidents(install, now - 90 * DAY),
    hours: await store.hours(install, now - DAY),
  };
}

async function handleSummary(request, env, now) {
  const h = signingHeaders(request, now);
  if (h.error) return json({ error: h.error }, 401);
  const store = new Store(env.DB);
  const key = await store.installKey(h.install);
  if (!key) return json({ error: "unknown_install" }, 401);
  if (!(await verify(key, h.ts, "GET /v1/summary", h.signature))) return json({ error: "bad_signature" }, 401);
  const v = await view(store, h.install, now);
  const week = v.incidents.filter((i) => (i.ended_at || now) >= now - 7 * DAY);
  return json({
    install: h.install,
    condition: v.state.condition,
    detail: v.state.detail,
    heartbeat_age_s: v.heartbeat ? now - v.heartbeat.received_at : null,
    up_7d_pct: v.up7,
    incidents_7d: week.map((i) => ({
      cause: i.cause,
      causes: i.causes,
      started_at: i.started_at,
      ended_at: i.ended_at,
    })),
  });
}

async function handleStatusPage(request, env, now, deps) {
  const access = await checkAccess(request, env, { fetcher: deps.fetcher, now: now * 1000 });
  if (!access.ok) {
    return new Response(access.status === 503 ? "The status page is not set up." : "Forbidden.", {
      status: access.status,
      headers: { "Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store" },
    });
  }
  const store = new Store(env.DB);
  const installs = await store.listInstalls();
  const views = [];
  for (const install of installs.length ? installs : [SITE]) views.push(await view(store, install, now));
  return new Response(statusPage(views, now), {
    headers: {
      "Content-Type": "text/html; charset=utf-8",
      "Cache-Control": "no-store",
      "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'",
      "X-Content-Type-Options": "nosniff",
      "Referrer-Policy": "no-referrer",
    },
  });
}

export async function handle(request, env, now, deps = {}) {
  const url = new URL(request.url);
  const route = `${request.method} ${url.pathname}`;
  const d = { fetcher: deps.fetcher || fetch };
  if (route === "POST /v1/heartbeat") return handleHeartbeat(request, env, now);
  if (route === "POST /v1/pair") return handlePair(request, env, now, false);
  if (route === "POST /v1/unpair") return handlePair(request, env, now, true);
  if (route === "GET /v1/summary") return handleSummary(request, env, now);
  if (route === "GET /") return handleStatusPage(request, env, now, d);
  return json({ error: "not_found" }, 404);
}

// -- the minute run -------------------------------------------------------------

async function probe(env, path, fetcher) {
  const base = String(env.PULL_URL || "").replace(/\/+$/, "");
  if (!base) return { error: "no_url" };
  const headers = {};
  if (env.ACCESS_CLIENT_ID && env.ACCESS_CLIENT_SECRET) {
    headers["CF-Access-Client-Id"] = env.ACCESS_CLIENT_ID;
    headers["CF-Access-Client-Secret"] = env.ACCESS_CLIENT_SECRET;
  }
  const started = Date.now();
  try {
    const res = await fetcher(base + path, {
      headers, redirect: "manual", signal: AbortSignal.timeout(PROBE_TIMEOUT_MS),
    });
    const ms = Date.now() - started;
    let hint = res.headers.get("Location") || "";
    if (res.status !== 200) hint += ` ${(await res.text().catch(() => "")).slice(0, 2000)}`;
    else if (res.body && res.body.cancel) await res.body.cancel().catch(() => {});
    return { status: res.status, ms, hint };
  } catch (exc) {
    return { error: exc && exc.name === "TimeoutError" ? "timeout" : "fetch_failed", ms: Date.now() - started };
  }
}

// Uptime counts Kazma serving: a full disk still serves (and pages), and
// planned maintenance is not downtime.
function countsAsUp(condition) {
  return GOOD.has(condition) || condition === "disk_critical";
}

/** One minute of watching. Returns what it saw, for the tests. */
export async function runMinute(env, now, deps = {}) {
  const fetcher = deps.fetcher || fetch;
  const store = new Store(env.DB);
  const live = await probe(env, "/health/live", fetcher);
  const minute = Math.floor(now / 60);
  const ready = minute % 5 === 0 ? await probe(env, "/health/ready", fetcher) : null;
  const installs = await store.listInstalls();
  const seen = {};
  for (const install of installs.length ? installs : [SITE]) {
    const prev = await store.state(install);
    const heartbeat = install === SITE ? null : await store.heartbeat(install);
    const readyNow = ready ? { status: ready.status || 0, at: now } : prev.ready;
    const diag = diagnose({ live, ready: readyNow, heartbeat, now, expectHeartbeat: install !== SITE });
    let { state, pages } = step(prev, diag, now);
    state.ready = readyNow;
    const sent = [];
    for (const p of pages) {
      const ok = await page(env, p.text, now, { fetcher, makeMessage: deps.makeMessage });
      sent.push({ kind: p.kind, ok, text: p.text });
      state = settle(state, p, ok, now);
    }
    await store.saveState(install, state, now);
    await store.record(install, now, diag.condition, live,
      heartbeat ? now - heartbeat.received_at : null, countsAsUp(diag.condition));
    seen[install] = { ...diag, pages: sent };
  }
  if (minute % 60 === 7) await store.prune(now);
  return { live, seen };
}

async function pingHealthchecks(env, ok, fetcher) {
  const url = String(env.HEALTHCHECKS_URL || "").replace(/\/+$/, "");
  if (!url.startsWith("https://")) return;
  try {
    await fetcher(ok ? url : `${url}/fail`, { method: "POST", signal: AbortSignal.timeout(8000) });
  } catch {
    // Healthchecks emails the owner when the pings stop: nothing to do here.
  }
}

/** The scheduled run and its Healthchecks ping (exported for the tests). */
export async function scheduledRun(env, now, deps = {}) {
  let ok = true;
  try {
    await runMinute(env, now, deps);
  } catch (exc) {
    ok = false;
    console.error("observer run failed", exc && exc.name);
  }
  await pingHealthchecks(env, ok, deps.fetcher || fetch);
  return ok;
}

export default {
  async fetch(request, env) {
    return handle(request, env, Math.floor(Date.now() / 1000));
  },
  async scheduled(controller, env, ctx) {
    const now = Math.floor((controller.scheduledTime || Date.now()) / 1000);
    ctx.waitUntil(scheduledRun(env, now));
  },
};
