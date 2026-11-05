/**
 * A turn's token count is said in its plural form, with the number shown
 * grouped ("80,676 tokens") while the form follows the number itself.
 *
 * Live 2026-11-05: an instant reply's meta line read "1 tokens", glued from
 * one word ('n + " " + tokens'). Runs the REAL _pluralCategory, tiCount and
 * _tokensText extracted from chat.js, with the forms the template injects
 * (CHAT_I18N.plural.count_tokens, catalog common.ui.tokens). Negative
 * control: the old glued text.
 */
"use strict";

const fs = require("fs");
const path = require("path");

const src = fs.readFileSync(path.join(
  __dirname, "..", "..", "kazma-ui", "kazma_ui", "static", "js", "chat.js"), "utf8")
  .replace(/\r\n/g, "\n");

let fail = 0;
function ok(name, cond, detail) {
  if (cond) { console.log("OK " + name); return; }
  fail += 1;
  console.log("FAIL " + name + "  " + String(detail || ""));
}

function slice(marker) {
  const at = src.indexOf(marker);
  const end = src.indexOf("\n  }\n", at) + 4;
  return at > 0 && end > at ? src.slice(at, end) : "";
}
const helpers = [
  slice("  function _pluralCategory("),
  slice("  function tiCount("),
  slice("  function _tokensText("),
];
ok("the helpers are extractable", helpers.every(Boolean));

// The catalog's forms (kazma_ui/i18n/catalog, common.ui.tokens).
const FORMS = {
  en: { zero: "{n} tokens", one: "{n} token", two: "{n} tokens", few: "{n} tokens", many: "{n} tokens", other: "{n} tokens" },
  ar: { zero: "لا رموز", one: "رمز واحد", two: "رمزان", few: "{n} رموز", many: "{n} رمزًا", other: "{n} رمز" },
};

function load(lang) {
  const document = { documentElement: { getAttribute: () => lang } };
  const window = { CHAT_I18N: { plural: { count_tokens: FORMS[lang] } } };
  const KS = { formatTokens: (n) => (n ? Number(n).toLocaleString("en-US") : "0") };
  // eslint-disable-next-line no-new-func
  return new Function("document", "window", "KS",
    helpers.join("\n") + "\nreturn _tokensText;")(document, window, KS);
}

const en = load("en");
ok("en 1", en(1) === "1 token", en(1));
ok("en 2", en(2) === "2 tokens", en(2));
ok("en grouped", en(80676) === "80,676 tokens", en(80676));
ok("en zero", en(0) === "0 tokens", en(0));

const ar = load("ar");
ok("ar 0", ar(0) === "لا رموز", ar(0));
ok("ar 1", ar(1) === "رمز واحد", ar(1));
ok("ar 2", ar(2) === "رمزان", ar(2));
ok("ar 5", ar(5) === "5 رموز", ar(5));
ok("ar 11", ar(11) === "11 رمزًا", ar(11));
// The form follows the number (1,200 -> "other"), the text shows it grouped.
ok("ar grouped", ar(1200) === "1,200 رمز", ar(1200));

// Negative control: the meta line as it was.
{
  const tokens = 1;
  const old = KSOld(tokens) + " " + "tokens";
  ok("the old text is caught", old === "1 tokens" && old !== en(1), old);
}
function KSOld(n) { return String(n); }

if (fail) {
  console.log(fail + " failed");
  process.exit(1);
}
console.log("all passed");
