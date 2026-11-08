/**
 * The owner's accent: applied as the two theme variants, never over the
 * other inline styles on <html>, and never a default nobody chose.
 * Run: node tests/js/test_accent_family.js
 *
 * Found 2026-11-09 (the UI colour audit):
 *  - applyAccentColor wrote --accent itself inline, which beats both theme
 *    blocks: light and dark got the same hex (a dark-theme blue under white
 *    ink in light: 3.68:1), and only 4 of the ~12 accent shades followed.
 *    It now sets --accent-chosen-dark/-light (+ rgb, + ink); kazma.css
 *    derives --accent and every shade from them per theme.
 *  - base.html bound :style as a STRING on <html>; Alpine applies a string
 *    with setAttribute("style"), wiping the accent whenever the font size
 *    changed. It is an object binding now (checked below).
 *  - Settings applied the server default (the retired indigo #5e6ad2) as if
 *    chosen, and every Appearance save stored it.
 *
 * The variants must equal kazma_ui/accent_colors.py's (the server renders
 * them on <html>): both are held to tests/fixtures/accent_variants.json.
 * Negative controls: an apply that rewrites the whole style text, and the
 * old Settings save that sent the whole appearance object.
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const root = path.join(__dirname, "..", "..");
const jsDir = path.join(root, "kazma-ui", "kazma_ui", "static", "js");
const fixture = JSON.parse(fs.readFileSync(path.join(root, "tests", "fixtures", "accent_variants.json"), "utf8"));
const componentsSrc = fs.readFileSync(path.join(jsDir, "modules", "components.js"), "utf8").replace(/\r\n/g, "\n");

let fail = 0;
function ok(name, cond, detail) {
  if (cond) { console.log("ok  ", name); return; }
  fail += 1;
  console.error("FAIL", name, detail === undefined ? "" : detail);
}

/** A <html> whose inline style behaves like the browser's: per property. */
function fakeRoot() {
  const props = new Map();
  const style = {
    setProperty(name, value) { props.set(name, String(value)); },
    removeProperty(name) { props.delete(name); },
    getPropertyValue(name) { return props.get(name) || ""; },
    get cssText() { return Array.from(props, ([k, v]) => k + ": " + v).join("; "); },
    set cssText(text) {
      props.clear();
      String(text).split(";").forEach((part) => {
        const i = part.indexOf(":");
        if (i > 0) props.set(part.slice(0, i).trim(), part.slice(i + 1).trim());
      });
    },
  };
  return { style, props };
}

/** applyAccentColor + accentVariants from components.js, with *patch*. */
function loadAccent(patch) {
  const start = componentsSrc.indexOf("const _ACCENT_PROPS");
  const fnAt = componentsSrc.indexOf("export function applyAccentColor(");
  const end = componentsSrc.indexOf("\n}\n", fnAt) + 3;
  if (start < 0 || fnAt < start || end < fnAt) throw new Error("accent code not found in components.js");
  let code = componentsSrc.slice(start, end).replace(/export function/g, "function");
  if (patch) {
    if (!code.includes(patch.from)) throw new Error("negative control: patch target moved");
    code = code.replace(patch.from, patch.to);
  }
  const html = fakeRoot();
  const sb = { document: { documentElement: html }, Math, Object, parseInt };
  vm.runInNewContext(code + "\nthis.applyAccentColor = applyAccentColor; this.accentVariants = accentVariants;", sb);
  return { sb, html };
}

// ── 1. The variants equal the server's (the shared fixture) ──
{
  const { sb } = loadAccent();
  for (const [hex, want] of Object.entries(fixture.cases)) {
    const got = sb.accentVariants(hex);
    ok("variants of " + hex + " match accent_colors.py", JSON.stringify(got) === JSON.stringify(want),
      JSON.stringify(got) + " vs " + JSON.stringify(want));
  }
  for (const bad of fixture.not_chosen) {
    ok("no variants for " + JSON.stringify(bad), sb.accentVariants(bad) === null);
  }
}

// ── 2. Applying sets the accent and keeps every other inline style ──
function checkApply(patch) {
  const problems = [];
  const { sb, html } = loadAccent(patch);
  html.style.setProperty("font-size", "15px");
  html.style.setProperty("--app-ivh", "700px");
  html.style.setProperty("--accent", "#5e6ad2");  // an older build's inline accent
  sb.applyAccentColor("#10b981");
  const want = fixture.cases["#10b981"];
  for (const [name, value] of Object.entries(want)) {
    if (html.style.getPropertyValue(name) !== value) problems.push(name + " = " + html.style.getPropertyValue(name));
  }
  if (html.style.getPropertyValue("font-size") !== "15px") problems.push("font-size was wiped");
  if (html.style.getPropertyValue("--app-ivh") !== "700px") problems.push("--app-ivh was wiped");
  if (html.style.getPropertyValue("--accent")) problems.push("--accent left inline (beats both themes)");
  sb.applyAccentColor("");
  for (const name of Object.keys(want)) {
    if (html.style.getPropertyValue(name)) problems.push(name + " not cleared by an empty accent");
  }
  if (html.style.getPropertyValue("font-size") !== "15px") problems.push("clearing wiped font-size");
  return problems;
}
{
  const problems = checkApply();
  ok("applyAccentColor sets both theme variants and keeps other inline styles", problems.length === 0, problems);
  const control = checkApply({
    from: "Object.keys(props).forEach((name) => root.style.setProperty(name, props[name]));",
    to: "root.style.cssText = Object.keys(props).map((n) => n + ':' + props[n]).join(';');",
  });
  ok("negative control: rewriting the whole style text is caught",
    control.some((p) => p.includes("wiped")), control);
}

// ── 3. base.html binds :style as an object (Alpine merges an object; a
//       string is setAttribute("style") and wipes the accent) ──
{
  const base = fs.readFileSync(path.join(root, "kazma-ui", "kazma_ui", "templates", "base.html"), "utf8");
  const tag = base.slice(base.indexOf("<html"), base.indexOf(">", base.indexOf("<html")) + 1);
  const m = tag.match(/:style="([^"]*)"/);
  ok("<html> has a :style binding", Boolean(m), tag);
  ok("<html>'s :style is an object, not a string", m && /^\s*\{/.test(m[1]), m && m[1]);
  ok("<html> renders the chosen accent server-side", tag.includes("accent_style()"), tag);
  const stringBinding = ":style=\"'font-size: ' + effectiveFontSize() + 'px'\"";
  ok("negative control: the old string binding is recognised as one",
    !/^\s*\{/.test(stringBinding.match(/:style="([^"]*)"/)[1]));
}

// ── 4. Settings never applies or saves an accent the owner did not choose ──
const MIXINS = ["settings_core.js", "settings_hub.js", "settings_agent.js",
  "settings_integrations.js", "settings_ops.js"];

function settingsPage(patch, appearanceAnswer) {
  const applied = [];
  const saved = [];
  const sb = { console, setTimeout, clearTimeout, setInterval, clearInterval, URL, URLSearchParams, Promise };
  sb.window = sb;
  sb.globalThis = sb;
  sb.showToast = () => {};
  sb.kazmaT = (key, en) => en;
  sb.kazmaCount = (key, n) => String(n);
  sb.t = (key) => key;
  sb.tOr = (key, fallback) => fallback;
  sb.kazmaApplyAccentColor = (hex) => applied.push(hex);
  sb.syncDocumentColorScheme = () => {};
  sb.kazmaSave = async (url, opts) => {
    if (String(url).includes("/appearance")) saved.push(JSON.parse(opts.body));
    return { ok: true };
  };
  sb.fetch = async (url) => ({
    ok: true, status: 200, statusText: "OK",
    json: async () => (String(url).endsWith("/api/settings/appearance") ? appearanceAnswer : {}),
    text: async () => "{}",
  });
  sb.kazmaConfirm = async () => true;
  sb.kazmaAlert = async () => undefined;
  sb.kazmaPrompt = async () => null;
  const el = { style: {}, setAttribute() {}, removeAttribute() {}, getAttribute() { return null; },
    classList: { add() {}, remove() {}, toggle() {} } };
  sb.document = { documentElement: el, getElementById: () => null, querySelector: () => null,
    querySelectorAll: () => [], addEventListener() {} };
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
  return { app: sb.settingsApp(), applied, saved };
}

async function loadThenSaveTheme(patch, answer) {
  const page = settingsPage(patch, answer);
  try { await Promise.race([page.app.init(), new Promise((r) => setTimeout(r, 3000))]); } catch (e) { /* other loaders */ }
  const loadedAccent = page.app.appearance.accent_color;
  page.app.appearance.theme = "dark";  // the owner changes only the theme
  await page.app.saveAppearance();
  return { page, loadedAccent };
}

(async () => {
  // The server default: no accent chosen (and an older install's indigo).
  for (const answer of [
    { theme: "light", accent_color: "", font_size: 14, accent_color_chosen: false },
    { theme: "light", accent_color: "#5e6ad2", font_size: 14, accent_color_chosen: false },
  ]) {
    const { page, loadedAccent } = await loadThenSaveTheme(null, answer);
    ok("not chosen (" + JSON.stringify(answer.accent_color) + "): Settings applies no accent",
      page.applied.length === 0, page.applied);
    ok("not chosen: the picker shows no accent", loadedAccent === "", loadedAccent);
    ok("not chosen: a theme save sends no accent",
      page.saved.length === 1 && !("accent_color" in page.saved[0]), page.saved);
  }
  // A chosen accent is applied, and a theme save does not resend it.
  {
    const answer = { theme: "light", accent_color: "#10b981", font_size: 14, accent_color_chosen: true };
    const { page } = await loadThenSaveTheme(null, answer);
    ok("chosen: Settings applies the owner's accent", page.applied.includes("#10b981"), page.applied);
    ok("chosen: a theme save does not resend it",
      page.saved.length === 1 && !("accent_color" in page.saved[0]), page.saved);
  }
  // The owner picks a colour: it is saved and applied; '' goes back to the theme.
  {
    const answer = { theme: "light", accent_color: "", font_size: 14, accent_color_chosen: false };
    const page = settingsPage(null, answer);
    try { await Promise.race([page.app.init(), new Promise((r) => setTimeout(r, 3000))]); } catch (e) { /* ignore */ }
    page.app.appearance.accent_color = "#f85149";
    await page.app.saveAppearance();
    ok("a picked accent is saved", page.saved.length === 1 && page.saved[0].accent_color === "#f85149", page.saved);
    ok("a picked accent is applied", page.applied[page.applied.length - 1] === "#f85149", page.applied);
    page.app.appearance.accent_color = "";
    await page.app.saveAppearance();
    ok("clearing it saves '' (the theme's accent)", page.saved[1] && page.saved[1].accent_color === "", page.saved);
  }
  // Negative control: the old load applied whatever the server returned.
  {
    const answer = { theme: "light", accent_color: "#5e6ad2", font_size: 14, accent_color_chosen: false };
    const src = fs.readFileSync(path.join(jsDir, "settings_core.js"), "utf8").replace(/\r\n/g, "\n");
    const from = src.slice(src.indexOf("if (!appearanceCfg.accent_color_chosen)"),
      src.indexOf("self._applyAccentColor(self.appearance.accent_color);"));
    const { page } = await loadThenSaveTheme({
      file: "settings_core.js", from,
      to: "if (typeof self._applyAccentColor === 'function') {\n                            ",
    }, answer);
    ok("negative control: a load that applies the server default is caught",
      page.applied.includes("#5e6ad2"), page.applied);
  }
  // Negative control: the old save sent the whole appearance object.
  {
    const answer = { theme: "light", accent_color: "#5e6ad2", font_size: 14, accent_color_chosen: false };
    const { page } = await loadThenSaveTheme({
      file: "settings_ops.js",
      from: "body: JSON.stringify(this._appearanceToSave()),",
      to: "body: JSON.stringify(Object.assign({}, this.appearance, { accent_color: '#5e6ad2' })),",
    }, answer);
    ok("negative control: a save that sends the page's default is caught",
      page.saved.length === 1 && page.saved[0].accent_color === "#5e6ad2", page.saved);
  }

  if (fail) {
    console.error("\n" + fail + " failure(s)");
    process.exit(1);
  }
  console.log("\nall accent checks passed");
})();
