"""The research report rubric scores content, not length (2026-11, FA-235).

The rubric was structural only (a title, three headings, a Sources section,
1,500 characters, URLs from two domains), so a padded report with no claim
cited and its paragraphs said twice scored 100/100. Most of the score now
comes from content: paragraphs that cite their sources, listed sources used
in the body, cited links that are listed, nothing repeated, the question's
words covered. Still deterministic, in English and Arabic.

The padded report passing every old structural check is the negative
control: shape alone no longer passes.
"""

from __future__ import annotations

from kazma_core.tools.research_eval import score_report_markdown

TOPIC = "Python GIL and free threading"
SOURCES = (
    "\n\n## Sources\n\n"
    "- https://docs.python.org/3/library/threading.html\n"
    "- https://peps.python.org/pep-0703/\n"
    "- https://realpython.com/python-gil/\n"
)

PADDED = (
    "# Research report\n\n## Background\n\n"
    + ("Concurrency is an important topic for many programs in many settings and contexts. " * 6)
    + "\n\n## Key findings\n\n"
    + ("Concurrency is an important topic for many programs in many settings and contexts. " * 6)
    + "\n\n## Conclusion\n\n"
    + ("There are many considerations that matter when one weighs the options in practice. " * 6)
    + SOURCES
)

CITED = (
    "# Research report: the Python GIL\n\n## Background\n\n"
    "CPython's GIL lets one thread run Python bytecode at a time, so CPU-bound threads gain "
    "little on several cores (https://docs.python.org/3/library/threading.html).\n\n"
    "## Key findings\n\n"
    "PEP 703 makes the GIL optional in a free-threading build, paying for it with per-object "
    "locks and biased reference counting (https://peps.python.org/pep-0703/).\n\n"
    "Measured programs that are I/O-bound already release the lock while they wait, which is "
    "why threads still help them today (https://realpython.com/python-gil/).\n\n"
    "## Conclusion\n\n"
    "Free threading moves the GIL's cost from every program to the ones that opt in, as the "
    "PEP's own measurements show (https://peps.python.org/pep-0703/).\n"
    + SOURCES
)


def test_a_cited_report_passes():
    rub = score_report_markdown(CITED, min_sources=3, min_chars=300, topic=TOPIC)
    assert rub.ok and rub.score >= 90, rub.to_dict()


def test_a_padded_report_with_nothing_cited_fails():
    rub = score_report_markdown(PADDED, min_sources=3, min_chars=300, topic=TOPIC)
    assert not rub.ok and rub.score < 70, rub.to_dict()
    for check in ("cited_paragraphs", "sources_used", "no_repetition", "answers_topic"):
        assert rub.checks[check] is False, check


def test_negative_control_the_padded_report_has_every_old_shape():
    rub = score_report_markdown(PADDED, min_sources=3, min_chars=300, topic=TOPIC)
    for check in ("has_title", "min_length", "has_sources_section", "min_urls", "min_domains",
                  "has_headings", "not_error_only", "has_body"):
        assert rub.checks[check] is True, check


def test_a_link_cited_but_not_listed_is_found():
    rub = score_report_markdown(CITED.replace("(https://realpython.com/python-gil/)",
                                              "(https://example.org/elsewhere)"),
                                min_sources=3, min_chars=300)
    assert rub.checks["citations_resolve"] is False


def test_an_arabic_report_is_read_too():
    arabic = (
        "# تقرير البحث\n\n## الخلفية\n\n"
        "يسمح قفل المفسر العام في بايثون لخيط واحد فقط بتنفيذ الشيفرة في كل لحظة، فلا تستفيد "
        "الخيوط كثيفة الحساب من الأنوية المتعددة (https://docs.python.org/3/library/threading.html).\n\n"
        "## النتائج\n\n"
        "يجعل المقترح 703 القفل اختيارياً في بناء التنفيذ الحر، مقابل أقفال لكل كائن وعدّ مراجع "
        "منحاز يحمل كلفة التغيير (https://peps.python.org/pep-0703/).\n\n"
        "## المصادر\n\n- https://docs.python.org/3/library/threading.html\n- https://peps.python.org/pep-0703/\n"
    )
    rub = score_report_markdown(arabic, min_sources=2, min_chars=200, topic="قفل المفسر في بايثون")
    assert rub.checks["has_sources_section"] and rub.checks["cited_paragraphs"], rub.to_dict()
    assert rub.ok, rub.to_dict()
