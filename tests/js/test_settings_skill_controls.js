/**
 * The Settings tab's skill switch and Uninstall reach the routes that act.
 * Run: node tests/js/test_settings_skill_controls.js
 *
 * Found 2026-10-01: Settings -> Skills posted to /api/settings/skills/*,
 * which wrote skills.<name>.enabled -- a key nothing reads (the switch is
 * skills.enabled.<id>, kazma_core/skills/switches.py) -- and "uninstalled"
 * every skill, built-in ones included, by writing that key, then said
 * "Skill uninstalled". The switch changed nothing and so did Uninstall.
 *
 * The composed Settings component (settingsApp() over the real mixin files)
 * runs in a sandbox: the switch must post the skill's id to
 * /api/skills/toggle, Uninstall to /api/skills/uninstall, and an uninstall
 * the server did not do ("not_found") must not be reported as done.
 * Negative control: the old switch is put back and must be caught.
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

/** A Settings page whose server answers *answer* to every save. */
function load(answer, patch) {
  const toasts = [];
  const saves = [];
  const sb = { console, setTimeout, clearTimeout, setInterval, clearInterval, URL, URLSearchParams };
  sb.window = sb;
  sb.globalThis = sb;
  sb.showToast = (msg, type) => toasts.push({ msg: String(msg), type: type || "info" });
  sb.kazmaT = (key, en, vars) => fill(en, vars);
  sb.t = (key) => key;
  sb.tOr = (key, fallback) => fallback;
  sb.fetch = async () => ({ ok: true, status: 200, json: async () => [], text: async () => "[]" });
  sb.kazmaSave = async (url, init) => {
    let body = init && init.body;
    if (typeof body === "string") body = JSON.parse(body);
    saves.push({ url: String(url), method: (init && init.method) || "GET", body });
    return answer;
  };
  sb.kazmaConfirm = async () => true;
  sb.kazmaAlert = async () => undefined;
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
    let src = fs.readFileSync(path.join(jsDir, f), "utf8").replace(/\r\n/g, "\n");
    if (patch && patch.file === f) {
      if (!src.includes(patch.from)) throw new Error("negative control: patch target moved in " + f);
      src = src.replace(patch.from, patch.to);
    }
    vm.runInNewContext(src, sb, { filename: f });
  }
  return { app: sb.settingsApp(), toasts, saves };
}

const NATIVE = { id: "native:browser_automation", name: "browser-automation", builtin: true };
const AGENT_SKILL = { id: "agent-skill:improve", name: "improve" };

function toggleReachesTheSwitch(saves) {
  const s = saves[0];
  return saves.length === 1 && s.url === "/api/skills/toggle" && s.method === "POST"
    && s.body && s.body.skill_id === NATIVE.id && s.body.enabled === false;
}

(async () => {
  {
    const { app, toasts, saves } = load({ status: "ok" });
    await app.toggleSkill(NATIVE, false);
    ok("the switch posts the skill's id to /api/skills/toggle", toggleReachesTheSwitch(saves), saves);
    ok("the switch reports what it did", toasts.some((t) => t.type === "success"), toasts);
  }
  {
    const { app, toasts, saves } = load({ status: "ok" });
    await app.uninstallSkill(AGENT_SKILL);
    const s = saves[0];
    ok("Uninstall posts the skill's id to /api/skills/uninstall",
      saves.length === 1 && s.url === "/api/skills/uninstall" && s.method === "POST"
        && s.body && s.body.skill_id === AGENT_SKILL.id, saves);
    ok("an uninstall the server did is reported as done",
      toasts.some((t) => t.type === "success") && !toasts.some((t) => t.type === "error"), toasts);
  }
  {
    const { app, toasts } = load({ status: "not_found" });
    await app.uninstallSkill(AGENT_SKILL);
    ok("an uninstall that removed nothing is not reported as done",
      toasts.some((t) => t.type === "error") && !toasts.some((t) => t.type === "success"), toasts);
  }
  {
    const { saves } = load({ status: "ok" });
    const src = MIXINS.map((f) => fs.readFileSync(path.join(jsDir, f), "utf8")).join("\n");
    ok("no Settings script calls the retired /api/settings/skills routes",
      !/['"`]\/api\/settings\/skills/.test(src), saves);
  }

  // Negative control: the old switch, put back.
  {
    const { app, saves } = load({ status: "ok" }, {
      file: "settings_integrations.js",
      from: "await window.kazmaSave('/api/skills/toggle', {\n                    method: 'POST',\n                    body: { skill_id: skill.id, enabled: !!enabled },\n                });",
      to: "await window.kazmaSave(`/api/settings/skills/${encodeURIComponent(skill.name)}/toggle`, {\n                    method: 'PUT',\n                    body: JSON.stringify({ enabled }),\n                });",
    });
    await app.toggleSkill(NATIVE, false);
    ok("negative control: the old switch is caught", !toggleReachesTheSwitch(saves), saves);
  }

  if (fail) {
    console.error(fail + " failure(s)");
    process.exit(1);
  }
  console.log("all ok");
})();
