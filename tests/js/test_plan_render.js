/**
 * Behavior tests for the chat.js plan-fence presentation transform.
 * Extracts splitPlanAndProse / stripPlanFenceForDisplay /
 * transformRenderedForPlan from chat.js and exercises them under Node —
 * no browser needed. Mirrors tests/js/test_markdown_render.js style.
 *
 * Run: node tests/js/test_plan_render.js
 */
"use strict";

const fs = require("fs");
const path = require("path");

const srcPath = path.join(
  __dirname, "..", "..",
  "kazma-ui", "kazma_ui", "static", "js", "chat.js",
);
const src = fs.readFileSync(srcPath, "utf8");
const start = src.indexOf("function splitPlanAndProse(");
const end = src.indexOf("function _normalizeStatusTitle(", start);
if (start < 0 || end < 0) {
  console.error("Could not locate plan functions in chat.js");
  process.exit(1);
}

// Stubs for the i18n / workbench helpers referenced inside the slice.
const ti = (k, fb) => fb ?? k;
const tiFmt = (k, fb) => fb ?? k;
const setPlan = () => {};
const logProgress = () => {};
const _planItems = [];
const _planParsedFromText = { v: false };

const chunk = src.slice(start, end).trim();
// eslint-disable-next-line no-eval
const api = eval(
  "(function(ti, tiFmt, setPlan, logProgress){"
  + chunk.replace(/\b_planItems\b/g, "_ctx._planItems")
         .replace(/\b_planParsedFromText\b/g, "_ctx._flag")
  + "; return { splitPlanAndProse: typeof splitPlanAndProse === 'function' ? splitPlanAndProse : null,"
  + " stripPlanFenceForDisplay: typeof stripPlanFenceForDisplay === 'function' ? stripPlanFenceForDisplay : null,"
  + " transformRenderedForPlan: typeof transformRenderedForPlan === 'function' ? transformRenderedForPlan : null,"
  + " tryIngestPlanFromText: typeof tryIngestPlanFromText === 'function' ? tryIngestPlanFromText : null }; })"
  + "(ti, tiFmt, setPlan, logProgress)",
);

let fail = 0;
function assert(name, cond, detail) {
  if (!cond) {
    console.error("FAIL", name, String(detail ?? "").slice(0, 300));
    fail += 1;
  } else {
    console.log("OK", name);
  }
}

const T = api.transformRenderedForPlan;
assert("transform extracted", typeof T === "function");
assert("split extracted", typeof api.splitPlanAndProse === "function");
assert("strip extracted", typeof api.stripPlanFenceForDisplay === "function");

// ── shape helpers mimicking KazmaStream mdRender output ────────────────────
function mdPre(langLabel, code) {
  const escaped = code
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const lang = langLabel
    ? '<span class="code-lang">' + langLabel + "</span>" : "";
  return "<pre class=\"code-block\">" + lang + "<code>" + escaped + "</code>"
    + '<button class="copy-btn" onclick="KazmaStream.copyCode(this)">⌨</button></pre>';
}

// ── 1. explicit ```plan rendered block wraps into collapsed details ────────
{
  const html = "<p>Doing things now.</p>"
    + mdPre("plan", "- Step one\n- Step two\n- Step three\n")
    + "<p>Done.</p>";
  const out = T(html);
  assert("wrap occurs", out.includes('<details class="kazma-plan">'), out);
  assert("summary Plan", out.includes("<summary>Plan</summary>"), out);
  assert("collapsed by default", !out.includes('<details class="kazma-plan" open'), out);
  assert("body preserved verbatim", out.includes("- Step one") && out.includes("- Step three"), out);
  assert("copy button preserved", out.includes("copy-btn"), out);
  // prose BEFORE the details is untouched
  assert("leading prose intact", out.startsWith('<p>Doing things now.</p>'), out);
}

// ── 2. unspec-typed checklist-majority block (artifact shape) also wraps ───
{
  const html = "<p>Let me check.</p>"
    + mdPre("", ":Core stats\n- item a\n- item b\n");
  const out = T(html);
  assert("heuristic wrap", out.includes('class="kazma-plan"'), out);
}

// ── 3. duplicated plan collapses to ONE details ─────────────────────────────
{
  const planBody = "- Step one\n- Step two\n";
  const html = mdPre("plan", planBody) + "<p>mid</p>" + mdPre("plan", planBody);
  const out = T(html);
  const n = (out.match(/<details class="kazma-plan"/g) || []).length;
  assert("duplicate collapses to one details", n === 1, "count=" + n + "\n" + out);
}

// ── 4. bare-text glue after </details> gets a <p> boundary ─────────────────
{
  const html = mdPre("plan", "- a\n- b\n") + ":Core stats and more";
  const out = T(html);
  assert("boundary inserted", /<\/details>\s*<p>:Core stats/.test(out), out);
  assert("glued text node intact", out.includes(":Core stats and more"), out);
}
// already-separated case must NOT gain an extra wrapper (idempotent-ish)
{
  const html = mdPre("plan", "- a\n- b\n") + "<p>:Core stats</p>";
  const out = T(html);
  assert("no double boundary for element sibling",
    !out.includes("</details>\n<p><p>"), out);
  assert("idempotent element-sibling case", T(out) === out, out);
}

// ── 5. idempotency: t(t(x)) === t(x) across all shapes ─────────────────────
[
  mdPre("plan", "- Step one\n- Step two\n") + "<p>prose</p>",
  mdPre("", "- a\n- b\n") + ":tail",
  "<p>plain answer only</p>",
  "",
].forEach((x, i) => {
  const once = T(x);
  const twice = T(once);
  assert("idempotency case " + i, once === twice, JSON.stringify([once, twice]));
});

// ── 6. non-plan content never touched ──────────────────────────────────────
{
  const codeOnly = "<p>code:</p>" + mdPre("js", "var x = '- not';\nvar y = '- a plan';\n");
  assert("typed lang untouched", T(codeOnly) === codeOnly, T(codeOnly));
  const plain = "<p>hello world</p>";
  assert("prose untouched", T(plain) === plain, T(plain));
  const shortFence = "<p>x</p>" + mdPre("", "- single line list\n");
  assert("single-list-line fence untouched", T(shortFence) === shortFence, T(shortFence));
}

// ── 7. pre-existing details are protected & external twins dropped ─────────
{
  const once = T(mdPre("plan", "- s1\n- s2\n"));
  const repainted = once + mdPre("plan", "- s1\n- s2\n"); // streaming repaint added a twin
  const twice = T(repainted);
  const n = (twice.match(/<details class="kazma-plan"/g) || []).length;
  assert("repaint twin dropped, original untouched", n === 1, "count=" + n + "\n" + twice);
  assert("protected body byte-identical", twice.indexOf(once) === 0, twice.slice(0, 120));
}

// ── 8. text-level splitter tolerates "``` plan" (space variant) ────────────
{
  const r = api.splitPlanAndProse("Let me check.\n``` plan\n- one\n- two\n```\nAfter word.");
  assert("spacey fence recognized", !!r.plan && r.plan.includes("one"), JSON.stringify(r));
  assert("preamble+postamble split clean",
    r.prose.includes("Let me check.") && r.prose.includes("After word.")
    && !r.prose.includes("- one"), JSON.stringify(r));
}

process.exit(fail ? 1 : 0);
