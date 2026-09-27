/**
 * A dialog opened right after another one closes keeps its text and buttons.
 *
 * The modal store's close() resets the dialog's content 200 ms later, after
 * the leave transition. It did so regardless, so a dialog shown within those
 * 200 ms -- an error alert right after a confirm, a second delete -- was
 * wiped: open, with no title, no buttons and no checkbox. On CI the
 * delete-forget browser test (tests/e2e/test_chat_delete_forget.py) opened
 * its second delete dialog inside the window and its Delete button was
 * detached mid-click (2026-09-27). Runs the REAL store extracted from
 * modules/stores.js; the negative control runs it with the guard removed.
 *
 * Run: node tests/js/test_modal_store.js
 */
"use strict";

const fs = require("fs");
const path = require("path");

// LF-normalised: a Windows checkout has CRLF.
const src = fs.readFileSync(path.join(
  __dirname, "..", "..", "kazma-ui", "kazma_ui", "static", "js", "modules", "stores.js"),
  "utf8").replace(/\r\n/g, "\n");
const open = "Alpine.store('modal', {";
const start = src.indexOf(open);
const end = src.indexOf("\n        });", start);
const literal = src.slice(start + open.length - 1, end + "\n        }".length);
const GUARD = "if (this.open) return;";

let fail = 0;
function ok(name, cond, detail) {
  if (cond) { console.log("OK " + name); return; }
  fail += 1;
  console.log("FAIL " + name + "  " + String(detail || ""));
}
ok("the modal store is extractable", start >= 0 && end > start && literal.includes("close()"));
ok("the reset is guarded", literal.includes(GUARD));

function makeStore(code) {
  const timers = [];
  const documentStub = {
    activeElement: null,
    documentElement: { dataset: {} },
    querySelector: () => null,
  };
  // eslint-disable-next-line no-new-func
  const store = new Function("document", "setTimeout", "return (" + code + ");")(
    documentStub, (fn, ms) => { timers.push({ fn, ms }); return timers.length; },
  );
  return { store, runTimers() { while (timers.length) timers.shift().fn(); } };
}

function dialog(title, label, checkbox) {
  return { title, actions: [{ label, close: true }], checkbox };
}

// A dialog opened inside the reset window keeps its content.
{
  const { store, runTimers } = makeStore(literal);
  store.show(dialog("Delete chat A?", "Delete", "Also forget"));
  store.close();
  store.show(dialog("Delete chat B?", "Delete B", "Also forget B"));
  runTimers();
  ok("the second dialog is still open", store.open === true);
  ok("its title survives", store.title === "Delete chat B?", store.title);
  ok("its buttons survive", store.actions.length === 1 && store.actions[0].label === "Delete B",
    JSON.stringify(store.actions));
  ok("its checkbox survives", store.checkbox === "Also forget B", store.checkbox);
}

// A dialog that stays closed is still reset.
{
  const { store, runTimers } = makeStore(literal);
  store.show(dialog("Delete chat A?", "Delete", "Also forget"));
  store.close();
  runTimers();
  ok("a closed dialog is cleared", store.title === "" && store.actions.length === 0 && store.checkbox === "");
}

// Negative control: without the guard the second dialog is wiped.
{
  const { store, runTimers } = makeStore(literal.replace(GUARD, ""));
  store.show(dialog("Delete chat A?", "Delete", "Also forget"));
  store.close();
  store.show(dialog("Delete chat B?", "Delete B", "Also forget B"));
  runTimers();
  ok("negative control: the unguarded reset wipes the open dialog",
    store.open === true && store.actions.length === 0 && store.title === "");
}

if (fail) {
  console.log(`\n${fail} failure(s)`);
  process.exit(1);
}
console.log("\nall passed");
