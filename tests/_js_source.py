"""Extract one JavaScript function's body, without swallowing its neighbours.

Several suites assert on the *text* of `chat.js` — "does `_paintHitlFromDoc`
still avoid `_awaitingApproval`?" — because the invariant is about which flags a
paint path is allowed to consult, and there is no headless DOM here to assert it
behaviourally.

Those tests sliced from one named function to another::

    chat.split("function _paintHitlFromDoc(el, doc)", 1)[1].split(
        "function renderTurn(doc, meta)", 1)[0]

which silently widens the moment anything is inserted between the two. On
2026-09-12 that is exactly what had happened: `_isWatchdogNotice` and
`_forcePaintDoneContent` were added between them, so the slice picked up 1,528
characters of unrelated code and the test failed on an `_awaitingApproval`
reference belonging to a *different* function. The invariant it guards was
intact the whole time — verified assertion by assertion against the real body.

A test that fails when a neighbour changes is worse than no test: it costs an
investigation, and once it is written off as "that one's just flaky" it stops
being read at all.

:func:`js_function_body` ends the slice at the next function *at the same
indentation*, so a nested closure stays in and a sibling stays out.
"""

from __future__ import annotations

import re
from pathlib import Path

__all__ = ["js_function_body"]

_FUNC_RE = re.compile(r"^(?P<indent>[ \t]*)(?:async\s+)?function\s")


def js_function_body(source: str | Path, signature: str) -> str:
    """Return *signature*'s function body, up to the next sibling function.

    Args:
        source: the JavaScript text, or a path to read it from.
        signature: the function's opening text, e.g.
            ``"function _paintHitlFromDoc(el, doc)"``. Matched literally.

    Raises:
        AssertionError: if the signature is absent — a renamed function should
            fail loudly rather than silently return an empty string that every
            ``not in`` assertion then passes.
    """
    js = Path(source).read_text(encoding="utf-8") if isinstance(source, Path) else source
    at = js.find(signature)
    assert at != -1, f"function not found in source: {signature!r}"

    lines = js[at:].split("\n")
    # The signature line carries the indentation of this function; the opening
    # line itself is sliced from `at`, so recover it from the source instead.
    line_start = js.rfind("\n", 0, at) + 1
    indent = len(js[line_start:at]) if js[line_start:at].strip() == "" else 0

    out = [lines[0]]
    for line in lines[1:]:
        m = _FUNC_RE.match(line)
        if m is not None and len(m.group("indent").expandtabs(2)) <= indent:
            break
        out.append(line)
    return "\n".join(out)
