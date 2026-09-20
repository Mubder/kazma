"""Accessibility and interaction — ``docs/plans/UNIFIED_TURN_BLOCK.md`` §11.

    - Semantic disclosure buttons with ``aria-expanded`` and stable
      accessible names.
    - Announce coarse status/approval changes, not every token or timer tick.
    - Preserve focus during keyed updates; restore it intentionally after a
      control disappears.
    - Keyboard-operable gate rows and controls; readable mobile layout, RTL,
      reduced motion, adequate contrast.
    - Keep existing scroll-pinning behavior: only auto-follow when the user
      is already following. Approvals must not pull a reader away from
      inspected content.

These are structural checks over the shipped markup, handlers and CSS.
Focus retention across a live update is behavioral and belongs in the
browser suite; it is named here and owned there rather than approximated.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "kazma-ui" / "kazma_ui"


@pytest.fixture(scope="module")
def chat_js() -> str:
    return (UI / "static" / "js" / "chat.js").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def css() -> str:
    return (UI / "static" / "css" / "kazma.css").read_text(encoding="utf-8")


def _block(src: str, start: str, end: str) -> str:
    a = src.index(start)
    return src[a : src.index(end, a)]


# ══════════════════════════════════════════════════════════════════════════
# The disclosure
# ══════════════════════════════════════════════════════════════════════════


def test_the_thoughts_disclosure_is_a_real_disclosure(chat_js: str) -> None:
    """A div that toggles a class is not a disclosure to a screen reader.

    It needs a role, a tab stop, ``aria-expanded`` that tracks the actual
    state, and ``aria-controls`` naming the region it opens.
    """
    build = _block(
        chat_js,
        "function _buildRestoredWorkbench(activity, turnId)",
        "\n  function ",
    )
    assert 'role="button"' in build
    assert 'tabindex="0"' in build
    assert 'aria-expanded="false"' in build, "the default must be announced"
    assert 'aria-controls="' in build, "the toggle names no region"

    # aria-expanded is written by the ONE place that applies the fold, so
    # it cannot drift from the class the CSS reads.
    fold = _block(chat_js, "function _applyActivityFold(panel, turnId)",
                  "\n  /** The ONE place")
    assert "aria-expanded" in fold
    assert "String(open)" in fold, (
        "aria-expanded is being set from something other than the resolved "
        "open state"
    )


def test_the_disclosure_is_keyboard_operable(chat_js: str) -> None:
    """Enter and Space, and the same handler the mouse uses."""
    build = _block(
        chat_js,
        "function _buildRestoredWorkbench(activity, turnId)",
        "\n  function ",
    )
    assert "addEventListener('keydown'" in build
    assert "e.key === 'Enter'" in build and "e.key === ' '" in build
    assert "e.preventDefault()" in build, (
        "Space would scroll the page as well as toggling"
    )
    # One handler for both, or they drift.
    assert build.count("toggle()") >= 2


# ══════════════════════════════════════════════════════════════════════════
# Announcements
# ══════════════════════════════════════════════════════════════════════════


def test_the_header_announces_phase_not_every_tick(chat_js: str) -> None:
    """A per-second rewrite of a live region re-announces the whole thing.

    The retired task card learned this the hard way and kept its visible
    header ``aria-hidden`` with a static accessible name. The turn header
    is repainted every second by the display ticker, so the same rule
    applies: the live region only changes when the PHASE does.
    """
    build = _block(chat_js, "function _buildTurnHeader(turnId)",
                   "\n  /**")
    assert 'role="status"' in build
    assert 'aria-live="polite"' in build
    assert "sr-only" in build, "the live region is visible chrome"
    # The phase glyph carries no meaning a reader needs spelled out.
    assert 'class="turn-header-phase" aria-hidden="true"' in build

    paint = _block(chat_js, "function _paintTurnHeader(el, doc)",
                   "\n  // ══")
    assert "data-said" in paint, (
        "the live region has no dedupe, so a 1s repaint re-announces the "
        "phase every second"
    )
    assert "if (live.getAttribute('data-said') !== say)" in paint


def test_the_approval_group_is_a_named_group(chat_js: str) -> None:
    """Four rows in a region need the region to say what it is."""
    build = _block(chat_js, "function _buildApprovalGroup()",
                   "\n  /**")
    assert "'role', 'group'" in build or 'role="group"' in build
    assert "turn-approvals-title" in build
    assert "turn-approvals-count" in build, (
        "the group states no count, so a reader cannot tell one request "
        "from four without counting rows"
    )


# ══════════════════════════════════════════════════════════════════════════
# Focus and scrolling
# ══════════════════════════════════════════════════════════════════════════


def test_a_repaint_does_not_rebuild_a_row_under_the_reader(chat_js: str) -> None:
    """Focus survives because the NODE survives.

    Keyed rows are created once and repainted in place, so a control the
    reader has focused is the same element after an update. The one
    exception is the deliberate rebuild of a frozen hydration card, which
    is a stated fact about that node rather than a churn.
    """
    group = _block(chat_js, "function _paintApprovalGroup(el, entry, ctx)",
                   "\n  /**")
    assert "byKey[row.key]" in group, "rows are not keyed"
    assert "el.__kzRows" in group, "the row table does not live on the node"
    assert group.count("removeChild") == 1, (
        "the group removes rows on more than the one stated condition; "
        "every removal is focus the reader loses"
    )
    # Ordering compares before it moves, so an unchanged region does not
    # touch the DOM at all.
    assert "if (ordered[i] !== want)" in group


def test_approvals_do_not_yank_the_reader(chat_js: str) -> None:
    """Plan §11: "Approvals must not pull a reader away from inspected
    content" — only auto-follow when already following."""
    assert "function _isNearBottom()" in chat_js
    assert "_userPinnedToBottom" in chat_js
    scroll = _block(chat_js, "function scrollToBottom()", "\n  function ")
    assert "_userPinnedToBottom" in scroll, (
        "scrollToBottom no longer checks whether the reader was following"
    )
    # A claimed card must not bounce the chat; only a live question may.
    reveal = _block(chat_js, "function _revealHitlCard(card)", "\n  function ")
    assert "_hitlCardIsClaimed" in reveal or "claimed" in reveal, (
        "reveal bounces the reader for a settled card"
    )


# ══════════════════════════════════════════════════════════════════════════
# Presentation
# ══════════════════════════════════════════════════════════════════════════


def test_reduced_motion_stops_the_header_pulse(css: str) -> None:
    """The only motion the header has, and it is opt-out."""
    block = css.split(".turn-header.is-working .turn-header-phase,", 1)[1]
    assert "animation: kz-turn-pulse" in block
    reduced = css.split("@media (prefers-reduced-motion: reduce) {", 1)
    assert len(reduced) > 1
    assert any(
        "turn-header-phase" in chunk and "animation: none" in chunk
        for chunk in css.split("@media (prefers-reduced-motion: reduce) {")[1:]
    ), "the header pulse ignores prefers-reduced-motion"


def test_the_turn_block_is_readable_on_a_phone(css: str) -> None:
    """Both new regions have a narrow-viewport rule."""
    narrow = "".join(
        chunk.split("}\n}", 1)[0]
        for chunk in css.split("@media (max-width: 640px) {")[1:]
    )
    assert ".turn-header" in narrow, "the header has no mobile rule"
    assert ".turn-approvals" in narrow, "the approval group has no mobile rule"


def test_the_clock_does_not_jitter(css: str) -> None:
    """A proportional font makes a per-second counter dance."""
    meta = css.split(".turn-header-meta {", 1)[1].split("}", 1)[0]
    assert "tabular-nums" in meta


def test_the_answer_is_never_inside_a_collapsible_region(chat_js: str) -> None:
    """Plan §5: the answer is a sibling of the activity and approval
    regions, never their descendant.

    This is the structural half of "collapsing thoughts never hides the
    answer" — with the answer outside every collapsible region, the
    failure is unrepresentable rather than guarded.
    """
    view = (
        UI / "static" / "js" / "modules" / "turn_view.js"
    ).read_text(encoding="utf-8")
    plan = _block(view, "function slotPlan(doc, has, TD, gateState)",
                  "\n  function create(")
    # Every region is pushed onto ONE flat list.
    for region in ("kind: 'header'", "kind: 'workbench'", "kind: 'approvals'",
                   "kind: 'text'"):
        assert region in plan, region
    assert "plan.push(" in plan
    # And the renderer inserts them as direct children of the content host.
    render = _block(view, "// 3. One ordering pass.", "// 4. Chrome last")
    assert "content.insertBefore(node, want || null)" in render
