/**
 * Settings -> Alerts' mixin (static/js/settings_alerts.js), the unified
 * alerts plan's Stage 3. Run: node tests/js/test_settings_alerts.js
 *
 * - Save sends only the fields that changed.
 * - A reload after a test page or a pairing keeps what the owner typed and
 *   did not save yet (it stays unsaved); with nothing typed it shows the
 *   server's values. Control: the reload as written first, which overwrote
 *   an unsaved chat with the stored one.
 * - The guard's test page polls until the guard answers, and says so.
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const jsDir = path.join(__dirname, "..", "..", "kazma-ui", "kazma_ui", "static", "js");
const SOURCE = fs.readFileSync(path.join(jsDir, "settings_alerts.js"), "utf8");

let fail = 0;
function ok(name, cond, detail) {
  if (!cond) {
    fail++;
    console.error("FAIL", name, detail === undefined ? "" : detail);
  } else {
    console.log("ok  ", name);
  }
}

function server(state) {
  return {
    routes: { chosen: state.routes, names: ["telegram", "telegram-group", "discord", "slack"],
      ready: { telegram: true } },
    lifecycle: { events: state.lifecycle, names: ["started", "startup_failed", "starting", "shutting_down"] },
    notices_to_chats: state.notices,
    guard: { chat: state.chat, guard: "running", tests: true, pager: null },
    watch: { paired: false, email_to: state.email || "" },
  };
}

function component(source) {
  const sent = [];
  const stored = { routes: ["telegram"], lifecycle: ["started"], notices: true, chat: "111", email: "" };
  const sb = {
    console, setTimeout: (fn) => { fn(); return 0; }, clearTimeout() {},
    showToast() {},
    KazmaFormat: { time: () => "t", relative: () => "r", dateTime: () => "d", number: String },
  };
  sb.window = sb;
  sb.kazmaGetJson = async (url) => {
    if (url === "/api/settings/alerts") return server(stored);
    if (url === "/api/settings/alerts/recent") return { items: [], errors: [] };
    if (url.startsWith("/api/settings/alerts/guard-test?")) {
      sb.polls = (sb.polls || 0) + 1;
      return sb.polls < 3 ? { state: "sending" } : { state: "sent", chat: "111" };
    }
    return null;
  };
  sb.kazmaSave = async (url, init) => {
    sent.push({ url, init });
    if (url === "/api/settings/alerts/guard-test") return { ok: true, id: "abcd1234", requested_at: 1 };
    return { ok: true };
  };
  vm.runInNewContext(source, sb, { filename: "settings_alerts.js" });
  const c = sb.KazmaSettingsMixins.alerts();
  const app = {};
  Object.defineProperties(app, Object.getOwnPropertyDescriptors(c));
  return { app, sent, stored };
}

(async () => {
  // Save: only what changed.
  let { app, sent } = component(SOURCE);
  await app.loadAlerts();
  ok("loaded from the server", app.alerts.loaded && app.alerts.guardChat === "111" && !app.alertsDirty);
  app.toggleAlertList("routes", "slack", true);
  ok("a change is unsaved", app.alertsDirty);
  await app.saveAlerts();
  const put = sent.find((s) => s.url === "/api/settings/alerts");
  ok("save sends only the changed field", put && JSON.stringify(put.init.body) === JSON.stringify({ routes: ["telegram", "slack"] }),
    put && put.init.body);

  // A reload keeps what was typed and not saved.
  ({ app } = component(SOURCE));
  await app.loadAlerts();
  app.alerts.guardChat = "-100222";
  app.alerts.watchEmail = "owner@example.com";
  await app.loadAlerts();
  ok("an unsaved chat survives a reload", app.alerts.guardChat === "-100222", app.alerts.guardChat);
  ok("and is still unsaved", app.alertsDirty);
  ok("a typed email survives a reload", app.alerts.watchEmail === "owner@example.com");
  app.resetAlerts();
  ok("Cancel puts the stored chat back", app.alerts.guardChat === "111" && !app.alertsDirty);

  // Control: the reload as first written overwrote it.
  const old = SOURCE.replace("const edits = this.alerts.loaded && this.alertsDirty ? this._alertsDiffable() : null;",
    "const edits = null;");
  ok("control: the replacement applies", old !== SOURCE);
  ({ app } = component(old));
  await app.loadAlerts();
  app.alerts.guardChat = "-100222";
  await app.loadAlerts();
  ok("control: without it the unsaved chat is lost", app.alerts.guardChat === "111");

  // The guard's test page: asked, polled, answered.
  ({ app } = component(SOURCE));
  await app.loadAlerts();
  await app.sendGuardTestPage();
  ok("the answer is said", !app.guardTest.busy && app.guardTest.tone === "tone-success"
    && app.guardTest.note.includes("111"), app.guardTest);

  if (fail) {
    console.error(`${fail} failed`);
    process.exit(1);
  }
  console.log("all ok");
})();
