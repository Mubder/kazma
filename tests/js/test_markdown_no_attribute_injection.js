/**
 * The markdown renderer writes only the tags it means to (security audit
 * 2026-11-10, SEC-03).
 *
 * A reply is steerable by any untrusted text the agent read. The link pass
 * accepted an address with a space in it, and the auto-link pass then ran
 * over the anchor already written: a second URL inside the first href got an
 * anchor of its own, whose quotes ended the outer attribute, and the rest
 * became attributes of the outer tag:
 *
 *   [click](https://a.com/ https://b/onmouseover=location=/javascript:alert%281%29/.source//)
 *
 * rendered an `onmouseover` handler. The page's CSP has no script-src, so
 * that was script in the operator's signed-in page.
 *
 * The check: after every well-formed tag is taken out of the output, no
 * angle bracket is left, and no tag carries an attribute the renderer never
 * writes.
 *
 * Run: node tests/js/test_markdown_no_attribute_injection.js
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

const ALLOWED_ATTRS = new Set([
  "href", "src", "alt", "title", "class", "dir", "target", "rel", "loading",
  "start", "type", "disabled", "checked", "translate", "lang", "colspan",
  "rowspan", "align", "style", "data-lang", "data-code", "id", "role",
  "aria-label", "aria-hidden", "tabindex", "onclick-none",
]);
const TAG = /<\/?[a-zA-Z][a-zA-Z0-9]*((?:\s+[a-zA-Z_:][-a-zA-Z0-9_:.]*(?:="[^"<>]*")?)*)\s*\/?>/g;
const ATTR = /\s+([a-zA-Z_:][-a-zA-Z0-9_:.]*)(?:="[^"<>]*")?/g;

/** Problems with one rendered string: [] when every tag is one the renderer wrote whole. */
function problems(html) {
  const found = [];
  const rest = html.replace(TAG, function (tag, attrs) {
    let m;
    ATTR.lastIndex = 0;
    while ((m = ATTR.exec(attrs || "")) !== null) {
      const name = m[1].toLowerCase();
      if (/^on/.test(name) && name !== "onclick-none") found.push("handler attribute " + name);
      else if (!ALLOWED_ATTRS.has(name) && !/^data-/.test(name)) found.push("unexpected attribute " + name);
    }
    return "";
  });
  if (/[<>]/.test(rest)) found.push("text left outside a whole tag: " + rest.slice(0, 120));
  if (/href="\s*(javascript|data|vbscript):/i.test(html)) found.push("script address");
  return found;
}

/** The two passes as they were, to show the check sees the defect. */
function oldInline(s) {
  function esc(t) {
    return t.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }
  let html = esc(s);
  html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, function (_, text, url) {
    const decoded = url.replace(/&amp;/g, "&");
    if (/^(https?:|mailto:)/i.test(decoded)) {
      return '<a href="' + esc(decoded) + '" target="_blank" rel="noopener noreferrer" dir="ltr">' + text + "</a>";
    }
    return text;
  });
  html = html.replace(
    /(^|[\s>(])((?:https?:\/\/|www\.)[^\s<]+[^\s<.,;:!?'")\]])/g,
    function (_, pre, url) {
      return pre + '<a href="' + esc(url) + '" target="_blank" rel="noopener noreferrer" dir="ltr">' + url + "</a>";
    },
  );
  return "<p>" + html + "</p>";
}

const HOSTILE = [
  "[click](https://a.com/ https://b/onmouseover=location=/javascript:alert%281%29/.source//)",
  "![x](https://a.com/ https://b/onerror=alert%281%29//)",
  "![x](/static/a.png https://b/onerror=alert%281%29//)",
  "[a](https://a.com/ www.b.com/onfocus=alert%281%29//)",
  "[t](https://a.com/\" onmouseover=\"alert(1))",
  "[t](https://a.com/' onmouseover='alert(1))",
  "[t](javascript:alert(1))",
  "[t](data:text/html,<script>alert(1)</script>)",
  "[https://c.example/x](https://a.com/y)",
  "**[b](https://a.com/ https://b/onmouseover=alert%281%29//)**",
  "| a |\n|---|\n| [c](https://a.com/ https://b/onmouseover=alert%281%29//) |",
  "- [c](https://a.com/ https://b/onmouseover=alert%281%29//)",
  "# [c](https://a.com/ https://b/onmouseover=alert%281%29//)",
  "> [c](https://a.com/ https://b/onmouseover=alert%281%29//)",
  "https://a.com/x\"onmouseover=alert(1)",
  "<img src=x onerror=alert(1)>",
];

let fail = 0;
function assert(name, cond, detail) {
  if (!cond) {
    console.error("FAIL", name, String(detail || "").slice(0, 400));
    fail += 1;
  } else {
    console.log("ok  ", name);
  }
}

// 1. The control: the old passes write the handler, and the check sees it.
const oldOut = oldInline(HOSTILE[0]);
assert("control: the old passes break the attribute", problems(oldOut).length > 0, oldOut);

// 2. Nothing hostile leaves a tag the renderer did not write whole.
HOSTILE.forEach(function (text, i) {
  const out = mdRender(text);
  const found = problems(out);
  assert("hostile " + i + " renders only whole tags", found.length === 0, found.join("; ") + " :: " + out);
  assert("hostile " + i + " has no handler text in a tag",
    !/<[^>]*\son[a-z]+\s*=/i.test(out.replace(/"[^"]*"/g, '""')), out);
});

// 3. What a reply normally holds still renders.
const link = mdRender("See [the docs](https://example.com/a?b=1&c=2) now.");
assert("a link renders", /<a href="https:\/\/example\.com\/a\?b=1&amp;c=2" target="_blank" rel="noopener noreferrer" dir="ltr">the docs<\/a>/.test(link), link);
const auto = mdRender("Open https://example.com/path today.");
assert("a bare URL is linked once", (auto.match(/<a /g) || []).length === 1 && /href="https:\/\/example\.com\/path"/.test(auto), auto);
const www = mdRender("Open www.example.com today.");
assert("a www address is linked", /href="https:\/\/www\.example\.com"/.test(www), www);
const coded = mdRender("[`code` here](https://example.com/x)");
assert("a code span inside a link", /<a href="https:\/\/example\.com\/x"[^>]*><code class="inline-code" dir="ltr">code<\/code> here<\/a>/.test(coded), coded);
const bold = mdRender("**[bold](https://example.com/x)**");
assert("a link inside bold", /<strong><a href="https:\/\/example\.com\/x"[^>]*>bold<\/a><\/strong>/.test(bold), bold);
const urlText = mdRender("[https://shown.example/x](https://real.example/y)");
assert("a URL as link text is not linked twice", (urlText.match(/<a /g) || []).length === 1 && /href="https:\/\/real\.example\/y"/.test(urlText), urlText);
const img = mdRender("![chart](/api/chat/files/abc123)");
assert("this server's image loads", /<img src="\/api\/chat\/files\/abc123" alt="chart" loading="lazy" class="md-img">/.test(img), img);
const off = mdRender("![chart](https://other.example/c.png)");
assert("another host's image is a link", /<a href="https:\/\/other\.example\/c\.png"[^>]*class="md-img-link"/.test(off) && !/<img/.test(off), off);
const local = mdRender("[settings](/settings?tab=alerts)");
assert("a local link renders", /<a href="\/settings\?tab=alerts">settings<\/a>/.test(local), local);
const mail = mdRender("[mail](mailto:someone@example.com)");
assert("a mailto link renders", /<a href="mailto:someone@example\.com"/.test(mail), mail);
[link, auto, www, coded, bold, urlText, img, off, local, mail].forEach(function (out, i) {
  assert("normal " + i + " is whole tags", problems(out).length === 0, problems(out).join("; ") + " :: " + out);
});
assert("no stash token is left behind", ![link, auto, coded, bold, urlText, img, off].some(function (o) { return /\x00|KZCODE/.test(o); }));

if (fail) {
  console.error(fail + " failed");
  process.exit(1);
}
console.log("all passed");
