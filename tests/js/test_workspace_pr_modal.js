/**
 * The Workspace PR viewer shows a pull request's own text as text.
 * Run: node tests/js/test_workspace_pr_modal.js
 *
 * Found 2026-10-01: ghOpenPr() escaped the PR body but put the title, the
 * reviewers' names and states and GitHub's merge state into the modal's
 * body -- rendered with x-html -- as they came. Anyone who can open a pull
 * request on the repository chooses its title, so a title such as
 * <img src=x onerror=...> ran script in the operator's page when the PR was
 * opened. The same function wrote its frame (labels, buttons) in English
 * whatever the page language.
 *
 * The page's inline script runs in a sandbox (workspaceApp() from
 * templates/workspace.html); a hostile PR is opened and the modal it builds
 * must hold no element from the PR's fields and the frame must come from
 * the catalog. Negative control: the title's escape is removed and must be
 * caught.
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const TEMPLATE = path.join(__dirname, "..", "..", "kazma-ui", "kazma_ui", "templates", "workspace.html");

let fail = 0;
function ok(name, cond, detail) {
  if (!cond) {
    fail++;
    console.error("FAIL", name, detail === undefined ? "" : JSON.stringify(detail));
  } else {
    console.log("ok  ", name);
  }
}

/** workspace.html's inline component script. */
function pageScript() {
  const html = fs.readFileSync(TEMPLATE, "utf8").replace(/\r\n/g, "\n");
  const start = html.indexOf("<script>\nfunction workspaceApp()");
  if (start < 0) throw new Error("workspaceApp() script not found in workspace.html");
  const end = html.indexOf("</script>", start);
  return html.slice(start + "<script>".length, end);
}

const HOSTILE = "<img src=x onerror=alert(1)>";
const PR = {
  number: 7,
  title: "Fix " + HOSTILE,
  body: "Body " + HOSTILE,
  state: "open",
  merged: false,
  draft: true,
  mergeable_state: "<b>clean</b>",
  additions: 3,
  deletions: 1,
  changed_files: 2,
  html_url: "https://github.com/o/r/pull/7",
  reviews: [{ user: "<svg onload=alert(2)>", state: "APPROVED" }],
};

async function openPr(patch) {
  let src = pageScript();
  if (patch) {
    if (!src.includes(patch.from)) throw new Error("negative control: patch target moved");
    src = src.replace(patch.from, patch.to);
  }
  const shown = [];
  const sb = { console, setTimeout, clearTimeout, setInterval, clearInterval, URL, URLSearchParams };
  sb.window = sb;
  // The catalog: every key the viewer asks for comes back marked, so the
  // test sees which words came from it.
  sb.t = (key, vars) => {
    let s = "[" + key + "]";
    if (vars) for (const k of Object.keys(vars)) s += " " + k + "=" + vars[k];
    return s;
  };
  sb.tOr = (key, fallback) => fallback;
  sb.fetch = async () => ({ ok: true, status: 200, json: async () => PR });
  sb.showModal = (opts) => shown.push(opts);
  sb.KazmaStream = { toast() {} };
  sb.localStorage = { getItem() { return null; }, setItem() {}, removeItem() {} };
  sb.document = { getElementById: () => null, querySelector: () => null, querySelectorAll: () => [] };
  vm.runInNewContext(src, sb, { filename: "workspace.html" });
  const app = sb.workspaceApp();
  await app.ghOpenPr(7);
  return shown[0];
}

/** Every tag in an HTML string, as the browser would parse it. */
function tags(html) {
  return (String(html).match(/<\s*([a-zA-Z][\w-]*)/g) || []).map((m) => m.replace(/[<\s]/g, "").toLowerCase());
}

(async () => {
  const modal = await openPr();
  ok("the viewer opens a modal", !!modal, modal);
  const found = tags(modal.body);
  ok("the modal body holds only the viewer's own elements",
    found.every((tag) => ["pre", "p", "br", "span"].includes(tag)), found);
  ok("the PR's title is shown as text", modal.body.includes("Fix &lt;img src=x onerror=alert(1)&gt;"), modal.body);
  ok("a reviewer's name is shown as text", modal.body.includes("&lt;svg onload=alert(2)&gt;"), modal.body);
  ok("the title comes from the catalog", modal.title.startsWith("[workspace.pr_modal_title]"), modal.title);
  ok("the buttons come from the catalog",
    modal.actions.every((a) => /^\[[a-z_.]+\]$/.test(a.label)), modal.actions.map((a) => a.label));
  ok("the frame's lines come from the catalog",
    ["[workspace.pr_diff_stats]", "[workspace.pr_state_line]", "[workspace.pr_reviews_line]"]
      .every((key) => modal.body.includes(key)), modal.body);

  // Negative control: the title put in unescaped, as before.
  const broken = await openPr({
    from: "#${esc(pr.number)} ${esc(pr.title)}",
    to: "#${esc(pr.number)} ${pr.title}",
  });
  ok("negative control: an unescaped title is caught",
    !tags(broken.body).every((tag) => ["pre", "p", "br", "span"].includes(tag)), tags(broken.body));

  if (fail) {
    console.error(fail + " failure(s)");
    process.exit(1);
  }
  console.log("all ok");
})();
