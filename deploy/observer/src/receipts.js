// One page per problem (the unified alerts plan, Stage 2): the page receipts
// the guard's heartbeat carries, and what Kazma Watch does with them.
//
// A receipt is an inside page still open: its catalog key (Kazma's
// ``database.away``, the guard's ``guard.restarting``...), when it was paged
// and whether a channel took it. Names and times only. Kazma Watch stays
// quiet about a condition the heartbeat says was paged and delivered; it
// pages when
//   - the condition is one only it can see (its catalog entry owns it);
//   - the inside page was not delivered within five minutes, or none was
//     made five minutes after Kazma Watch would have paged on its own;
//   - the heartbeats stop (machine_unreachable, guard_gone, heartbeat_silent: its own).
// A heartbeat with no receipts at all (a guard from before Stage 2) is read
// as before: Kazma Watch pages what it sees.
//
// A Cloudflare notice Kazma Watch already paged (cloudflare.js) answers for
// a condition of the same cause the same way.

import { CATALOG } from "./catalog.js";

export const PAGES_MAX = 16;
// How long an inside page may take to reach someone before Kazma Watch pages.
export const INSIDE_GRACE_S = 300;

const ENTRIES = new Map(CATALOG.alerts.map((a) => [a.key, a]));
const INSIDE = new Set(["ops", "guard"]);
const ANSWERED_BY = CATALOG.answered_by || {};
const SAME_CAUSE = CATALOG.same_cause || {};
const KEY_RE = /^[a-z_]+(\.[a-z_:{}]+)+$/;

export function entryOf(key) {
  return ENTRIES.get(key) || null;
}

function opensIncident(entry) {
  return !!(entry && INSIDE.has(entry.sender) && (entry.closed_by || entry.condition));
}

/**
 * The receipts as the guard sends them (receipts_allowlist in the guard):
 * a catalog key of an inside sender, a time, delivered. ``null`` when the
 * body carries none (an older guard): the old rules then.
 */
export function sanitizePages(pages) {
  if (!Array.isArray(pages)) return null;
  const out = new Map();
  for (const p of pages) {
    if (!p || typeof p !== "object") continue;
    const key = typeof p.key === "string" && KEY_RE.test(p.key) ? p.key : null;
    const at = p.paged_at;
    if (!key || !opensIncident(entryOf(key))) continue;
    if (typeof at !== "number" || !Number.isFinite(at) || at < 1e9 || at >= 1e10) continue;
    out.set(key, { key, paged_at: Math.trunc(at), delivered: p.delivered === true });
  }
  return [...out.values()].sort((a, b) => (a.key < b.key ? -1 : a.key > b.key ? 1 : 0)).slice(0, PAGES_MAX);
}

/** Kazma Watch pages this condition itself whatever was paged inside. */
export function watchOwns(condition) {
  const entry = ENTRIES.get(`watch.${condition}`);
  return !entry || entry.owner !== false;
}

/** Two of Kazma Watch's conditions one cause shows as (SAME_CAUSE). */
export function sameCause(a, b) {
  if (!a || !b) return false;
  return a === b || (SAME_CAUSE[a] || []).includes(b) || (SAME_CAUSE[b] || []).includes(a);
}

/** The inside receipt that answers for Kazma Watch's ``condition``, if any. */
export function answeringReceipt(condition, pages) {
  const conditions = new Set([condition, ...(ANSWERED_BY[condition] || [])]);
  const found = (pages || []).filter((p) => {
    const entry = entryOf(p.key);
    return entry && conditions.has(entry.condition);
  });
  found.sort((a, b) => (b.delivered - a.delivered) || (a.paged_at - b.paged_at));
  return found[0] || null;
}

/**
 * What Kazma Watch does with a bad condition it has not paged yet:
 * ``{act: "page"}``, ``{act: "hold", by}`` (someone else told the owner) or
 * ``{act: "wait"}`` (an inside page may still come), with a ``note`` for the
 * page when there is something to say.
 *
 * ``pages``: the fresh heartbeat's receipts (null: none sent, the old
 * rules); ``notices``: Cloudflare notices Kazma Watch paged that are still
 * open ({key, condition, delivered}); ``badSince``/``openAfter``: when the
 * condition began and when Kazma Watch would page it on its own.
 */
export function decide(condition, { pages, notices, now, badSince, openAfter }) {
  const notice = (notices || []).find((n) => n.delivered && sameCause(n.condition, condition));
  if (notice) return { act: "hold", by: notice.key };
  if (pages === null || pages === undefined || watchOwns(condition)) return { act: "page" };
  const r = answeringReceipt(condition, pages);
  if (r && r.delivered) return { act: "hold", by: r.key };
  if (r) {
    if (now - r.paged_at < INSIDE_GRACE_S) return { act: "wait" };
    return { act: "page", note: `${titleOfKey(r.key)}: Kazma's own page has not reached anyone.` };
  }
  if (now - badSince < openAfter + INSIDE_GRACE_S) return { act: "wait" };
  return { act: "page", note: "Kazma has not paged it." };
}

/** The title of a catalog key (an inside page, a Cloudflare notice). */
export function titleOfKey(key) {
  return (ENTRIES.get(key) || {}).title || key;
}

/** The inside pages open when the heartbeats stopped, for a page's note. */
export function openInside(pages) {
  return (pages || []).filter((p) => p.delivered).map((p) => p.key);
}
