// The operator card, the same text Kazma and its guard send (the unified
// alerts plan, "One card"): kazma_core/observability/alert_card.py and the
// guard's stdlib copy in scripts/service/kazma_guard.py. All three are held
// to tests/fixtures/alert_card/cards.json (the observer's test, and
// tests/test_alert_catalog.py), so a page from Kazma Watch reads like one
// from Kazma.
//
//   🔴 [Watch] Kazma cannot be reached
//   The tunnel answers, the machine does not.
//   Since 14:02 UTC (12 min) · Open: https://my.kazma.ai/dashboard
//
//   🔴 ⏰ Still: [Watch] Kazma cannot be reached         (a reminder)
//   🟢 [Watch] Kazma is back: it lasted 12 min.          (a recovery)

const ICONS = {
  info: "\u{1f535}",
  success: "\u{1f7e2}",
  warn: "\u{1f7e1}",
  error: "\u{1f534}",
  critical: "\u{1f6a8}",
};

// The guard's info cards are orange (the operator's request, 2026-09-05).
const SOURCE_ICON_OVERRIDES = { guard: { info: "\u{1f7e0}" } };

const SEVERITY = { info: "Info", success: "Success", warn: "Warn", error: "Error", critical: "Critical" };

const SOURCES = {
  guard: "Guard",
  ops: "Ops",
  system: "System",
  watch: "Watch",
  cloudflare: "Cloudflare",
};

function own(table, key) {
  return Object.prototype.hasOwnProperty.call(table, key) ? table[key] : undefined;
}

// Python's str.strip(): whitespace off both ends.
function strip(value) {
  return String(value ?? "").trim();
}

/**
 * The card. ``form`` is "page" (the default), "reminder" or "recovery";
 * ``since`` (a time written for the reader) and ``duration`` ("12 min") make
 * the last line's "Since <since> (<duration>)", ``link`` its "Open: <link>".
 * A recovery is always green and puts ``duration`` in its first line.
 */
export function formatOperatorCard(source, severity, title, detail = "",
  { form = "page", since = "", duration = "", link = "" } = {}) {
  const srcKey = strip(source || "").toLowerCase().replace(/^[[\]]+|[[\]]+$/g, "");
  let src = own(SOURCES, srcKey) ?? (strip(source || "Ops") || "Ops");
  if (src.toLowerCase() === "gaurd") src = "Guard";
  const kind = strip(form || "page").toLowerCase();
  const sevKey = kind === "recovery" ? "success" : strip(severity || "warn").toLowerCase();
  const override = own(SOURCE_ICON_OVERRIDES, srcKey);
  const icon = (override && own(override, sevKey)) || own(ICONS, sevKey) || ICONS.warn;
  const head = strip(title || "") || own(SEVERITY, sevKey) || "Warn";
  const lasted = strip(duration || "");
  let first;
  if (kind === "reminder") first = `${icon} ⏰ Still: [${src}] ${head}`;
  else if (kind === "recovery") first = `${icon} [${src}] ${head} is back${lasted ? `: it lasted ${lasted}.` : "."}`;
  else first = `${icon} [${src}] ${head}`;
  const lines = [first];
  const body = strip(detail || "");
  if (body) lines.push(body);
  const footer = [];
  const when = strip(since || "");
  if (when && kind !== "recovery") footer.push(`Since ${when}${lasted ? ` (${lasted})` : ""}`);
  const target = strip(link || "");
  if (target) footer.push(`Open: ${target}`);
  if (footer.length) lines.push(footer.join(" · "));
  return lines.join("\n");
}
