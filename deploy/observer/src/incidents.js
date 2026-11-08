// Incidents: one page when a bad condition has lasted three minutes, a
// reminder every hour while it lasts, one recovery page with how long it
// lasted. Planned maintenance pages nothing. A page no channel took is tried
// again the next minute.
//
// ``step`` is pure: it takes what the observer knew a minute ago and the
// diagnosis now, and returns the new state and the pages to send. The run
// (index.js) sends them and records which went out (``settle``).

import { formatOperatorCard } from "./card.js";
import { GOOD, severityOf, titleOf } from "./diagnose.js";

// Three failed minutes: the runs are a minute apart, so the third bad
// reading (120 s after the first) opens the incident; 10 s of slack.
export const OPEN_AFTER_S = 110;
export const REMIND_EVERY_S = 3600;
// A recovery page no channel took is given up after this long.
export const RECOVERY_RETRY_S = 3600;

export function duration(seconds) {
  const minutes = Math.max(1, Math.round(seconds / 60));
  if (minutes < 60) return `${minutes} min`;
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return m ? `${h} h ${m} min` : `${h} h`;
}

// Times are UTC until the owner's zone reaches Kazma Watch (Settings ->
// Alerts, the unified alerts plan's Stage 3).
function clock(epoch) {
  return new Date(epoch * 1000).toISOString().slice(0, 16).replace("T", " ") + " UTC";
}

/**
 * prev: {condition, detail, bad_since, incident}; incident:
 * {id, cause, detail, causes[], started_at, paged_at, last_page_at, ended_at,
 *  recovery_paged_at} or null.
 */
export function step(prev, diag, now) {
  const state = {
    condition: diag.condition,
    detail: diag.detail || null,
    bad_since: prev.bad_since ?? null,
    incident: prev.incident ? { ...prev.incident, causes: [...(prev.incident.causes || [])] } : null,
  };
  const pages = [];
  const incident = state.incident;

  // A recovery page still to send: retried, then given up.
  if (incident && incident.ended_at) {
    if (now - incident.ended_at > RECOVERY_RETRY_S) {
      state.incident = null;
    } else {
      pages.push({ kind: "recovered" });
      return finish(state, diag, now, pages);
    }
  }

  if (GOOD.has(diag.condition)) {
    state.bad_since = null;
    if (state.incident && diag.condition === "up") {
      state.incident.ended_at = now;
      pages.push({ kind: "recovered" });
    }
    return finish(state, diag, now, pages);
  }

  if (state.bad_since === null) state.bad_since = now;
  if (!state.incident) {
    if (now - state.bad_since >= OPEN_AFTER_S) {
      state.incident = {
        id: null,
        cause: diag.condition,
        detail: diag.detail || null,
        causes: [diag.condition],
        started_at: state.bad_since,
        paged_at: null,
        last_page_at: null,
        ended_at: null,
        recovery_paged_at: null,
      };
      pages.push({ kind: "open" });
    }
    return finish(state, diag, now, pages);
  }

  const open = state.incident;
  if (open.cause !== diag.condition) {
    open.cause = diag.condition;
    if (!open.causes.includes(diag.condition)) open.causes.push(diag.condition);
  }
  open.detail = diag.detail || null;
  if (open.paged_at === null) pages.push({ kind: "open" });
  else if (now - (open.last_page_at || open.paged_at) >= REMIND_EVERY_S) pages.push({ kind: "remind" });
  return finish(state, diag, now, pages);
}

function finish(state, diag, now, pages) {
  return { state, pages: pages.map((p) => ({ ...p, text: pageText(p.kind, state.incident, now) })) };
}

/**
 * The page for ``kind`` ("open", "remind", "recovered"): Kazma's operator
 * card with the source Watch (card.js), its title and severity from the
 * alert catalog.
 */
export function pageText(kind, incident, now) {
  if (!incident) return "";
  if (kind === "open" || kind === "remind") {
    return formatOperatorCard("watch", severityOf(incident.cause), titleOf(incident.cause),
      incident.detail || "", {
        form: kind === "remind" ? "reminder" : "page",
        since: clock(incident.started_at),
        duration: duration(now - incident.started_at),
      });
  }
  const end = incident.ended_at || now;
  const lines = [`It was: ${titleOf(incident.causes[0] || incident.cause)}.`];
  if (incident.causes.length > 1) lines.push(`On the way: ${incident.causes.map(titleOf).join("; ")}.`);
  // "🟢 [Watch] Kazma is back: it lasted 12 min." (watch.recovered)
  return formatOperatorCard("watch", "success", "Kazma", lines.join("\n"),
    { form: "recovery", duration: duration(end - incident.started_at) });
}

/** Record that ``page`` went out (``sent``) on the state's incident. */
export function settle(state, page, sent, now) {
  const incident = state.incident;
  if (!incident || !sent) return state;
  if (page.kind === "recovered") {
    incident.recovery_paged_at = now;
    return { ...state, incident: null, closed: incident };
  }
  if (incident.paged_at === null) incident.paged_at = now;
  incident.last_page_at = now;
  return state;
}
