"""Text follows its own language, whatever the UI's (the owner's rule, 2026-10-02).

English text is laid out left-to-right and aligned left, Arabic right-to-left
and aligned right, in the Arabic UI and the English UI alike; the UI language
lays out the page and its own labels. Live in the Arabic UI every chat reply
was right-aligned (``[dir="rtl"] .message-assistant .message-text {
text-align: right }``), the plan's step text was forced ``direction: rtl``,
data shown through templates and scripts took the page's direction, and four
routes chose their language from the cookie with an English fallback, so an
Arabic install with no cookie showed the Dashboard's budget line in English.

The browser half (``tests/e2e/test_text_follows_its_language.py``) measures
where each line sits; this half holds the sources to the rule.
"""

from __future__ import annotations

import re
from pathlib import Path

from tests._template_bindings import text_data_outside_content

REPO = Path(__file__).resolve().parents[1]
UI = REPO / "kazma-ui" / "kazma_ui"
CSS = [UI / "static" / "css" / "kazma.css", UI / "static" / "css" / "kazma.v5.css"]

#: Rules that may set a physical alignment or direction, each with its reason.
#: Matched against a rule's whole selector text (whitespace normalized).
DECLARED = {
    # Code and math are left-to-right in every language.
    '[dir="rtl"] pre, [dir="rtl"] code, [dir="rtl"] .code-block, [dir="rtl"] .highlight': "code",
    ".katex, .math-inline, .math-block, .math-bidi-isolate, span[data-math=\"true\"]": "math",
    ".message-text pre": "code",
    '[dir="rtl"] pre, [dir="rtl"] code, [dir="rtl"] .message-text pre, [dir="rtl"] .message-text code': "code",
    ".plan-step-duration, .plan-step-id, .plan-step-code": "durations and ids",
    ".agent-progress-step .step-time": "a time",
    # Keyed to the element's OWN language, not the page's.
    '.chat-message.streaming[data-lang="ar"] .message-text, .message-text.streaming-lock-ar, .streaming-lock-rtl':
        "an Arabic reply while it streams",
    '.ar-dominant, [dir="rtl"].bidi-content, [dir="rtl"].markdown-body, [dir="rtl"].message-text':
        "the element's own dir",
    '.en-dominant, [dir="ltr"].bidi-content, [dir="ltr"].message-text': "the element's own dir",
    # The plan box's own layout (the step number on the start side) follows the
    # page or an Arabic plan; each step's text has its own dir="auto" (chat.js).
    '[dir="rtl"] .plan-block, [dir="rtl"] .workbench-plan, [dir="rtl"] .plan-checklist, [dir="rtl"] .agent-plan, '
    '.chat-message[data-lang="ar"] .plan-block, .chat-message[data-lang="ar"] .agent-plan': "the plan box's layout",
    # Layout, not text: chart canvases, a grid's order, a utility, a number column.
    '[dir="rtl"] .chart-card canvas': "a chart's canvas (its labels are drawn from the left edge)",
    '[dir="rtl"] .swarm-container .metrics-grid': "grid order",
    ".text-right": "a utility class a page asks for",
    ".pc-pmeta": "a check's result column",
}


def rules(css: str) -> list[tuple[str, str]]:
    """``(selector, body)`` of every rule, comments removed."""
    css = re.sub(r"/\*.*?\*/", " ", css, flags=re.S)
    return [(" ".join(m.group(1).split()), m.group(2)) for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", css)]


def physical(css: str) -> list[str]:
    """Rules that align or direct text physically and are not declared."""
    found = []
    for selector, body in rules(css):
        if selector in DECLARED or selector.startswith("@"):
            continue
        if re.search(r"text-align:\s*(?:right|left)\b", body) and "[x-xs-" not in selector:
            if selector.startswith((".xs-", ".xs-row", ".sched-summary", ".audit-")):
                continue  # X Studio and Scheduled bodies: keyed to the post's own language
            found.append(selector)
        elif re.search(r"(?<![-\w])direction:\s*(?:rtl|ltr)\b", body) and "dir=" in selector:
            found.append(selector)
    return found


def test_no_text_is_aligned_by_the_pages_direction() -> None:
    problems = [f"{path.name}: {sel}" for path in CSS for sel in physical(path.read_text(encoding="utf-8"))]
    assert not problems, (
        "Align text logically (text-align: start) so each paragraph follows its own "
        "language, or declare the rule in DECLARED with why:\n  " + "\n  ".join(problems)
    )


def test_the_check_sees_the_old_rules() -> None:
    """Negative control: the rules the Arabic UI had until 2026-10-02."""
    old = (
        '[dir="rtl"] .message-assistant .message-text { text-align: right; }\n'
        '[dir="rtl"] .agent-plan-item .plan-text { text-align: right; direction: rtl; }\n'
        ".dropdown-item { text-align: left; }\n"
    )
    assert physical(old) == [
        '[dir="rtl"] .message-assistant .message-text',
        '[dir="rtl"] .agent-plan-item .plan-text',
        ".dropdown-item",
    ]


_PAGE_RTL = re.compile(r"""^(?:html|:root|body)?\[dir=["']?rtl["']?\]\s+""")


def page_keyed_bubble_alignment(css: str) -> list[str]:
    """Selectors keyed to the PAGE's direction that align a chat bubble's text
    (the bubble itself: code inside it is left-to-right in every language)."""
    return [
        part.strip()
        for selector, body in rules(css)
        if re.search(r"text-align(?:-last)?\s*:", body)
        for part in selector.split(",")
        if _PAGE_RTL.match(part.strip()) and ".message-text" in part.split()[-1]
    ]


def test_a_reply_is_laid_out_the_same_in_either_ui() -> None:
    """The Arabic UI aligned replies to the start while the English UI
    justified them, so a wrapped line sat differently in each. The browser
    half compares the two UIs line by line."""
    found = [f"{path.name}: {sel}" for path in CSS
             for sel in page_keyed_bubble_alignment(path.read_text(encoding="utf-8"))]
    assert not found, found


def test_the_bubble_check_sees_the_old_rules() -> None:
    old = (
        '[dir="rtl"] .message-user .message-text { border-radius: 0; text-align: start; }\n'
        '[dir="rtl"] .message-assistant .message-text { text-align: right; }\n'
        '.message-text[dir="rtl"] { text-align: start; }\n'
    )
    assert page_keyed_bubble_alignment(old) == [
        '[dir="rtl"] .message-user .message-text',
        '[dir="rtl"] .message-assistant .message-text',
    ]


def test_content_takes_its_direction_from_its_own_text() -> None:
    """``translate="no"`` marks content; it and everything in it -- never code,
    and never an element whose ``dir`` a script decided (the renderer's
    per-paragraph direction, a ``<bdi dir="ltr">`` around a tool call) --
    take each paragraph's direction from its first strong letter."""
    css = (UI / "static" / "css" / "kazma.css").read_text(encoding="utf-8")
    plaintext = [sel for sel, body in rules(css) if re.search(r"unicode-bidi:\s*plaintext", body)]
    joined = " , ".join(plaintext)
    assert '[translate="no"]:not([dir="ltr"], [dir="rtl"], pre, code, kbd, samp)' in joined
    assert ('[translate="no"] :not([dir="ltr"], [dir="rtl"], [translate="yes"], [translate="yes"] *, '
            'pre, code, kbd, samp, pre *, code *)' in joined)
    assert 'input[type="text"]' in joined and "textarea" in joined
    # Interface words inside content follow the page, as an isolate.
    isolates = [sel for sel, body in rules(css) if re.search(r"unicode-bidi:\s*isolate", body)]
    assert '[translate="no"] [translate="yes"]' in isolates


def test_the_plan_text_is_not_forced_right_to_left() -> None:
    css = (UI / "static" / "css" / "kazma.css").read_text(encoding="utf-8")
    for selector, body in rules(css):
        if ".plan-text" in selector:
            assert not re.search(r"(?<![-\w])direction:", body), selector


def test_every_text_binding_sits_in_content() -> None:
    problems = [
        f"{path.relative_to(UI / 'templates')}:{b.line}: {b.attr}=\"{' '.join(b.expr.split())[:80]}\""
        for path in sorted((UI / "templates").rglob("*.html"))
        for b in text_data_outside_content(path.read_text(encoding="utf-8"))
    ]
    assert not problems, (
        "A binding that shows data (a name, a title, an error) sits in content: "
        'translate="no" on it or an ancestor:\n  ' + "\n  ".join(problems)
    )


def test_the_binding_check_tells_data_from_interface() -> None:
    """Negative control: data outside content is found; translations, counts,
    flags and content are not."""
    page = """
    <div>
      <span x-text="item.title"></span>
      <p x-text="ghError"></p>
      <span x-text="t('k')"></span>
      <span x-text="busy ? t('a') : t('b')"></span>
      <span x-text="items.length"></span>
      <div translate="no"><span x-text="item.name"></span></div>
      <span :translate="x ? 'no' : null" x-text="item.reason"></span>
    </div>
    """
    found = [b.expr for b in text_data_outside_content(page)]
    assert found == ["item.title", "ghError"]


def test_only_the_middleware_reads_the_language_cookie() -> None:
    """``i18n.current_language()`` is the one answer: the reader's choice, else
    the install's. Four routes read the cookie with an English fallback."""
    reads = []
    for path in sorted(UI.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for n, line in enumerate(text.splitlines(), 1):
            if re.search(r"cookies\.get\(\s*[\"']kazma-lang[\"']", line):
                reads.append(f"{path.relative_to(UI)}:{n}")
    assert len(reads) == 1 and reads[0].startswith("app.py:"), reads


def test_current_language_falls_back_to_the_install() -> None:
    import contextvars

    from kazma_ui import i18n

    def check() -> None:
        i18n.set_install_language("ar")
        try:
            assert i18n.current_language() == "ar"  # no choice: the install's
            i18n.set_request_language("en")
            assert i18n.current_language() == "en"  # the reader's choice
        finally:
            i18n.set_install_language("en")

    contextvars.copy_context().run(check)
