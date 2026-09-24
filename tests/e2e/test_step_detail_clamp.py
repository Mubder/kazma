"""A collapsed CoT detail shows whole lines, never a sliver.

Reported 2026-09-24: expanding Thinking & Activity showed rows like
"List files · Done · Show more ▾" whose detail was a thin strip with the text
cut through the middle. The clamps were ``max-height: 1.5em`` / ``3.6em`` on
a padded ``border-box`` element: measured 0.06 and 1.36 visible lines. A
source-grep test had locked the 1.5em in as "one line".

This renders the REAL ``_detailHtml`` from chat.js with the REAL stylesheets
in Chromium and measures the line boxes themselves: how many are fully
visible, and whether any is cut by the clip edge. Expanded shows everything.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "kazma-ui" / "kazma_ui" / "static"
# A short first line makes a detail gist-led (one-line clamp). Eight short
# lines, so the expanded check fits the expanded height.
GIST = "14 entries in kazma-data/exports (3 folders, 11 files)\n" + "\n".join(
    f'{{"name": "tweets_2026-09-{n:02d}.json", "size": 18211}}' for n in range(1, 8)
)
# No newline in the first 120 characters and over the clamp threshold: the
# three-line clamp (a one-line JSON payload, wrapped by the panel).
WIDE = "{" + ", ".join(f'"key_{n}": "value number {n}"' for n in range(40)) + "}"
THOUGHT = "\n\n".join(
    [
        "First paragraph of the reasoning, long enough to wrap across the panel "
        "so that the clamp has real lines to cut. " * 2,
        "Second paragraph: checking the published history against the queue "
        "for duplicates before saving the refined set. " * 2,
        "Third paragraph, hidden until Show more. " * 6,
    ]
)
# The pre-fix rules: the PADDED box clips, in em. Injected for the negative
# control -- the gist rule is the one that drew the half-visible rows.
OLD_RULE = (
    ".agent-progress-step .step-detail.is-clamped "
    "{ max-height: 3.6em !important; overflow: hidden !important; }\n"
    ".agent-progress-step .step-detail.is-clamped.has-gist "
    "{ max-height: 1.5em !important; }"
)


def _builder() -> str:
    """``_detailHtml`` exactly as shipped, plus the constants it reads."""
    src = (STATIC / "js" / "chat.js").read_text(encoding="utf-8")
    start = src.index("  function _detailHtml(")
    end = src.index("  function _stepRowHtml(", start)
    consts = "".join(
        f"var {name} = {re.search(rf'var {name} = (\d+);', src).group(1)};\n"
        for name in ("TOOL_DETAIL_MAX", "STEP_DETAIL_CLAMP_AT")
    )
    return consts + src[start:end]


_STUBS = r"""
function escapeHtml(s) { return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }
function truncateStr(s, n) { s = String(s); return s.length > n ? s.slice(0, n) + '…' : s; }
function ti(k, d) { return d; }
function _scrubDsml(s) { return s; }
var KS = { markdown: function (t) {
  return String(t).split(/\n\n+/).map(function (p) { return '<p>' + escapeHtml(p) + '</p>'; }).join('');
} };
function addRow(kind, detail) {
  var li = document.createElement('li');
  li.className = 'agent-progress-step step-' + kind + ' state-done';
  li.innerHTML = '<div class="step-body"><div class="step-line"><span class="step-title">t</span></div>' +
    _detailHtml(detail, false, kind) + '</div>';
  document.querySelector('.agent-progress-steps').appendChild(li);
  return document.querySelectorAll('.agent-progress-step').length - 1;
}
function clipBottom(el, stop) {
  var bottom = Infinity;
  for (var n = el; n && n !== stop; n = n.parentElement) {
    var cs = getComputedStyle(n);
    if (cs.overflowY !== 'visible' || cs.overflow !== 'visible') {
      bottom = Math.min(bottom, n.getBoundingClientRect().bottom - parseFloat(cs.borderBottomWidth));
    }
  }
  return bottom;
}
// One rect per visual line of TEXT. Element boxes (a <p>) are not lines, and
// a line can come back as several fragments; keep the widest per line.
function textLines(el) {
  var byTop = {};
  var w = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
  for (var n = w.nextNode(); n; n = w.nextNode()) {
    if (!n.data.trim()) continue;
    var r = document.createRange(); r.selectNodeContents(n);
    Array.from(r.getClientRects()).forEach(function (x) {
      if (x.width < 1 || x.height < 1) return;
      var k = Math.round(x.top);
      if (!byTop[k] || byTop[k].width < x.width) byTop[k] = x;
    });
  }
  return Object.keys(byTop).map(function (k) { return byTop[k]; });
}
// A glyph box can reach a pixel past its line box, so the hidden line after
// a clean clamp shows ~5% of an EMPTY band. A cut line shows a real part of
// itself: that is the half-drawn row the user saw.
function measure(i) {
  var li = document.querySelectorAll('.agent-progress-step')[i];
  var d = li.querySelector('.step-detail');
  var t = d.querySelector('.step-detail-text') || d;
  var bottom = clipBottom(t, li);
  var lines = textLines(t);
  function shown(x) { return Math.max(0, Math.min(x.bottom, bottom) - x.top) / x.height; }
  return {
    full: lines.filter(function (x) { return shown(x) >= 0.85; }).length,
    cut: lines.filter(function (x) { var s = shown(x); return s > 0.15 && s < 0.85; }).length,
    total: lines.length,
  };
}
function expand(i) {
  var det = document.querySelectorAll('.agent-progress-step')[i].querySelector('.step-detail');
  det.classList.toggle('is-clamped', false);   // exactly _wireStepToggles
  det.classList.toggle('is-expanded', true);
}
"""


def _page(pw, extra_css: str = ""):
    css = "\n".join(
        (STATIC / "css" / name).read_text(encoding="utf-8")
        for name in ("kazma.css", "kazma.v5.css")
    )
    browser = pw.chromium.launch()
    page = browser.new_page(viewport={"width": 760, "height": 1200})
    page.set_content(
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        f"<style>{css}\n{extra_css}</style></head><body style='padding:24px;max-width:720px'>"
        "<div class='message message-assistant'><div class='agent-progress is-done'>"
        "<div class='agent-progress-body'><ul class='agent-progress-steps'></ul>"
        "</div></div></div></body></html>"
    )
    page.add_script_tag(content=_STUBS + _builder())
    return browser, page


def _rows(page) -> dict[str, int]:
    return {
        "gist": page.evaluate("d => addRow('tool', d)", GIST),
        "wide": page.evaluate("d => addRow('tool', d)", WIDE),
        "thought": page.evaluate("d => addRow('thought', d)", THOUGHT),
    }


def test_collapsed_details_show_whole_lines_and_expand_fully():
    with sync_playwright() as pw:
        browser, page = _page(pw)
        try:
            rows = _rows(page)
            got = {name: page.evaluate("i => measure(i)", i) for name, i in rows.items()}
            assert got["gist"]["cut"] == 0 and got["gist"]["full"] == 1, got["gist"]
            assert got["wide"]["cut"] == 0 and got["wide"]["full"] == 3, got["wide"]
            assert got["thought"]["cut"] == 0 and got["thought"]["full"] == 3, got["thought"]

            page.evaluate("i => expand(i)", rows["gist"])
            opened = page.evaluate("i => measure(i)", rows["gist"])
            assert opened["cut"] == 0 and opened["full"] == opened["total"] == 8, opened
        finally:
            browser.close()


def test_the_measurement_catches_the_old_clamp():
    """Negative control (AGENTS.md §28): the pre-fix rule must read as cut."""
    with sync_playwright() as pw:
        browser, page = _page(pw, extra_css=OLD_RULE)
        try:
            rows = _rows(page)
            gist = page.evaluate("i => measure(i)", rows["gist"])
            assert gist["cut"] >= 1 and gist["full"] == 0, gist
        finally:
            browser.close()
