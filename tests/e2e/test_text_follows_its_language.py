"""Where each line actually sits: every paragraph by its own language, in either UI.

The owner's rule (2026-10-02): English text is laid out and aligned
left-to-right and Arabic right-to-left, whatever the UI's language. Live in
the Arabic UI a quoted English refusal ("Safety: Kazma control-plane store
...") was right-aligned, and so was much other English. Measuring real turns
then found the opposite case in both UIs: in a mostly English reply an Arabic
paragraph came out left-to-right, because the bidi helper isolated its Arabic
words against the MESSAGE's direction and its ``dir="auto"`` was left with
the one English word in it.

Two real turns go through the chat page (the scripted model, the real route,
renderer, bidi pass and tool rows), in the Arabic UI and in the English UI on
one server, measured as they arrive and again after a reload: each reply
paragraph, list item and quote, and each tool step's output (an English
refusal, an Arabic file). A paragraph's lines must start at its own
language's edge, a short last line showing which edge that is -- and the two
UIs must lay each paragraph out the same, line for line.

Negative controls: the Arabic UI's old rules put back (the English quote's
first line, a content block and the tool's English refusal move right), and
the per-paragraph direction turned off (the Arabic paragraph moves left).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("playwright")
pytest.importorskip("uvicorn")

from tests.e2e._unified_turn_harness import (  # noqa: E402
    Harness,
    Script,
    Step,
    unified_turn_server,
)

pytestmark = [pytest.mark.e2e, pytest.mark.slow]

#: One word wider than the reply column, so the quote's first line holds only
#: "Safety:" -- a line that follows ``text-align``, unlike a paragraph's last.
TOKEN = (
    "kazma_data_stores_cron_db_scheduled_tasks_and_reminders_control_plane_store_"
    "not_readable_by_file_tools_read_it_with_list_scheduled_instead_of_opening_the_file"
)

#: A mostly English reply (by the bidi helper's own measure, checked below)
#: with an Arabic paragraph and an Arabic list item. The long paragraphs wrap:
#: their inner lines are where an alignment chosen by the UI's language shows.
R1 = "\n\n".join([
    "Kazma checked the cron store for you. The SQL tools refused to open it, because "
    "cron.db is one of Kazma's own stores; the refusal below says which tool reads it "
    "instead, and this reply quotes it exactly as it came back from the tool, so you can "
    "see the same words the tool returned to the agent before it answered.\nDone.",
    f"> Safety: {TOKEN} — read it with list_scheduled.\n> Done.",
    "ملاحظة: يمكنك قراءة المهام المجدولة عبر أداة list_scheduled بدلاً من فتح الملف "
    "مباشرة، فهذه الأداة تقرأ المخزن نفسه دون أن تفتحه.\nتم.",
    "- First item in English\n- عنصر عربي ثاني",
])
#: A mostly Arabic reply quoting the English refusal the owner reported.
R2 = "\n\n".join([
    "تحققت كاظمة من قاعدة البيانات، ورفضت أدوات SQL فتح الملف لأنه أحد مخازن "
    "كاظمة الخاصة. هذا نص الرفض كما ورد:\nتم.",
    "> Safety: Kazma control-plane store — not readable by file tools. cron.db is one of "
    "Kazma's own stores (scheduled tasks and reminders); file tools may not open it.\n"
    "> Read it with list_scheduled.",
    "The tool list_scheduled reads the same store without opening the file.\nDone.",
    "PDF الملف جاهز للتنزيل الآن من صفحة المستندات.\nتم.",
    "- عنصر عربي\n- English item",
])

#: Each measured block by how it begins, longest first (a prefix of another
#: block's text must not claim it).
EXPECT = sorted({
    "Kazma checked": "ltr",
    "Safety:": "ltr",
    "ملاحظة: يمكنك": "rtl",
    "First item": "ltr",
    "عنصر عربي ثاني": "rtl",
    "تحققت كاظمة": "rtl",
    "The tool": "ltr",
    "PDF الملف": "rtl",
    "عنصر عربي": "rtl",
    "English item": "ltr",
}.items(), key=lambda kv: -len(kv[0]))

NOTE_AR = "ملاحظة: المهام المجدولة تقرأ بأداة list_scheduled ولا يفتح الملف مباشرة."


class _Turns(Script):
    """Turn one runs two tools (an English refusal, an Arabic file) and
    answers R1; turn two answers R2 at once."""

    def respond(self, messages):
        last = ""
        for m in reversed(messages or []):
            if isinstance(m, dict) and str(m.get("role") or "").lower() == "user":
                content = m.get("content")
                last = content if isinstance(content, str) else json.dumps(content)
                break
        if "second" in last.lower():
            from kazma_core.llm_provider import LLMResponse

            return LLMResponse(content=R2, finish_reason="stop", model="harness")
        return super().respond(messages)


#: Every reply block's and every tool output's lines: (gap at the left, gap at
#: the right) of each line, against the block's content box.
_MEASURE_JS = r"""() => {
  const lines = (el) => {
    const r = document.createRange(); r.selectNodeContents(el);
    const cs = getComputedStyle(el), box = el.getBoundingClientRect();
    const left = box.left + parseFloat(cs.paddingLeft) + parseFloat(cs.borderLeftWidth);
    const right = box.right - parseFloat(cs.paddingRight) - parseFloat(cs.borderRightWidth);
    const out = [];
    for (const x of r.getClientRects()) {
      if (x.width < 1) continue;
      const line = out.find((ln) => Math.abs(ln.top - x.top) < 4);
      if (line) { line.l = Math.min(line.l, x.left); line.r = Math.max(line.r, x.right); }
      else out.push({top: x.top, l: x.left, r: x.right});
    }
    out.sort((a, b) => a.top - b.top);
    return out.map((ln) => [Math.round(ln.l - left), Math.round(right - ln.r)]);
  };
  const shape = (el) => ({
    text: el.textContent.trim(), dir: getComputedStyle(el).direction,
    width: Math.round(el.getBoundingClientRect().width), lines: lines(el),
  });
  const blocks = [...document.querySelectorAll('.message-assistant .message-text')]
    .flatMap((mt) => [...mt.querySelectorAll('p, li')]).map(shape);
  const tools = [...document.querySelectorAll('.agent-progress-step.step-tool .step-detail-text')]
    .map(shape);
  const content = [...document.querySelectorAll('[data-probe="content"]')].map(shape);
  const decided = [...document.querySelectorAll('[data-probe="decided"]')].map(shape);
  return {page: document.documentElement.dir, blocks, tools, content, decided};
}"""

#: Two blocks of content (translate="no", the i18n rules' mark) in the page's
#: own flow: one the global rule gives its text's direction, and one whose
#: direction a script decided (the renderer, as in the IDE chat's content
#: box) -- the rule must keep that decision: "PDF ..." is an Arabic sentence,
#: and its first letter is not.
_ADD_CONTENT_JS = r"""() => {
  const host = document.querySelector('.chat-messages') || document.body;
  const d = document.createElement('div');
  d.setAttribute('translate', 'no');
  d.setAttribute('data-probe', 'content');
  d.style.width = '100%';
  d.innerHTML = 'Safety: the SQL tools may not open it, and Kazma said why.<br>Done.';
  host.appendChild(d);
  const decided = document.createElement('div');
  decided.setAttribute('translate', 'no');
  decided.style.width = '100%';
  decided.innerHTML = '<p data-probe="decided" dir="rtl">PDF الملف جاهز للتنزيل الآن.<br>تم.</p>';
  host.appendChild(decided);
}"""

#: Opens each finished turn's activity panel, as a reader does (the fold is
#: the reader's: a turn closes it when it finishes).
_OPEN_ACTIVITY_JS = r"""() => {
  document.querySelectorAll('.agent-progress.is-collapsed .agent-progress-header')
    .forEach((h) => h.click());
}"""

#: The Arabic UI's rules before 2026-10-02: replies right-aligned, no content rule.
_OLD_CSS = (
    '[dir="rtl"] .message-assistant .message-text { text-align: right; }'
    ' [translate="no"], [translate="no"] * { unicode-bidi: normal !important; }'
)
_ADD_OLD_CSS_JS = (
    "() => { const s = document.createElement('style'); s.textContent = "
    + json.dumps(_OLD_CSS) + "; document.head.appendChild(s); }"
)
#: The tool rows before 2026-10-02: no direction of their own.
_OLD_TOOL_ROWS_JS = (
    "() => document.querySelectorAll('.step-detail-text').forEach((e) => e.removeAttribute('dir'))"
)


@pytest.fixture
def harness() -> Iterator[Harness]:
    with unified_turn_server(_Turns(steps=[], final=R1)) as h:
        Path(h.data_dir, "notes-ar.md").write_text(NOTE_AR, encoding="utf-8")
        h.script.steps[:] = [
            # cron.db sits in the data dir: the SQL tools refuse it in English.
            Step(tool="sqlite_query", args={"query": "SELECT 1", "db_path": "cron.db"}),
            Step(tool="file_read", args={"path": "notes-ar.md"}),
        ]
        yield h


def _run(
    harness: Harness,
    lang: str,
    *,
    before_send: str = "",
    before_measure: str = "",
    reload: bool = True,
) -> list[dict]:
    """Both turns through the page, in a chat of their own; the measurements
    as they arrived, then after a reload."""
    from playwright.sync_api import sync_playwright

    host = harness.base.split("//", 1)[1].split(":", 1)[0]
    shots = []

    def measure(pg, *, reloaded: bool) -> None:
        if before_send and reloaded:  # a reload drops what was put on the page
            pg.evaluate(before_send)
        pg.evaluate(_ADD_CONTENT_JS)
        pg.evaluate(_OPEN_ACTIVITY_JS)
        pg.wait_for_timeout(500)
        if before_measure:
            pg.evaluate(before_measure)
        shots.append(pg.evaluate(_MEASURE_JS))

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        context.add_cookies([{"name": "kazma-lang", "value": lang, "domain": host, "path": "/"}])
        try:
            pg = context.new_page()
            pg.goto(f"{harness.base}/chat", wait_until="domcontentloaded", timeout=30000)
            pg.locator("#chat-input").wait_for(state="visible", timeout=20000)
            pg.wait_for_function("() => !!window.KazmaChat && !!window.KazmaStream && !!window.KazmaBidi")
            if before_send:
                pg.evaluate(before_send)
            for n, ask in enumerate(["first question", "second question"]):
                pg.fill("#chat-input", ask)
                pg.evaluate("() => window.KazmaChat.sendMessage()")
                pg.wait_for_function(
                    "(n) => { const b = document.querySelectorAll('.message-assistant');"
                    " return b.length > n && !!b[n].querySelector('.turn-header.is-completed'); }",
                    arg=n, timeout=90000,
                )
            pg.wait_for_timeout(1500)  # the post-turn resync and its render pass
            measure(pg, reloaded=False)
            if reload:
                pg.reload(wait_until="domcontentloaded")
                pg.wait_for_function(
                    "() => document.querySelectorAll('.message-assistant .message-text p,"
                    " .message-assistant .message-text li').length >= 11",
                    timeout=30000,
                )
                pg.wait_for_timeout(1500)
                measure(pg, reloaded=True)
        finally:
            context.close()
            browser.close()
    return shots


def _expected(text: str) -> str:
    for start, direction in EXPECT:
        if text.startswith(start):
            return direction
    raise AssertionError(f"a block the test does not know: {text[:60]!r}")


def _starts_left(lines: list[list[int]]) -> bool:
    return all(left <= 3 for left, _right in lines)


def _starts_right(lines: list[list[int]]) -> bool:
    return all(right <= 3 for _left, right in lines)


def _sits(lines: list[list[int]], direction: str) -> bool:
    """Every line starts at the language's edge, and the last (short) line
    leaves the other edge free -- so the measurement says which edge."""
    if not lines:
        return False
    last_left, last_right = lines[-1]
    if direction == "ltr":
        return _starts_left(lines) and last_right > 20
    return _starts_right(lines) and last_left > 20


def _check(shot: dict, lang: str) -> None:
    assert shot["page"] == ("rtl" if lang == "ar" else "ltr"), shot["page"]
    # R1: two paragraphs, a quote, two list items; R2: three, a quote, two.
    assert len(shot["blocks"]) == 11, [b["text"][:30] for b in shot["blocks"]]
    for block in shot["blocks"]:
        want = _expected(block["text"])
        assert block["dir"] == want and _sits(block["lines"], want), (lang, block)
    # The tool rows: the English refusal (the text the owner reported) and the
    # Arabic file.
    by_tool = {("cron.db" in t["text"]): t for t in shot["tools"]}
    refusal, note = by_tool.get(True), by_tool.get(False)
    assert refusal and note, shot["tools"]
    assert refusal["dir"] == "ltr" and _sits(refusal["lines"], "ltr"), (lang, refusal)
    assert note["dir"] == "rtl" and _sits(note["lines"], "rtl"), (lang, note)
    # Content outside the chat's own blocks: the global rule, which keeps a
    # direction a script decided.
    (content,) = shot["content"]
    assert _sits(content["lines"], "ltr"), (lang, content)
    (decided,) = shot["decided"]
    assert decided["dir"] == "rtl" and _sits(decided["lines"], "rtl"), (lang, decided)


def _same_layout(a: dict, b: dict) -> bool:
    """One block, laid out the same in two UIs: its width, and each line."""
    if abs(a["width"] - b["width"]) > 2 or len(a["lines"]) != len(b["lines"]):
        return False
    return all(abs(x - y) <= 2 for la, lb in zip(a["lines"], b["lines"]) for x, y in zip(la, lb))


def test_every_paragraph_sits_by_its_own_language_in_either_ui(harness: Harness) -> None:
    arabic_ui, english_ui = _run(harness, "ar"), _run(harness, "en")
    for lang, shots in (("ar", arabic_ui), ("en", english_ui)):
        for shot in shots:
            _check(shot, lang)
    # Whatever the UI's language: each paragraph the same, line for line. The
    # Arabic UI aligned replies to the start and the English UI justified
    # them, so a wrapped line sat differently in each.
    for ar_shot, en_shot in zip(arabic_ui, english_ui):
        different = [
            (a["text"][:30], a["lines"], b["lines"])
            for a, b in zip(ar_shot["blocks"], en_shot["blocks"])
            if not _same_layout(a, b)
        ]
        assert not different, different
    # The long paragraphs wrapped, so the comparison saw inner lines.
    assert len(arabic_ui[0]["blocks"][0]["lines"]) >= 3, arabic_ui[0]["blocks"][0]


def _block(shot: dict, start: str) -> dict:
    return next(b for b in shot["blocks"] if b["text"].startswith(start))


def test_the_old_rules_right_aligned_english_in_the_arabic_ui(harness: Harness) -> None:
    """Negative control: the instrument sees the bug the owner reported."""
    (shot,) = _run(harness, "ar", before_send=_ADD_OLD_CSS_JS, before_measure=_OLD_TOOL_ROWS_JS,
                   reload=False)
    first = _block(shot, "Safety: kazma_data")["lines"][0]
    assert first[1] <= 3 and first[0] > 20, first  # "Safety:" alone, at the right
    (content,) = shot["content"]
    assert _starts_right(content["lines"]) and content["lines"][-1][0] > 20, content
    refusal = next(t for t in shot["tools"] if "cron.db" in t["text"])
    assert refusal["dir"] == "rtl" and _starts_right(refusal["lines"]), refusal


def test_a_content_rule_without_the_exemption_overrode_a_decided_direction(harness: Harness) -> None:
    """Negative control: the content rule applied to an element whose dir a
    script set put that Arabic sentence left-to-right, by its first letter."""
    rule = '[translate="no"] :not(pre, code) { unicode-bidi: plaintext !important; }'
    inject = ("() => { const s = document.createElement('style'); s.textContent = "
              + json.dumps(rule) + "; document.head.appendChild(s); }")
    (shot,) = _run(harness, "ar", before_measure=inject, reload=False)
    (decided,) = shot["decided"]
    # Geometry only: plaintext lays a paragraph out by its first letter but
    # leaves the computed `direction` (the dir attribute's) as it was. And
    # under plaintext a <br> starts a new paragraph, so only the first line
    # ("PDF ...") goes left; "تم." after it stays right.
    first = decided["lines"][0]
    assert first[0] <= 3 and first[1] > 20, decided


def test_without_a_direction_per_paragraph_arabic_ran_left_to_right(harness: Harness) -> None:
    """Negative control: the renderer's dir="auto" and the message-wide isolation."""
    (shot,) = _run(harness, "en", before_send="() => { window.KazmaBidi.blockDir = undefined; }",
                   reload=False)
    arabic = _block(shot, "ملاحظة: يمكنك")
    assert arabic["dir"] == "ltr" and _starts_left(arabic["lines"]), arabic


def test_the_replies_are_what_the_controls_need() -> None:
    """R1 counts as mostly English and R2 as mostly Arabic by the bidi helper's
    own measure, or the second control tests something else: the old isolation
    hid an Arabic paragraph's letters only in a reply measured as English."""
    import shutil
    import subprocess

    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    bidi_js = Path(__file__).resolve().parents[2] / "kazma-ui" / "kazma_ui" / "static" / "js" / "bidi.js"
    script = (
        "global.window = global; global.document = {readyState: 'loading', addEventListener() {},"
        " documentElement: {getAttribute: () => 'ltr'}};"
        "eval(require('fs').readFileSync(process.argv[1], 'utf8'));"
        "const replies = JSON.parse(require('fs').readFileSync(0, 'utf8'));"
        "console.log(JSON.stringify(replies.map((r) => window.KazmaBidi.isArabicDominant(r))));"
    )
    out = subprocess.run(
        [node, "-e", script, str(bidi_js)], input=json.dumps([R1, R2]),
        capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout.strip()) == [False, True]
