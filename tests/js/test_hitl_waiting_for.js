/**
 * Spaces S2-05: an approval card goes to the people it asks. The server
 * names them on the card (`answered_by`, `answered_by_names`) only on an
 * install that enforces Spaces; anyone else watching the chat sees the card
 * waiting for them, with no buttons. Runs the REAL helpers from chat.js.
 */
"use strict";

const fs = require("fs");
const path = require("path");

const src = fs.readFileSync(path.join(
  __dirname, "..", "..", "kazma-ui", "kazma_ui", "static", "js", "chat.js"), "utf8")
  .replace(/\r\n/g, "\n");
const start = src.indexOf("  function _hitlWaitingFor(");
const end = src.indexOf("  function _markHitlWaiting(", start);
const tiStart = src.indexOf("  function ti(key, fallback) {");
const tiEnd = src.indexOf("  /** A clarify card's question", tiStart);
const escStart = src.indexOf("  function escapeHtml(");
const escEnd = src.indexOf("  // Tools where a hidden suffix", escStart);

let fail = 0;
function ok(name, cond, detail) {
  if (cond) { console.log("OK " + name); return; }
  fail += 1;
  console.log("FAIL " + name + "  " + String(detail || ""));
}
ok("the helpers are extractable", start > 0 && end > start && tiStart > 0 && tiEnd > tiStart
   && escStart > 0 && escEnd > escStart);

function load(catalog) {
  global.window = { CHAT_I18N: catalog || {} };
  // eslint-disable-next-line no-new-func
  return new Function(src.slice(tiStart, tiEnd) + src.slice(escStart, escEnd) + src.slice(start, end) +
    "\nreturn { waitingFor: _hitlWaitingFor, html: _hitlWaitingHtml };")();
}

const api = load();
const card = { answered_by: ["acc-reem"], answered_by_names: ["Reem"] };

// Who sees buttons.
ok("the person it asks answers it (no waiting line)", api.waitingFor(card, "acc-reem") === "");
ok("another member sees it waiting for the asker", api.waitingFor(card, "acc-omar") === "Reem");
ok("several people it waits for are all named",
  api.waitingFor({ answered_by: ["a", "b"], answered_by_names: ["Sara", "Omar"] }, "c") === "Sara, Omar");
ok("an install without Spaces names nobody: every card is answerable as before",
  api.waitingFor({ tool: "file_write" }, "acc-omar") === "" && api.waitingFor({}, "") === "");
ok("a page that does not know its account leaves the decision to the server",
  api.waitingFor(card, "") === "");
ok("a card that names who but not their names still waits",
  api.waitingFor({ answered_by: ["acc-reem"] }, "acc-omar") !== "");

// How the line reads: text, never markup; a name is content.
const hostile = '<img src=x onerror="alert(1)">';
const line = api.html(hostile);
ok("the line escapes the name", line.indexOf("<img") === -1 && line.indexOf("&lt;img") !== -1, line);
ok("the name is content, isolated", line.indexOf('<bdi translate="no">') !== -1, line);
ok("English by default", api.html("Reem").indexOf("Waiting for") !== -1);
const ar = load({ hitl_waiting_for: "بانتظار ردّ {names}" });
ok("Arabic from the page's catalog", ar.html("ريم").indexOf("بانتظار ردّ") !== -1
   && ar.html("ريم").indexOf("ريم") !== -1);

process.exit(fail ? 1 : 0);
