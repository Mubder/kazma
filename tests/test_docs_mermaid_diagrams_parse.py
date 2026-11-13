"""Every Mermaid diagram in the docs parses with the docs site's own Mermaid
(2026-11-13).

The site draws a diagram in the reader's browser, so a diagram Mermaid cannot
parse leaves no picture and no build error: Docusaurus builds the page, and the
browser logs a parse error nobody reads. The Commitment Layer page's flowchart
had not drawn at all, found by opening it after the move to Mermaid 12: its
node ``U[User: "remind me about the meeting"]`` has a quote inside an unquoted
label, which Mermaid refuses (a whole label in quotes, with ``#quot;`` for a
quote inside it, is the form it takes).

This loads the Mermaid the site ships (``docs/node_modules/mermaid``, the
version ``docs/package-lock.json`` holds) into a blank Chromium page and asks
its parser about every ``mermaid`` block under ``docs/``. It needs the docs
site's packages installed (``cd docs && npm install``), as building the site
does, and Playwright's Chromium; without the packages it skips, as CI's suite
(which does not build the docs) does.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
MERMAID_BUNDLE = DOCS / "node_modules" / "mermaid" / "dist" / "mermaid.min.js"
FENCE = re.compile(r"^```mermaid[^\n]*\n(.*?)^```", re.MULTILINE | re.DOTALL)

#: The Commitment Layer page's node before the fix: a quote inside an unquoted label.
OLD_COMMITMENT_DIAGRAM = 'flowchart LR\n    U[User: "remind me about the meeting"] --> M[Model]\n'


def mermaid_blocks() -> list[tuple[str, int, str]]:
    """(page, line, diagram) for every Mermaid block in the docs."""
    found = []
    for page in sorted(DOCS.rglob("*.md*")):
        if "node_modules" in page.parts or page.suffix not in (".md", ".mdx"):
            continue
        text = page.read_text(encoding="utf-8")
        for match in FENCE.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            found.append((page.relative_to(ROOT).as_posix(), line, match.group(1)))
    return found


async def _parse_errors(diagrams: list[str]) -> list[str | None]:
    pw = pytest.importorskip("playwright.async_api")
    async with pw.async_playwright() as runtime:
        if not Path(runtime.chromium.executable_path).is_file():
            pytest.fail("Playwright's Chromium is not installed (python -m playwright install chromium)")
        browser = await runtime.chromium.launch()
        try:
            page = await browser.new_page()
            await page.set_content("<!doctype html><html><body></body></html>")
            await page.add_script_tag(path=str(MERMAID_BUNDLE))
            return await page.evaluate(
                """async (diagrams) => {
                    mermaid.initialize({startOnLoad: false});
                    const errors = [];
                    for (const text of diagrams) {
                        try { await mermaid.parse(text); errors.push(null); }
                        catch (error) { errors.push(String(error && error.message || error).split('\\n').slice(0, 3).join(' ')); }
                    }
                    return errors;
                }""",
                diagrams,
            )
        finally:
            await browser.close()


@pytest.fixture(scope="module")
def parse():
    if not MERMAID_BUNDLE.is_file():
        pytest.skip("the docs site's packages are not installed (cd docs && npm install)")
    return lambda diagrams: asyncio.run(_parse_errors(diagrams))


def test_the_docs_have_mermaid_diagrams():
    assert len(mermaid_blocks()) >= 8, "the block finder found fewer diagrams than the docs hold"


def test_every_docs_diagram_parses(parse):
    blocks = mermaid_blocks()
    errors = parse([diagram for _page, _line, diagram in blocks])
    broken = [f"{page}:{line}: {error}" for (page, line, _), error in zip(blocks, errors) if error]
    assert not broken, "\n".join(broken)


def test_negative_control_the_old_commitment_diagram_is_refused(parse):
    error, fixed = parse([OLD_COMMITMENT_DIAGRAM,
                          OLD_COMMITMENT_DIAGRAM.replace('[User: "remind me about the meeting"]',
                                                         '["User: #quot;remind me about the meeting#quot;"]')])
    assert error and "Parse error" in error
    assert fixed is None
