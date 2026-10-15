/**
 * Settings -> Documents: where untrusted documents run.
 * Run: node tests/js/test_settings_document_profile.js
 *
 * The profile (security.execution.document_worker_isolation) is saved only
 * when the owner changed it from what the server reported: never blind (the
 * page could not read it), never while KAZMA_DOCUMENT_WORKER_ISOLATION in the
 * environment decides, and never as an unchanged "process" over "none
 * chosen". "Use the container profile" saves the container profile. The
 * notice shows when the install requires containers and documents do not run
 * in one. Negative control: a save that always sends the choice is caught.
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
    console.error("FAIL", name, detail === undefined ? "" : detail);
  } else {
    console.log("ok  ", name);
  }
}

/** The composed Settings component over the real files; *patch* edits one. */
function page(patch) {
  const sent = [];
  const sb = { console, setTimeout, clearTimeout, setInterval, clearInterval, URL, URLSearchParams };
  sb.window = sb;
  sb.globalThis = sb;
  sb.showToast = () => {};
  sb.kazmaT = (key, en) => String(en);
  sb.kazmaCount = (key, n) => String(n);
  sb.t = (key) => key;
  sb.tOr = (key, fallback) => fallback;
  sb.fetch = async (url, opts) => {
    if (opts && opts.method === "PUT") sent.push({ url: String(url), body: JSON.parse(opts.body) });
    return { ok: true, status: 200, json: async () => ({ status: "ok" }), text: async () => "{}" };
  };
  sb.kazmaSave = async () => ({ status: "ok" });
  sb.kazmaConfirm = async () => true;
  sb.kazmaAlert = async () => undefined;
  sb.kazmaPrompt = async () => null;
  const el = { style: {}, setAttribute() {}, removeAttribute() {}, classList: { add() {}, remove() {}, toggle() {} } };
  sb.document = { documentElement: el, getElementById: () => null, querySelector: () => null, querySelectorAll: () => [] };
  sb.Alpine = { $data: () => null, store: () => ({}) };
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
  // What the server reported (the page's loader does the same).
  app.load = (isolation) => {
    app.documents.worker_isolation = isolation;
    app._syncWorkerProfileChoice();
  };
  return { app, sent };
}

function profileSent(sent) {
  const docs = sent.filter((s) => s.url === "/api/settings/documents");
  return docs.length ? docs[docs.length - 1].body.worker_isolation : "(no save)";
}

async function scenarios(patch) {
  const out = {};
  let p = page(patch);
  await p.app.saveDocuments();
  out.blind = profileSent(p.sent);

  p = page(patch);
  p.app.load({ mode: "process", source: "default", setting: null, container_required: false });
  await p.app.saveDocuments();
  out.unchanged_default = profileSent(p.sent);

  p = page(patch);
  p.app.load({ mode: "process", source: "default", setting: null, container_required: false });
  p.app.documents.worker_profile_choice = "container";
  await p.app.saveDocuments();
  out.chosen = profileSent(p.sent);

  p = page(patch);
  p.app.load({ mode: "container", source: "environment", environment: "container", setting: null });
  p.app.documents.worker_profile_choice = "process";
  await p.app.saveDocuments();
  out.environment = profileSent(p.sent);
  out.environment_disabled = p.app.workerProfileFromEnvironment();

  p = page(patch);
  p.app.load({ mode: "process", source: "default", setting: null, container_required: true });
  out.notice = p.app.containerProfileNeeded();
  await p.app.useContainerProfile();
  out.use_container = profileSent(p.sent);
  p.app.load({ mode: "container", source: "setting", setting: "container", container_required: true });
  out.notice_after = p.app.containerProfileNeeded();
  return out;
}

(async () => {
  const r = await scenarios();
  ok("a page that could not read the profile never writes it", r.blind === undefined, r.blind);
  ok("an unchanged default is not written", r.unchanged_default === undefined, r.unchanged_default);
  ok("a change is written", r.chosen === "container", r.chosen);
  ok("the environment decides: the switch is off and nothing is written",
    r.environment_disabled === true && r.environment === undefined, r);
  ok("the notice shows when containers are required and documents run locally", r.notice === true);
  ok("'Use the container profile' saves the container profile", r.use_container === "container", r.use_container);
  ok("the notice goes once documents run in containers", r.notice_after === false);

  // Negative control: a save that always sends the choice writes blind and
  // over the environment.
  const naive = await scenarios({
    file: "settings_agent.js",
    from: "if (w && !this.workerProfileFromEnvironment() && choice && choice !== (w.setting || w.mode)) {",
    to: "if (choice) {",
  });
  ok("negative control: the naive save writes blind and over the environment",
    naive.blind === "process" && naive.environment === "process", naive);

  if (fail) {
    console.error(fail + " failure(s)");
    process.exit(1);
  }
  console.log("all ok");
})();
