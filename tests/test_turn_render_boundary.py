"""One renderer owns the turn block — ``docs/plans/UNIFIED_TURN_BLOCK.md`` §13.

    Add a narrow architecture check prohibiting retired bar markup and
    unauthorized turn-content writers. Static checks are boundary
    enforcement, not substitutes for browser tests.

Narrow is the operative word. These are boundary checks: they say who may
write into a turn block and what markup may not come back. They say
nothing about whether the result looks right — that is
``tests/e2e/test_unified_turn_browser.py``'s job, and neither file can
stand in for the other.

The two failures they exist to catch have both happened:

* **A second status surface.** The Live Task Card was a docked bar with
  its own writer, its own timers and its own idea of the turn's state.
  Phase 2 removed it. Invariant U02 is one block per turn, and plan §15
  puts "a second status surface, side panel, or transcript copy" out of
  scope permanently.
* **A second DOM writer.** ``ensureProgressPanel`` painted a progress
  panel whenever the projector module was missing. Phase 5 removed it —
  see §14.4, "never run two DOM writers", and invariant U03, only the
  renderer mutates.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "kazma-ui" / "kazma_ui"
STATIC = UI / "static"
CHAT_JS = STATIC / "js" / "chat.js"
TURN_VIEW = STATIC / "js" / "modules" / "turn_view.js"


def _shipped_files(*globs: str) -> list[Path]:
    out: list[Path] = []
    for pattern in globs:
        out.extend(sorted(STATIC.glob(pattern)))
    out.extend(sorted((UI / "templates").glob("*.html")))
    assert out, "no shipped front-end files found"
    return out


def _code_lines(path: Path) -> list[tuple[int, str]]:
    """Source lines with comment-only lines dropped.

    A tombstone is documentation, not markup: "What stood here was the
    Live Task Card" must stay readable, and a check that forbids naming
    the thing you removed makes the removal harder to explain later.
    """
    lines: list[tuple[int, str]] = []
    in_block = False
    for i, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if in_block:
            if "*/" in line:
                in_block = False
                line = line.split("*/", 1)[1].strip()
            else:
                continue
        if line.startswith("/*"):
            if "*/" not in line:
                in_block = True
            continue
        if line.startswith("//") or line.startswith("*"):
            continue
        if line.startswith("<!--") and "-->" in line:
            continue
        if line.startswith("<!--"):
            in_block = True
            continue
        if line:
            lines.append((i, line))
    return lines


# ══════════════════════════════════════════════════════════════════════
# The retired bar
# ══════════════════════════════════════════════════════════════════════

#: Every hook the retired surfaces were addressed by. An id or class is
#: what makes markup reachable — CSS can style it, JS can find it, and a
#: second writer can start feeding it again.
RETIRED_HOOKS = (
    "live-task-card",
    "live-task-",
    "thinking-indicator",
)


@pytest.mark.parametrize("hook", RETIRED_HOOKS)
def test_no_retired_bar_markup_ships(hook: str) -> None:
    """Plan §9's removal inventory, enforced instead of remembered."""
    offenders: list[str] = []
    for path in _shipped_files("js/**/*.js", "css/*.css"):
        for lineno, line in _code_lines(path):
            if hook in line:
                offenders.append(
                    f"{path.relative_to(ROOT)}:{lineno}: {line[:90]}"
                )
    assert not offenders, (
        f"retired surface {hook!r} is reachable again:\n  "
        + "\n  ".join(offenders[:10])
    )


def test_the_tombstones_survive() -> None:
    """The inverse check, and it is not a joke.

    A rule that only forbids is satisfied by deleting the explanation
    along with the code. These comments are why the bar is gone; losing
    them is how it comes back.
    """
    chat_html = (UI / "templates" / "chat.html").read_text(encoding="utf-8")
    assert "The Live Task Card stood here" in chat_html, (
        "the template lost the note explaining what used to be there"
    )
    js = CHAT_JS.read_text(encoding="utf-8")
    assert "What stood here was the Live Task Card" in js


# ══════════════════════════════════════════════════════════════════════
# The one writer
# ══════════════════════════════════════════════════════════════════════


def test_the_legacy_progress_painter_is_gone() -> None:
    """Phase 5's removal, and the reason it was safe.

    ``ensureProgressPanel`` was reachable only when the projector module
    failed to load — and in that state ``_docs.live`` is never created,
    so ``_answerFromDoc`` returns "" and the turn has no answer at all.
    A fallback that can only ever produce a half-rendered turn is not a
    safety net.
    """
    js = CHAT_JS.read_text(encoding="utf-8")
    # Code lines only: the comment that explains the removal names the
    # function, and a rule that forbids saying what you deleted makes the
    # deletion impossible to explain.
    live = "\n".join(line for _lineno, line in _code_lines(CHAT_JS))
    assert "function ensureProgressPanel(" not in live
    assert "ensureProgressPanel()" not in live
    # ...and logProgress must not grow a new one: it feeds the projector
    # and returns.
    body = js.split("function logProgress(step) {", 1)[1]
    body = body.split("\n  function ", 1)[0]
    assert "applyTurnEvent(" in body, "logProgress no longer feeds the document"
    assert "createElement" not in body, (
        "logProgress builds DOM again; it is a dispatcher, not a painter"
    )


def test_only_the_view_module_writes_the_turn_regions() -> None:
    """Invariant U03: only the renderer mutates.

    The turn block's own regions — header, activity fold, approval group
    — are built and ordered inside ``turn_view.js``. ``chat.js`` supplies
    builders and painters that the view CALLS; what it must not do is
    insert them into a bubble itself, because two inserters is how a
    region ends up rendered twice (the ``duplicate-region:`` diagnostic
    the renderer now reports).
    """
    js = CHAT_JS.read_text(encoding="utf-8")
    offenders = []
    for lineno, line in _code_lines(CHAT_JS):
        if re.search(r"\.(appendChild|insertBefore)\(", line) and re.search(
            r"turnHeader|approvalGroup|workbench|_progressEl", line
        ):
            offenders.append(f"chat.js:{lineno}: {line[:90]}")
    assert not offenders, (
        "chat.js inserts a turn region directly instead of returning it to "
        "the renderer:\n  " + "\n  ".join(offenders[:10])
    )
    # The renderer is the one that orders regions, and it says so.
    view = TURN_VIEW.read_text(encoding="utf-8")
    assert "content.insertBefore(node, want || null)" in view, (
        "turn_view no longer performs the single ordering pass"
    )
    assert js.count("function renderTurn(") == 1


def test_the_renderer_reports_duplicates() -> None:
    """Plan §11 asks for bounded invariant diagnostics.

    Everything else a duplicate could trip is satisfied by a duplicate —
    the answer is present, the gate has a row, nothing is missing. It
    just says everything twice. Counting is the only way to see it.
    """
    view = TURN_VIEW.read_text(encoding="utf-8")
    for probe in ("duplicate-region:", "gate-outside-group:",
                  "gate-missing:", "text-missing", "text-blank"):
        assert probe in view, f"the renderer no longer reports {probe!r}"
