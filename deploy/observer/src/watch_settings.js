// What Settings -> Alerts in Kazma asks of Kazma Watch (the unified alerts
// plan, Stage 3). Every request is signed by the install's pairing key, the
// way the heartbeat is (verify.js): the page never holds PAIRING_TOKEN.
//
//   POST /v1/settings   what Kazma Watch should know of its owner: the time
//                       zone its cards are written in, the email recipient.
//                       Kept in D1 (install_settings); a request older than
//                       the last one accepted is refused, so a recorded one
//                       cannot put back an older setting.
//   POST /v1/test-page  one test card through every channel Kazma Watch has;
//                       the answer says which took it ("sent", "failed",
//                       "off"). At most one per TEST_GAP_S.
//   POST /v1/leave      the install stops being watched (and with "forget",
//                       everything kept about it goes): Unpair from the page.

import { pageChannels } from "./alerts.js";
import { formatOperatorCard } from "./card.js";
import { clock } from "./incidents.js";
import { json, readBody } from "./http.js";
import { Store } from "./store.js";
import { signingHeaders, verifyRequest } from "./verify.js";

export const TEST_GAP_S = 20;
const ZONE_MAX = 64;
const EMAIL_RE = /^[^\s@<>"(),;:]{1,64}@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$/;

/** True when ``zone`` is an IANA time zone this runtime knows. */
export function validZone(zone) {
  if (typeof zone !== "string" || !zone || zone.length > ZONE_MAX) return false;
  try {
    new Intl.DateTimeFormat("en-US", { timeZone: zone });
    return true;
  } catch {
    return false;
  }
}

export function validEmail(value) {
  return typeof value === "string" && value.length <= 254 && EMAIL_RE.test(value);
}

/**
 * A signed request's install, timestamp and body, or ``{response}`` saying
 * why not (401 unsigned or unknown, 413 too large, 400 not the install's JSON).
 */
async function signedBody(request, env, now) {
  const h = signingHeaders(request, now);
  if (h.error) return { response: json({ error: h.error }, 401) };
  const raw = await readBody(request);
  if (!raw) return { response: json({ error: "too_large" }, 413) };
  const store = new Store(env.DB);
  const key = await store.installKey(h.install);
  if (!key) return { response: json({ error: "unknown_install" }, 401) };
  if (!(await verifyRequest(key, h, request, raw))) return { response: json({ error: "bad_signature" }, 401) };
  let body;
  try {
    body = JSON.parse(new TextDecoder().decode(raw));
  } catch {
    return { response: json({ error: "bad_body" }, 400) };
  }
  if (!body || body.v !== 1 || body.install !== h.install) return { response: json({ error: "bad_body" }, 400) };
  return { store, install: h.install, ts: h.ts, body };
}

export async function handleWatchSettings(request, env, now) {
  const req = await signedBody(request, env, now);
  if (req.response) return req.response;
  const zone = req.body.timezone == null || req.body.timezone === "" ? null : req.body.timezone;
  const email = req.body.email_to == null || req.body.email_to === "" ? null : req.body.email_to;
  if (zone !== null && !validZone(zone)) return json({ error: "bad_timezone" }, 400);
  if (email !== null && !validEmail(email)) return json({ error: "bad_email" }, 400);
  const settings = { timezone: zone, email_to: email };
  if (!(await req.store.saveWatchSettings(req.install, req.ts, settings, now))) {
    return json({ error: "replayed" }, 409);
  }
  return json({ ok: true, settings });
}

/**
 * What Kazma Watch knows of its owner for a card that is about no one
 * install (a Cloudflare notice, cloudflare.js): the settings of the watched
 * install that told it last, or none (the cards in UTC, the email to
 * wrangler.jsonc's EMAIL_TO). One owner reads every card Kazma Watch sends.
 */
export async function ownerSettings(store, installs) {
  let best = null;
  for (const install of installs || []) {
    const told = await store.watchSettings(install);
    if (!told.timezone && !told.email_to) continue;
    if (!best || (told.updated_at || 0) > (best.updated_at || 0)) best = told;
  }
  return best || { timezone: null, email_to: null, updated_at: null };
}

/** The test card, its time in the owner's zone (so the zone is seen to work). */
export function testCard(now, timeZone) {
  return formatOperatorCard("watch", "info", "Test page",
    `Sent from Kazma's Settings -> Alerts at ${clock(now, timeZone)} to check that Kazma Watch `
    + "reaches you. Nothing is wrong.");
}

export async function handleTestPage(request, env, now, deps = {}) {
  const req = await signedBody(request, env, now);
  if (req.response) return req.response;
  if (!(await req.store.claimTest(req.install, req.ts, now, TEST_GAP_S))) {
    return json({ error: "too_soon" }, 429);
  }
  const settings = await req.store.watchSettings(req.install);
  const sent = await pageChannels(env, testCard(now, settings.timezone), now, {
    fetcher: deps.fetcher, emailTo: settings.email_to,
  });
  const word = (v) => (v === null ? "off" : v ? "sent" : "failed");
  const channels = { telegram: word(sent.telegram), email: word(sent.email) };
  // Why an email was refused, as Email Service's code (alerts.js): Settings
  // shows it while the sending domain is being set up.
  const reasons = sent.emailCode ? { email: sent.emailCode } : {};
  return json({ ok: sent.telegram === true || sent.email === true, channels, reasons });
}

export async function handleLeave(request, env, now) {
  const req = await signedBody(request, env, now);
  if (req.response) return req.response;
  const forget = req.body.forget === true;
  await req.store.unpair(req.install, forget);
  return json({ ok: true, unpaired: req.install, forgotten: forget });
}
