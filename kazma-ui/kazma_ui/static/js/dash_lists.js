/** Dashboard session/trace card HTML, and the labels the session table
 *  shares with it -- used by dashboard.js and tests. */
(function (root) {
  "use strict";

  function escapeHtml(str) {
    if (str == null || str === "") return "";
    return String(str).replace(/[&<>"]/g, function (c) {
      return ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c];
    });
  }

  function formatWhen(iso) {
    if (!iso) return "—";
    var fmt = root.KazmaFormat;
    if (fmt) return fmt.relative(iso) || String(iso);
    var d = new Date(iso);
    if (isNaN(d.getTime())) return String(iso);
    return d.toLocaleString();
  }

  /* A catalog string (window.t), the English fallback when the key is
     missing, with {name} placeholders filled from *vars*. */
  function tr(key, fallback, vars) {
    var text = typeof root.t === "function" ? root.t(key) : key;
    if (!text || text === key) text = fallback;
    for (var k in vars || {}) text = text.split("{" + k + "}").join(String(vars[k]));
    return text;
  }

  var PLATFORM_NAMES = { telegram: "Telegram", discord: "Discord", slack: "Slack" };

  /* A chat's platform as the page says it: names stay names, "web" and
     "gateway" are words and are translated. */
  function platformLabel(p) {
    p = String(p || "").toLowerCase();
    if (!p) return "—";
    if (p === "web") return tr("dashboard.platform_web", "Web");
    if (p === "gateway") return tr("dashboard.platform_gateway", "Gateway");
    return PLATFORM_NAMES[p] || p;
  }

  function buildSessionCard(s) {
    s = s || {};
    var tid = String(s.thread_id || "");
    var title = s.title
      ? '<strong class="dash-mobile-card-name" translate="no">' + escapeHtml(s.title) + "</strong>"
      : '<strong class="dash-mobile-card-name">' + escapeHtml(tr("dashboard.no_chat", "No chat")) + "</strong>";
    if (s.title && s.session_id) {
      title = '<a href="/chat?session=' + encodeURIComponent(s.session_id) + '">' + title + "</a>";
    }
    var messages = s.message_count == null ? "—" : String(s.message_count);
    return (
      '<article class="dash-mobile-card" data-thread-id="' + escapeHtml(tid) + '">' +
        '<div class="dash-mobile-card-top">' +
          '<span class="badge badge-basic">' + escapeHtml(platformLabel(s.platform)) + "</span>" +
          title +
        "</div>" +
        '<code class="dash-mobile-card-id" translate="no">' + escapeHtml(tid) + "</code>" +
        '<div class="dash-mobile-card-meta">' +
          escapeHtml(tr("dashboard.col_messages", "Messages")) + ": " + escapeHtml(messages) + " · " +
          escapeHtml(tr("dashboard.col_steps", "Saved steps")) + ": " + escapeHtml(String(s.steps || 0)) + " · " +
          escapeHtml(formatWhen(s.last_activity)) +
        "</div>" +
        '<button type="button" class="btn btn-sm btn-danger dash-session-delete" data-thread-id="' +
          escapeHtml(tid) + '">' + escapeHtml(tr("dashboard.delete", "Delete")) + "</button>" +
      "</article>"
    );
  }

  function buildTraceCard(t) {
    t = t || {};
    return (
      '<article class="dash-mobile-card">' +
        '<div class="dash-mobile-card-top">' +
          '<span class="badge badge-basic" translate="no">' + escapeHtml(t.trace_type || t.type || "") + "</span>" +
          '<span class="badge ' + escapeHtml(t.badge_class || "") + '">' +
            escapeHtml(t.status || "") + "</span>" +
        "</div>" +
        '<div class="dash-mobile-card-name" translate="no">' + escapeHtml(t.label || "") + "</div>" +
        '<div class="dash-mobile-card-meta">' +
          escapeHtml(t.time || "") + " · " +
          escapeHtml(String(t.duration_ms != null ? t.duration_ms + "ms" : "")) +
          " · " + escapeHtml(String(t.tokens != null ? t.tokens : "")) +
          " tok · " + escapeHtml(String(t.cost || "")) +
        "</div>" +
      "</article>"
    );
  }

  root.KazmaDashLists = {
    escapeHtml: escapeHtml,
    formatWhen: formatWhen,
    tr: tr,
    platformLabel: platformLabel,
    buildSessionCard: buildSessionCard,
    buildTraceCard: buildTraceCard,
  };
})(typeof globalThis !== "undefined" ? globalThis : this);
