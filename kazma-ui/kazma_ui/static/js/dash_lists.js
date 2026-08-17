/** Dashboard session/trace card HTML — used by dashboard.js and tests. */
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
    var d = new Date(iso);
    if (isNaN(d.getTime())) return String(iso);
    return d.toLocaleString();
  }

  function buildSessionCard(s) {
    s = s || {};
    var tid = String(s.thread_id || "unknown");
    var plat = String(s.platform || "unknown");
    var name = String(s.display_name || "anonymous");
    var msgs = String(s.message_count || 0);
    var tokens = String(s.context_tokens || 0);
    var when = formatWhen(s.created_at);
    return (
      '<article class="dash-mobile-card" data-thread-id="' + escapeHtml(tid) + '">' +
        '<div class="dash-mobile-card-top">' +
          '<span class="badge badge-basic">' + escapeHtml(plat) + "</span>" +
          '<strong class="dash-mobile-card-name">' + escapeHtml(name) + "</strong>" +
        "</div>" +
        '<code class="dash-mobile-card-id">' + escapeHtml(tid) + "</code>" +
        '<div class="dash-mobile-card-meta">' +
          escapeHtml(msgs) + " msgs · " + escapeHtml(tokens) + " tok · " + escapeHtml(when) +
        "</div>" +
        '<button type="button" class="btn btn-sm btn-danger dash-session-delete" data-thread-id="' +
          escapeHtml(tid) + '">Delete</button>' +
      "</article>"
    );
  }

  function buildTraceCard(t) {
    t = t || {};
    return (
      '<article class="dash-mobile-card">' +
        '<div class="dash-mobile-card-top">' +
          '<span class="badge badge-basic">' + escapeHtml(t.trace_type || t.type || "") + "</span>" +
          '<span class="badge ' + escapeHtml(t.badge_class || "") + '">' +
            escapeHtml(t.status || "") + "</span>" +
        "</div>" +
        '<div class="dash-mobile-card-name">' + escapeHtml(t.label || "") + "</div>" +
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
    buildSessionCard: buildSessionCard,
    buildTraceCard: buildTraceCard,
  };
})(typeof globalThis !== "undefined" ? globalThis : this);
