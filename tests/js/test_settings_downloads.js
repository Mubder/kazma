/**
 * Settings' "Create backup" and "Download config" never save an error.
 * Run: node tests/js/test_settings_downloads.js
 *
 * Found 2026-09-28: both saved whatever the server answered as
 * kazma-backup-<date>.yaml / kazma-config.<format>, and said "Backup
 * downloaded". A 500 or a proxy's error page became a file that looks like a
 * backup until the day it is needed. The two methods are run from the real
 * settings_ops.js against a failing and a working server. Negative control:
 * the old createBackup body, without the status check, saves the error.
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const src = fs.readFileSync(
  path.join(__dirname, "..", "..", "kazma-ui", "kazma_ui", "static", "js", "settings_ops.js"), "utf8",
).replace(/\r\n/g, "\n");

let fail = 0;
function ok(name, cond, detail) {
  if (!cond) { fail++; console.error("FAIL", name, detail === undefined ? "" : detail); }
  else console.log("ok  ", name);
}

function run(code, status) {
  const saved = [];
  const toasts = [];
  const sb = {
    console,
    URL: { createObjectURL: () => "blob:x", revokeObjectURL() {} },
    showToast: (m, type) => toasts.push(type),
    fetch: async () => ({ ok: status < 400, status, blob: async () => ({ size: 3 }) }),
    document: {
      createElement: () => ({ set href(v) {}, set download(v) { this._d = v; }, click() { saved.push(this._d); } }),
    },
  };
  sb.window = sb;
  vm.runInNewContext(code, sb);
  const ops = sb.KazmaSettingsMixins.ops();
  ops.exportFormat = "yaml";
  return { ops, saved, toasts };
}

async function main() {
  for (const method of ["createBackup", "exportConfig"]) {
    const bad = run(src, 500);
    await bad.ops[method]();
    ok(method + ": a failed answer is not saved", bad.saved.length === 0, bad.saved);
    ok(method + ": and says it failed", bad.toasts.indexOf("error") >= 0 && bad.toasts.indexOf("success") < 0, bad.toasts);
    const good = run(src, 200);
    await good.ops[method]();
    ok(method + ": a good answer is saved", good.saved.length === 1, good.saved);
  }

  // Negative control: the old body saved the error page. Both downloads go
  // through _downloadSettings since 2026-10-01; the check is removed there.
  const old = src.replace("            if (!resp.ok) throw new Error('HTTP ' + resp.status);\n            const blob = await resp.blob();",
    "            const blob = await resp.blob();");
  ok("control: the check was removed", old !== src);
  const was = run(old, 500);
  await was.ops.createBackup();
  ok("control: the old createBackup saved a 500", was.saved.length === 1, was.saved);

  if (fail) { console.error(fail + " failed"); process.exit(1); }
  console.log("all ok");
}

main().catch((e) => { console.error(e); process.exit(1); });
