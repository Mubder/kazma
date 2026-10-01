/**
 * Settings -> Restore settings backup: preview first, then write exactly the
 * plan the owner confirmed.
 * Run: node tests/js/test_settings_restore_flow.js
 *
 * The composed Settings component (settingsApp() over the real mixin files)
 * runs against a fake server:
 *   - the preview is a dry run carrying the backup text; nothing is written
 *     before the owner confirms, and a cancel writes nothing;
 *   - the write carries the confirmed plan's digest (expect=), so a plan that
 *     changed since is refused by the server (409) -- and the page then shows
 *     the plan as it is now and asks again;
 *   - the preview names settings and counts, never a value;
 *   - a backup that changes nothing asks nothing; a restart is offered when
 *     the server recommends one; undo previews, confirms and sends its digest;
 *   - Import/Export restores through the same preview, sections expanded.
 * Negative control: a restore that writes without the digest is caught.
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const jsDir = path.join(__dirname, "..", "..", "kazma-ui", "kazma_ui", "static", "js");
const MIXINS = ["settings_core.js", "settings_hub.js", "settings_agent.js",
  "settings_integrations.js", "settings_ops.js"];

let fail = 0;
function ok(name, cond, detail) {
  if (!cond) {
    fail++;
    console.error("FAIL", name, detail === undefined ? "" : JSON.stringify(detail));
  } else {
    console.log("ok  ", name);
  }
}

function fill(text, vars) {
  let s = String(text);
  if (vars) for (const k of Object.keys(vars)) s = s.split("{" + k + "}").join(String(vars[k]));
  return s;
}

function plan(digest, changed) {
  return {
    backup: { format: "backup", created_at: "2026-09-30T10:00:00+00:00", kazma_version: "0.11.0" },
    changed: changed || ["agent.language", "providers.list"],
    lists: { "providers.list": { added: ["deepseek"], updated: [] } },
    unchanged: 3,
    kept: { runtime: 0, credentials: 0, keys: 1, learned: 0, retired_defaults: 0 },
    kept_keys: ["connectors.slack.bot_token"],
    keys_restored: ["providers.list:deepseek.api_key"],
    keys_to_reenter: [],
    refused: {},
    not_selected: 0,
    digest,
  };
}

/** A Settings page in a sandbox; *server* answers kazmaSave / kazmaGetJson. */
function load(server, patch) {
  const ui = { toasts: [], confirms: [], alerts: [], saves: [], gets: [], restarts: [] };
  const sb = { console, setTimeout, clearTimeout, setInterval, clearInterval, URL, URLSearchParams };
  sb.window = sb;
  sb.globalThis = sb;
  sb.showToast = (msg, type) => ui.toasts.push({ msg: String(msg), type: type || "info" });
  sb.kazmaT = (key, en, vars) => fill(en, vars);
  sb.t = (key) => key;
  sb.tOr = (key, fallback) => fallback;
  sb.fetch = async () => ({ ok: true, status: 200, json: async () => ({}), text: async () => "{}" });
  sb.kazmaSave = async (url, init) => {
    ui.saves.push({ url: String(url), body: init && init.body });
    return server.save(String(url), init);
  };
  sb.kazmaGetJson = async (url) => { ui.gets.push(String(url)); return server.get(String(url)); };
  sb.kazmaConfirm = async (opts) => { ui.confirms.push(opts); return server.confirm(opts); };
  sb.kazmaAlert = async (opts) => { ui.alerts.push(opts); };
  sb.kazmaPrompt = async () => null;
  const el = { style: {}, setAttribute() {}, removeAttribute() {}, classList: { add() {}, remove() {}, toggle() {} } };
  sb.document = {
    documentElement: el,
    getElementById: () => null,
    querySelector: () => null,
    querySelectorAll: () => [],
  };
  sb.Alpine = { $data: () => null, store: () => ({}) };
  sb.ModelsManager = { refresh() {}, load() {}, setDefault() {} };
  sb.location = { href: "http://kazma.test/settings", search: "", pathname: "/settings", hash: "" };
  sb.history = { replaceState() {} };
  sb.localStorage = { getItem() { return null; }, setItem() {}, removeItem() {} };
  sb.matchMedia = () => ({ matches: false, addEventListener() {} });
  for (const f of MIXINS.concat(["settings.js"])) {
    let src = fs.readFileSync(path.join(jsDir, f), "utf8");
    if (patch && patch.file === f) {
      if (!src.includes(patch.from)) throw new Error("negative control: patch target moved in " + f);
      src = src.replace(patch.from, patch.to);
    }
    vm.runInNewContext(src, sb, { filename: f });
  }
  const app = sb.settingsApp();
  app.restartServer = async (opts) => { ui.restarts.push(opts); };
  return { app, ui };
}

function server(opts) {
  const o = Object.assign({ confirm: true, plans: [plan("d1")], restart: true }, opts || {});
  let previews = 0;
  return {
    confirm: () => o.confirm,
    get: (url) => (o.undo ? o.undo : { status: "ok", available: false }),
    save: async (url) => {
      if (url.includes("/restore/undo")) {
        return { status: "ok", available: true, reverted: 2, restart_recommended: true };
      }
      if (url.includes("dry_run=true")) {
        return { status: "ok", dry_run: true, plan: o.plans[Math.min(previews++, o.plans.length - 1)] };
      }
      const expect = new URL("http://x" + url).searchParams.get("expect");
      if (o.changedOnce && !o._refused) {
        o._refused = true;
        const err = new Error("The settings changed since the preview");
        err.status = 409;
        err.body = { status: "error", plan: plan("d2", ["agent.language"]) };
        throw err;
      }
      return { status: "ok", dry_run: false, restored: 2, plan: plan(expect), restart_recommended: o.restart };
    },
  };
}

const TEXT = "kazma_settings_backup: 1\nsettings:\n  agent.language: ar\n";

(async () => {
  // 1. Preview, confirm, write the confirmed plan, offer a restart.
  {
    const { app, ui } = load(server());
    await app.restoreSettings(TEXT, { source: "kazma-settings-2026-09-30.yaml" });
    const [preview, write] = ui.saves;
    ok("the preview is a dry run carrying the backup", preview && preview.url.includes("dry_run=true") && preview.body === TEXT, ui.saves);
    ok("one confirmation before anything is written", ui.confirms.length === 1, ui.confirms.length);
    ok("the write carries the confirmed plan's digest", write && write.url.includes("expect=d1") && !write.url.includes("dry_run"), ui.saves);
    const msg = ui.confirms[0] && ui.confirms[0].message;
    ok("the preview names the settings and the source", msg.includes("agent.language") && msg.includes("kazma-settings-2026-09-30.yaml"), msg);
    ok("the preview says which keys come back without entering them", msg.includes("Keys brought back") && msg.includes("providers.list:deepseek.api_key"), msg);
    ok("the preview says nothing is deleted", msg.includes("Nothing is deleted"), msg);
    ok("a restart is offered when the server recommends one", ui.restarts.length === 1, ui.restarts);
    ok("no error shown", !ui.toasts.some((t) => t.type === "error"), ui.toasts);
    ok("the undo is looked up after the write", ui.gets.some((u) => u.includes("/restore/undo")), ui.gets);
    ok("the button is free again", app.restoring === false);
  }
  // 2. Cancel writes nothing.
  {
    const { app, ui } = load(server({ confirm: false }));
    await app.restoreSettings(TEXT, {});
    ok("a cancelled preview writes nothing", ui.saves.length === 1 && ui.saves[0].url.includes("dry_run=true"), ui.saves);
  }
  // 3. The plan changed since the preview: shown again, then written with the new digest.
  {
    const { app, ui } = load(server({ changedOnce: true }));
    await app.restoreSettings(TEXT, {});
    const writes = ui.saves.filter((s) => !s.url.includes("dry_run"));
    ok("a refused write shows the plan as it is now and asks again", ui.confirms.length === 2, ui.confirms.length);
    ok("the second write carries the new plan's digest", writes.length === 2 && writes[1].url.includes("expect=d2"), writes);
  }
  // 4. A backup that changes nothing asks nothing.
  {
    const { app, ui } = load(server({ plans: [plan("d0", [])] }));
    await app.restoreSettings(TEXT, {});
    ok("nothing to change: an alert, no confirmation, no write", ui.alerts.length === 1 && ui.confirms.length === 0 && ui.saves.length === 1, ui);
  }
  // 5. No restart offered when the server does not recommend one.
  {
    const { app, ui } = load(server({ restart: false }));
    await app.restoreSettings(TEXT, {});
    ok("no restart offer without a recommendation", ui.restarts.length === 0, ui.restarts);
  }
  // 6. Import/Export goes through the same preview, with sections expanded.
  {
    const { app, ui } = load(server());
    app.importData = TEXT;
    app.importSelective = true;
    app.importSections = ["model", "agent"];
    await app.importConfig();
    const preview = ui.saves[0];
    const sections = preview && new URL("http://x" + preview.url).searchParams.get("sections");
    ok("import previews through the restore route", preview && preview.url.startsWith("/api/settings/system/restore?"), ui.saves);
    ok("the model section stands for its keys", sections === "models,providers,registry,llm,agent", sections);
    const { app: app2, ui: ui2 } = load(server());
    app2.importData = TEXT;
    app2.importSelective = true;
    app2.importSections = [];
    await app2.importConfig();
    ok("selective import with no section sends nothing", ui2.saves.length === 0 && ui2.toasts.some((t) => t.type === "error"), ui2);
  }
  // 7. Undo: preview, confirm, write with its digest.
  {
    const undo = { status: "ok", available: true, plan: { restored_at: "2026-10-01T02:00:00+00:00", reverted: ["agent.language"], kept_keys: [], changed_since: [], already: 0, digest: "u1" } };
    const { app, ui } = load(server({ undo }));
    await app.undoRestore();
    const write = ui.saves.find((s) => s.url.includes("/restore/undo"));
    ok("undo asks first", ui.confirms.length === 1 && ui.confirms[0].message.includes("agent.language"), ui.confirms);
    ok("undo writes with its digest", write && write.url.includes("expect=u1"), ui.saves);
    ok("the undo button goes away", app.restoreUndo === null);
  }

  // Negative control: a restore that writes without the confirmed digest.
  {
    const { app, ui } = load(server(), {
      file: "settings_ops.js",
      from: "const done = await post({ expect: plan.digest });",
      to: "const done = await post({});",
    });
    await app.restoreSettings(TEXT, {});
    const write = ui.saves.find((s) => !s.url.includes("dry_run"));
    ok("negative control: a write without the digest is caught", write && !write.url.includes("expect="), ui.saves);
  }

  if (fail) {
    console.error(fail + " failure(s)");
    process.exit(1);
  }
  console.log("all ok");
})();
