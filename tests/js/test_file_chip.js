/**
 * The workbench file chip ("WROTE path") appears only for tools that write
 * files, and names the file they wrote.
 *
 * Live 2026-09-24: x_list_scheduled -- a read -- showed "WROTE Asia/Kuwait".
 * The chip guessed from the tool name and from any slash in the result, and
 * its output held a timezone. Runs the REAL _FILE_CHIP_OPS / _fileChipOp /
 * _extractPathFromTool from chat.js. Parity with the server's side-effect
 * registry: tests/test_file_chip.py.
 */
"use strict";

const fs = require("fs");
const path = require("path");

const src = fs.readFileSync(path.join(
  __dirname, "..", "..", "kazma-ui", "kazma_ui", "static", "js", "chat.js"), "utf8")
  .replace(/\r\n/g, "\n");
const start = src.indexOf("  var _FILE_CHIP_OPS = {");
const end = src.indexOf("  function _renderPlanList(", start);

let fail = 0;
function ok(name, cond, detail) {
  if (cond) { console.log("OK " + name); return; }
  fail += 1;
  console.log("FAIL " + name + "  " + String(detail || ""));
}
ok("chip functions are extractable", start > 0 && end > start);

// eslint-disable-next-line no-new-func
const api = new Function(src.slice(start, end) +
  "\nreturn { op: _fileChipOp, path: _extractPathFromTool };")();

const live = '{"ok": true, "count": 12, "posts": [{"id": 12, "tz": "Asia/Kuwait", ' +
  '"fire_at": "2026-09-03T17:19:26+03:00"}]}';
ok("live: a read with a timezone in its output gets no chip",
  api.path("x_list_scheduled", live) === "", api.path("x_list_scheduled", live));
ok("a shell result with a path gets no chip",
  api.path("shell_exec", "ls /usr/local/bin") === "");
ok("a fetched URL gets no chip",
  api.path("read_url", "https://example.com/a/b") === "");
ok("x_status gets no chip", api.op("x_status") === "");

ok("file_write names the file from its result",
  api.path("file_write", "Wrote 3 lines, 20 bytes to docs/notes.md") === "docs/notes.md",
  api.path("file_write", "Wrote 3 lines, 20 bytes to docs/notes.md"));
ok("file_write names the file from its arguments",
  api.path("file_write", '{"path": "src/app.py", "content": "x = 1"}') === "src/app.py");
ok("file_delete names the file", api.path("file_delete", '{"path": "tmp/x.txt"}') === "tmp/x.txt");
ok("file_delete says deleted", api.op("file_delete") === "deleted");
ok("file_apply_patch says patched", api.op("file_apply_patch") === "patched");
ok("file_write says wrote", api.op("file_write") === "wrote");

process.exit(fail ? 1 : 0);
