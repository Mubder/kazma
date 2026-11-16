"""Fuzz the Telegram converter: any reply becomes HTML Telegram accepts.

``kazma_gateway.telegram_format.md_to_tg_html`` turns a model's Markdown into
Telegram's HTML subset. Whatever the reply holds, the output uses only the
tags Telegram knows, every tag is closed in order, and outside the tags no
``<`` or ``>`` is left raw and every ``&`` starts an entity: Telegram refuses
the whole message otherwise, and the reply never arrives.

Run under Atheris: ``python fuzz/fuzz_telegram_format.py -max_total_time=60``.
"""

from __future__ import annotations

import re
import sys

TOKENS = (
    "**", "__", "_", "*", "`", "```", "```py\n", "~~", "||", "[", "](", ")",
    "https://a.example/?q=1&r=2", "<", ">", "&", "&amp;", "\n", "\n\n", "> ",
    "# ", "## ", "- ", "1. ", "|", "a", "b", "ب", " ", "\\", "\"", "'",
)
SEEDS = (b"**bold** _it_ `code`", b"```\nx<1 & y\n```", b"[l](https://a.b/?q=1&r=2)", b"**unclosed _x")

_TAGS = ("b", "i", "u", "s", "code", "pre", "a", "blockquote", "tg-spoiler", "strong", "em")
_TAG = re.compile(r'<(/?)([a-z-]+)((?:\s+[a-z-]+="[^"<>]*")*)\s*>')
_ENTITY = re.compile(r"&(?:lt|gt|amp|quot|#\d+|#x[0-9a-fA-F]+);")


def _well_formed(html: str) -> str | None:
    """Why *html* is not Telegram HTML, or None."""
    stack: list[str] = []
    pos = 0
    for match in _TAG.finditer(html):
        text = html[pos:match.start()]
        pos = match.end()
        if "<" in text or ">" in text:
            return f"raw angle bracket in {text!r}"
        if len(_ENTITY.findall(text)) != text.count("&"):
            return f"bare & in {text!r}"
        closing, name = match.group(1), match.group(2)
        if name not in _TAGS:
            return f"tag {name!r}"
        if closing:
            if not stack or stack[-1] != name:
                return f"</{name}> closes {stack[-1:] or 'nothing'}"
            stack.pop()
        else:
            stack.append(name)
    rest = html[pos:]
    if "<" in rest or ">" in rest:
        return f"raw angle bracket in {rest!r}"
    if len(_ENTITY.findall(rest)) != rest.count("&"):
        return f"bare & in {rest!r}"
    return f"unclosed {stack}" if stack else None


def check(data: bytes) -> None:
    from kazma_gateway.telegram_format import md_to_tg_html

    text = data.decode("utf-8", errors="replace")
    html = md_to_tg_html(text)
    problem = _well_formed(html)
    assert problem is None, (problem, text, html)


def main() -> None:
    import atheris

    with atheris.instrument_imports():
        import kazma_gateway.telegram_format  # noqa: F401
    atheris.Setup(sys.argv, check)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
