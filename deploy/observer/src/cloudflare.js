// Cloudflare's own notifications through Kazma Watch (the unified alerts
// plan, Stage 4).
//
//   POST /v1/cloudflare   a Cloudflare Notifications webhook destination
//
// Tunnel health, Access and Worker errors are Cloudflare notifications; the
// owner points a webhook destination here (README, "Cloudflare's
// notifications"). Each request carries the destination's secret in the
// ``cf-webhook-auth`` header and is refused without it (CF_WEBHOOK_SECRET;
// 503 while none is set). A notification is mapped to an entry of Kazma's
// alert catalog (``cloudflare.*``; anything unknown is the generic
// ``cloudflare.notification`` card with its own text, cut short) and then:
//
//   - joins the incident Kazma Watch already has for the same cause (a
//     tunnel-down notice while the probe reads the tunnel's 530 is one
//     incident with two witnesses: Kazma Watch's page says "Cloudflare says
//     so too", and no second page goes out), or waits a little for the one
//     its probe is about to open;
//   - else is paged on its own, as the card with the source Cloudflare. A
//     paged notice holds Kazma Watch's own incident of the same cause
//     (receipts.js), and its end -- or Kazma Watch seeing Kazma back -- sends
//     its one recovery card.
//
// A body already taken (a replay, Cloudflare's own retry) is answered and
// never paged twice; one older than three days is answered and dropped.
// Nothing here ever reaches Kazma: Kazma is behind the tunnel the notice is
// about.

import { page } from "./alerts.js";
import { formatOperatorCard } from "./card.js";
import { clock, duration, OPEN_AFTER_S, RECOVERY_RETRY_S } from "./incidents.js";
import { entryOf, sameCause } from "./receipts.js";
import { Store } from "./store.js";
import { sameText } from "./verify.js";

export const MAX_CF_BODY = 16384;
export const SECRET_MIN_CHARS = 16;
// A notification whose own time is further off than this is not news.
export const STALE_S = 3 * 86400;
export const DETAIL_CHARS = 300;
// A notice of one of Kazma Watch's conditions waits this long, after it
// came, for the incident Kazma Watch's probe is about to open.
export const JOIN_WAIT_S = OPEN_AFTER_S + 70;
// Pages of Cloudflare notices in an hour at most; the rest are recorded.
export const PAGES_PER_HOUR = 20;

const TYPE_RE = /^[a-z0-9_]{1,80}$/;
const CORRELATION_RE = /^[A-Za-z0-9-]{8,80}$/;
const TUNNEL_DOWN = new Map([
  ["TUNNEL_STATUS_TYPE_DOWN", "down"],
  ["TUNNEL_STATUS_TYPE_INACTIVE", "inactive"],
  ["TUNNEL_STATUS_TYPE_INVALID", "in an invalid state"],
]);
const ACCESS = new Set([
  "expiring_service_token_alert",
  "access_custom_certificate_expiration_type",
  "mtls_certificate_store_certificate_expiration_type",
]);
const END = "ALERT_STATE_EVENT_END";

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
  });
}

/**
 * Cloudflare's text, made safe to page: control and direction characters
 * out, whitespace folded, cut at ``max`` characters.
 */
export function cut(value, max = DETAIL_CHARS) {
  const text = String(value ?? "")
    .replace(/[\u0000-\u0009\u000b-\u001f\u007f​-‏‪-‮⁦-⁩]/g, " ")
    .replace(/[ ]{2,}/g, " ")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
  return text.length > max ? `${text.slice(0, max - 1).trimEnd()}…` : text;
}

function oneLine(value, max) {
  return cut(String(value ?? "").replace(/\s+/g, " "), max);
}

function obj(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

/**
 * The notice a notification body is: ``{key, alert_type, event, correlation,
 * detail}``. ``event`` is "start" (an end closes it), "end" or "once".
 * ``digest`` names a notice nothing else pairs.
 */
export function mapNotification(body, digest = "") {
  const b = obj(body);
  const type = typeof b.alert_type === "string" && TYPE_RE.test(b.alert_type) ? b.alert_type : "";
  const data = obj(b.data);
  const ended = b.alert_event === END;
  const grouped = typeof b.alert_correlation_id === "string" && CORRELATION_RE.test(b.alert_correlation_id)
    ? b.alert_correlation_id : "";
  const text = cut(b.text);
  const notice = (key, event, correlation, detail) => ({
    key, alert_type: type || "unknown", event, correlation, detail: detail || null,
  });

  if (type === "tunnel_health_event") {
    const name = oneLine(data.tunnel_name || data.tunnel_id || "unnamed", 64);
    const correlation = `tunnel:${oneLine(data.tunnel_id || data.tunnel_name || "unknown", 80)}`;
    const status = String(data.new_status || "");
    if (status === "TUNNEL_STATUS_TYPE_HEALTHY" || ended) {
      return notice("cloudflare.tunnel_healthy", "end", correlation, `Tunnel ${name} is healthy.`);
    }
    if (status === "TUNNEL_STATUS_TYPE_DEGRADED") {
      return notice("cloudflare.tunnel_degraded", "start", correlation,
        `Tunnel ${name} is degraded: some of its connections are down.`);
    }
    const word = TUNNEL_DOWN.get(status) || "down";
    return notice("cloudflare.tunnel_down", "start", correlation, `Tunnel ${name} is ${word}.`);
  }
  if (type === "tunnel_update_event") {
    const name = oneLine(data.tunnel_name || data.tunnel_id || "unnamed", 64);
    const deleted = data.event === "TUNNEL_EVENT_TYPE_DELETED";
    const what = deleted ? "deleted" : data.event === "TUNNEL_EVENT_TYPE_CREATED" ? "created" : "changed";
    return notice(deleted ? "cloudflare.tunnel_deleted" : "cloudflare.tunnel_changed", "once",
      `once:${digest}`, `Tunnel ${name} was ${what}.`);
  }
  if (ACCESS.has(type)) {
    return notice("cloudflare.access_expiring", "once", `once:${digest}`, text);
  }
  if (type === "workers_observability_alert") {
    const correlation = grouped || `workers:${oneLine(b.policy_id || b.name || "alert", 80)}`;
    const resolved = ended || data.status === "RESOLVED" || !!obj(data.episode).resolved_at;
    return resolved
      ? notice("cloudflare.worker_errors_resolved", "end", correlation, text)
      : notice("cloudflare.worker_errors", "start", correlation, text);
  }
  const policy = oneLine(b.policy_name || b.name || "", 80);
  const detail = cut(policy && text ? `${policy}: ${text}` : policy || text || "(no text)");
  return notice(ended ? "cloudflare.notification_resolved" : "cloudflare.notification", "once",
    `once:${digest}`, detail);
}

async function digestOf(raw) {
  const hash = await crypto.subtle.digest("SHA-256", raw);
  return [...new Uint8Array(hash)].map((x) => x.toString(16).padStart(2, "0")).join("");
}

async function readBody(request) {
  const declared = Number(request.headers.get("Content-Length") || "0");
  if (declared > MAX_CF_BODY) return null;
  const raw = await request.arrayBuffer();
  return raw.byteLength > MAX_CF_BODY ? null : raw;
}

/** POST /v1/cloudflare. ``installs``: the watched installs (or the site). */
export async function handleCloudflare(request, env, now, { installs, fetcher, makeMessage } = {}) {
  const secret = String(env.CF_WEBHOOK_SECRET || "").trim();
  if (secret.length < SECRET_MIN_CHARS) return json({ error: "cloudflare_not_configured" }, 503);
  if (!sameText(request.headers.get("cf-webhook-auth") || "", secret)) return json({ error: "bad_secret" }, 401);
  const raw = await readBody(request);
  if (!raw) return json({ error: "too_large" }, 413);
  let body;
  try {
    body = JSON.parse(new TextDecoder().decode(raw));
  } catch {
    return json({ error: "bad_body" }, 400);
  }
  if (!body || typeof body !== "object" || Array.isArray(body)) return json({ error: "bad_body" }, 400);
  // Answered with 200 from here on: Cloudflare retries anything else.
  if (typeof body.ts === "number" && (now - body.ts > STALE_S || body.ts - now > 600)) {
    return json({ ok: true, stale: true });
  }
  const store = new Store(env.DB);
  const digest = await digestOf(raw);
  if (!(await store.takeDigest(digest, now))) return json({ ok: true, duplicate: true });
  const notice = mapNotification(body, digest.slice(0, 24));
  const taken = await takeNotice(store, notice, now);
  const pages = await processNotices(env, now, { installs, fetcher, makeMessage });
  return json({ ok: true, key: notice.key, ...taken, paged: pages.filter((p) => p.ok).length });
}

/** Record a mapped notice: a start, an end closing one, or a one-off. */
export async function takeNotice(store, notice, now) {
  const open = notice.event === "once" ? null : await store.openNotice(notice.correlation);
  if (notice.event === "end") {
    if (!open) return { ignored: "nothing_open" };
    open.ended_at = now;
    if (open.paged_at) {
      // Paged: its recovery is due. Joined (Kazma Watch's incident has it)
      // or never paged: nothing was said, so nothing to close.
      open.recovery_due = 1;
      open.closer = notice.key;
    }
    await store.saveNotice(open);
    return { closed: open.key };
  }
  if (open && open.key === notice.key) return { duplicate: true };
  if (open) {
    // A change of state (degraded, then down): the new notice speaks.
    open.ended_at = now;
    await store.saveNotice(open);
  }
  await store.addNotice({ ...notice, received_at: now });
  return { recorded: true };
}

function noticeText(n, entry) {
  return formatOperatorCard("cloudflare", entry.severity, entry.title, n.detail || "",
    n.event === "start" ? { since: clock(n.received_at) } : {});
}

function closerText(n) {
  const closer = entryOf(n.closer) || entryOf("cloudflare.notification_resolved");
  return formatOperatorCard("cloudflare", "success", closer.title,
    `It lasted ${duration(Math.max(0, (n.ended_at || n.received_at) - n.received_at))}.`);
}

/**
 * What the open notices still need: join an incident Kazma Watch opened,
 * page what nobody has said, send recovery cards that are due. Called by the
 * route and by every minute's run. Returns what it sent.
 */
export async function processNotices(env, now, { installs = [], fetcher, makeMessage } = {}) {
  const store = new Store(env.DB);
  const states = [];
  for (const install of installs) states.push({ install, state: await store.state(install) });
  let budget = PAGES_PER_HOUR - Number((await store.noticesPagedSince(now - 3600)) || 0);
  const sent = [];
  const send = async (text) => page(env, text, now, { fetcher, makeMessage });
  for (const n of await store.activeNotices()) {
    const entry = entryOf(n.key) || entryOf("cloudflare.notification");
    if (n.recovery_due) {
      if (now - n.ended_at > RECOVERY_RETRY_S) {
        n.recovery_due = 0;
      } else {
        const ok = await send(closerText(n));
        sent.push({ id: n.id, kind: "recovered", ok });
        if (ok) {
          n.recovery_due = 0;
          n.recovery_paged_at = now;
        }
      }
      await store.saveNotice(n);
      continue;
    }
    if (n.incident_id) {
      // Joined: over when its incident is (Kazma Watch sends that recovery).
      const ended = await store.incidentEnded(n.incident_id);
      if (ended !== null) {
        n.ended_at = ended || now;
        await store.saveNotice(n);
      }
      continue;
    }
    if (n.paged_at) continue; // a paged start waits for its end
    if (entry.condition && n.event === "start") {
      const joined = states.find((s) => s.state.incident && !s.state.incident.ended_at
        && sameCause(s.state.incident.cause, entry.condition));
      if (joined) {
        const incident = joined.state.incident;
        n.install = joined.install;
        n.incident_id = incident.id;
        if (!incident.witnesses.includes(n.key)) incident.witnesses.push(n.key);
        await store.saveNotes(incident);
        await store.saveNotice(n);
        sent.push({ id: n.id, kind: "joined", ok: true });
        continue;
      }
      const coming = states.some((s) => s.state.bad_since !== null && !s.state.incident
        && sameCause(s.state.condition, entry.condition));
      if (coming && now - n.received_at < JOIN_WAIT_S) continue;
    }
    if (now - n.received_at > RECOVERY_RETRY_S) {
      n.ended_at = now; // no channel took it for an hour: given up
      await store.saveNotice(n);
      continue;
    }
    n.last_try_at = now;
    if (budget > 0) {
      const ok = await send(noticeText(n, entry));
      sent.push({ id: n.id, kind: "notice", ok });
      if (ok) {
        budget -= 1;
        n.paged_at = now;
        if (n.event === "once") n.ended_at = now;
      }
    }
    await store.saveNotice(n);
  }
  return sent;
}

/**
 * Kazma Watch held an incident because a Cloudflare notice had paged it,
 * and now reads Kazma up: that notice's recovery card is due (the sender
 * that paged sends it). ``closed``: the incident ``step`` closed.
 */
export async function closeHeldNotice(store, closed, now) {
  if (!closed || !closed.held_by || !closed.held_by.startsWith("cloudflare.")) return;
  for (const n of await store.activeNotices()) {
    if (n.key !== closed.held_by || !n.paged_at || n.ended_at) continue;
    n.ended_at = now;
    n.recovery_due = 1;
    n.closer = (entryOf(n.key) || {}).closed_by || "cloudflare.notification_resolved";
    await store.saveNotice(n);
  }
}

/** Open Cloudflare notices Kazma Watch paged: they hold its own (receipts.js). */
export async function pagedNotices(store) {
  return (await store.activeNotices())
    .filter((n) => n.paged_at && !n.ended_at && n.event === "start")
    .map((n) => ({ key: n.key, condition: (entryOf(n.key) || {}).condition || "", delivered: true }));
}
