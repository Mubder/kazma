/**
 * The chat's slash-command menu describes each command in the page's language.
 * Run: node tests/js/test_chat_slash_i18n.js
 *
 * chat_slash.js held twenty English descriptions; typing "/" in an Arabic
 * chat opened them all in English (live, 2026-09-28). The catalog is read
 * through window.kazmaT, which base.html defines before this script; with no
 * translator the English stays, so the catalog and the menu never disagree
 * on what a command does.
 *
 * Loads the REAL chat_slash.js twice: once with a translator that marks what
 * it was asked for, once without. Negative control: the old shape (a bare
 * English string) never reaches the translator.
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const src = fs.readFileSync(path.join(
  __dirname, "..", "..", "kazma-ui", "kazma_ui", "static", "js", "chat_slash.js"), "utf8")
  .replace(/\r\n/g, "\n");

let fail = 0;
function ok(name, cond, detail) {
  if (cond) { console.log("OK " + name); return; }
  fail += 1;
  console.log("FAIL " + name + "  " + String(detail || ""));
}

function load(code, translator) {
  const window = {};
  if (translator) window.kazmaT = translator;
  vm.runInNewContext(code, { window });
  return window.KAZMA_SLASH_COMMANDS;
}

const asked = [];
const translated = load(src, (key, en) => { asked.push(key); return "AR " + key; });
const english = load(src, null);

ok("twenty commands, in both loads", translated.length >= 20 && english.length === translated.length, translated.length);
ok("every description came from the catalog", translated.every((c) => /^AR chat\.slash\./.test(c.desc)),
  translated.filter((c) => !/^AR chat\.slash\./.test(c.desc)).map((c) => c.cmd).join(","));
ok("every key is distinct", new Set(asked).size === asked.length);
ok("without a translator the English stays", english.every((c) => typeof c.desc === "string" && c.desc && !/^AR /.test(c.desc)));
ok("commands and their composer inserts are unchanged", JSON.stringify(english.map((c) => [c.cmd, c.insert || null]))
  === JSON.stringify(translated.map((c) => [c.cmd, c.insert || null])));
const steer = english.find((c) => c.cmd === "/steer");
ok("/steer still queues a draft", !!steer && steer.insert === "/steer ");

// Negative control: the old catalog shape holds English whatever the translator says.
const oldShape = "(function (root) { root.KAZMA_SLASH_COMMANDS = [ { cmd: '/help', desc: 'List available slash commands' } ]; })(window);";
const oldLoaded = load(oldShape, (key, en) => "AR " + key);
ok("negative control: a bare English description ignores the translator",
  oldLoaded.every((c) => !/^AR /.test(c.desc)));

if (fail) { console.log(fail + " failure(s)"); process.exit(1); }
console.log("all ok");
