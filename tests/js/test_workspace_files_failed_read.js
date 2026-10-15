/**
 * The Workspace file list shows only files the server listed (2026-10-15).
 * Run: node tests/js/test_workspace_files_failed_read.js
 *
 * When /api/workspace/files failed -- a refused response or no network --
 * loadFiles() filled the list with fallbackFiles(): kazma-core/, kazma-ui/,
 * kazma-gateway/, tests/, README.md, requirements.txt. Those were in no
 * workspace; the page presented them as the operator's files, and clicking
 * one opened a chat about a file that was not there. A failed read now marks
 * the list failed (the template shows a "Request failed" line),
 * keeps the listing the page already had (still true of the folder shown),
 * and invents nothing. An empty successful answer is the empty state.
 *
 * workspaceApp() runs from templates/workspace.html in a sandbox. Negative
 * control: the old loadFiles (with its fallback) invents files and is caught.
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const TEMPLATE = path.join(__dirname, "..", "..", "kazma-ui", "kazma_ui", "templates", "workspace.html");
const html = fs.readFileSync(TEMPLATE, "utf8").replace(/\r\n/g, "\n");

let fail = 0;
function ok(name, cond, detail) {
  if (!cond) {
    fail++;
    console.error("FAIL", name, detail === undefined ? "" : JSON.stringify(detail));
  } else {
    console.log("ok  ", name);
  }
}

function pageScript() {
  const start = html.indexOf("<script>\nfunction workspaceApp()");
  if (start < 0) throw new Error("workspaceApp() script not found in workspace.html");
  const end = html.indexOf("</script>", start);
  return html.slice(start + "<script>".length, end);
}

/** The loadFiles that shipped until 2026-10-15, with its fallback listing. */
const OLD_LOAD_FILES = `
async function oldLoadFiles(path) {
  path = path || '';
  try {
    const resp = await fetch('/api/workspace/files?path=' + encodeURIComponent(path));
    if (resp.ok) {
      const data = await resp.json();
      this.files = data.files || [];
      this.currentPath = path || '/';
    } else {
      this.files = fallbackFiles();
    }
  } catch (e) {
    this.files = fallbackFiles();
  }
}
function fallbackFiles() {
  return [
    { name: 'kazma-core/', is_dir: true, path: '/kazma-core', size: '—' },
    { name: 'README.md', is_dir: false, path: '/README.md', size: '—' },
  ];
}
`;

function app(answer, { old = false } = {}) {
  const context = { fetch: async (url) => answer(url), console, t: (k) => k, window: {} };
  vm.createContext(context);
  vm.runInContext(pageScript() + "\n" + OLD_LOAD_FILES + "\nthis.workspaceApp = workspaceApp; this.oldLoadFiles = oldLoadFiles;", context);
  const a = context.workspaceApp();
  if (old) a.loadFiles = context.oldLoadFiles;
  return a;
}

const SERVER_FILES = [{ name: "notes.md", is_dir: false, path: "notes.md", size: "1 KB" }];
const listed = async () => ({ ok: true, json: async () => ({ files: SERVER_FILES, path: "", parent: "" }) });
const emptyFolder = async () => ({ ok: true, json: async () => ({ files: [], path: "docs", parent: "" }) });
const refused = async () => ({ ok: false, status: 500, json: async () => ({ detail: "boom" }) });
const offline = async () => { throw new TypeError("Failed to fetch"); };
// The route answers a folder it refuses with 200, an error and no files.
const outside = async () => ({ ok: true, json: async () => ({ files: [], path: "", parent: "", error: "Path outside workspace" }) });

function invented(files) {
  return (files || []).filter((f) => !SERVER_FILES.some((s) => s.path === f.path));
}

(async () => {
  for (const [label, failure] of [["refused", refused], ["offline", offline], ["error answer", outside]]) {
    const a = app(failure);
    await a.loadFiles("");
    ok(`${label}: nothing is invented`, a.files.length === 0, a.files);
    ok(`${label}: the list is marked failed`, a.filesFailed === true);
    ok(`${label}: the empty state is not claimed`, a.filesLoaded === false);
  }

  {
    const a = app(emptyFolder);
    await a.loadFiles("docs");
    ok("an empty folder is the empty state", a.filesLoaded === true && a.filesFailed === false && a.files.length === 0);
    ok("an empty folder is the folder shown", a.currentPath === "docs");
  }

  {
    // A listing, then a navigation that fails: the listing (still true of
    // currentPath) stays, the failure is said, the path does not move.
    const answers = [listed, refused];
    const a = app((url) => answers.shift()(url));
    await a.loadFiles("");
    await a.loadFiles("secret");
    ok("a failed navigation keeps the true listing", invented(a.files).length === 0 && a.files.length === 1, a.files);
    ok("a failed navigation is said", a.filesFailed === true);
    ok("a failed navigation does not move the path", a.currentPath === "/", a.currentPath);
  }

  ok("no fallback listing is left in the page", !/fallbackFiles|kazma-gateway\//.test(pageScript()));
  ok("the failure line is in the file list, from the catalog",
    html.includes(`<div x-show="filesFailed && !loading" role="alert"`) && html.includes("{{ t('common.request_failed') }}"));
  ok("the empty state waits for a successful read",
    html.includes(`x-show="filesLoaded && !filesFailed && files.length === 0 && !loading"`));

  // Negative control: the old loader invents files on a failed read.
  for (const failure of [refused, offline]) {
    const a = app(failure, { old: true });
    await a.loadFiles("");
    ok("control: the old loader invents files", invented(a.files).length > 0, a.files);
  }

  if (fail) {
    console.error(`${fail} check(s) failed`);
    process.exit(1);
  }
  console.log("workspace file list: a failed read invents nothing and says it failed");
})().catch((err) => { console.error(err); process.exit(1); });
