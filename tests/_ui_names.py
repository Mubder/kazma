"""Words the interface shows as they are in every language.

Products, companies, platforms, protocols and unit symbols are names, not
interface text; an all-capitals word (JSON, MCP, YOLO) is an acronym and is
never counted either. Shared by the static template gate
(``tests/test_templates_have_no_english.py``) and the browser measure
(``tests/e2e/test_pages_read_in_arabic.py``), so the two cannot disagree on
what a name is.
"""

from __future__ import annotations

UI_NAMES = frozenset({
    # products, companies, platforms, protocols
    "Discord",
    "Git",
    "Kazma",
    "Markdown",
    "OAuth",
    "OpenAI",
    "Python",
    "Slack",
    "Telegram",
    # keyboard keys, as printed on them
    "Ctrl",
    "Enter",
    # unit symbols
    "ms",
    "px",
})
