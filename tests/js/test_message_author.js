/**
 * A chat message names its author for the other members of a space, and
 * only for them (Spaces S1-08).
 *
 * The server sends `author` (an account) with every user's message and
 * `author_name` only on an install that enforces Spaces. The page shows the
 * name beside another member's message; a message of the person looking --
 * this tab's or another of their tabs' -- keeps "You". Runs the REAL helpers
 * from chat.js.
 */
"use strict";

const fs = require("fs");
const path = require("path");

const src = fs.readFileSync(path.join(
  __dirname, "..", "..", "kazma-ui", "kazma_ui", "static", "js", "chat.js"), "utf8")
  .replace(/\r\n/g, "\n");
const start = src.indexOf("  function _otherAuthorName(");
const end = src.indexOf("  function appendMessage(", start);
const escStart = src.indexOf("  function escapeHtml(");
const escEnd = src.indexOf("  // Tools where a hidden suffix", escStart);

let fail = 0;
function ok(name, cond, detail) {
  if (cond) { console.log("OK " + name); return; }
  fail += 1;
  console.log("FAIL " + name + "  " + String(detail || ""));
}
ok("the author helpers are extractable", start > 0 && end > start && escStart > 0 && escEnd > escStart);

// eslint-disable-next-line no-new-func
const api = new Function(src.slice(escStart, escEnd) + src.slice(start, end) +
  "\nreturn { name: _otherAuthorName, avatar: _memberAvatarHtml, meta: _authorMetaHtml };")();

// Who is named.
ok("another member is named",
  api.name({ author: "acc-layla", author_name: "Layla" }, "acc-omar") === "Layla");
ok("the person looking is not named (another tab of theirs)",
  api.name({ author: "acc-omar", author_name: "Omar" }, "acc-omar") === "");
ok("no name sent (an install without Spaces): nobody is named",
  api.name({ author: "owner" }, "") === "" && api.name({ author: "owner" }, "owner") === "");
ok("a frame with no author names nobody", api.name({}, "acc-omar") === "" && api.name(null, "") === "");
ok("a page that does not know its account still names the author the server named",
  api.name({ author: "acc-layla", author_name: "Layla" }, "") === "Layla");

// How the name is shown: text, never markup; kept as written.
const hostile = '<img src=x onerror="alert(1)">';
const avatar = api.avatar(hostile);
ok("the avatar escapes the name", avatar.indexOf("<img") === -1 && avatar.indexOf("&lt;img") !== -1, avatar);
ok("the avatar is the name's first letter",
  />L<\/div>$/.test(api.avatar("layla")) && api.avatar(" ليلى").indexOf(">ل</div>") !== -1,
  api.avatar("layla"));
ok("the avatar is content, not interface text", api.avatar("Layla").indexOf('translate="no"') !== -1);
const meta = api.meta(hostile);
ok("the meta line escapes the name", meta.indexOf("<img") === -1 && meta.indexOf("&lt;img") !== -1, meta);
ok("the name is isolated from the time beside it (an Arabic name in an English line)",
  api.meta("ليلى").indexOf("<bdi") === 0);
ok("no name, no meta text", api.meta("") === "");

process.exit(fail ? 1 : 0);
