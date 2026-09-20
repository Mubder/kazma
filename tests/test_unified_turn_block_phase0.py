"""Phase 0 reproductions for ``docs/plans/UNIFIED_TURN_BLOCK.md``.

Phase 0 asks for "reproduction of the current split bar, separate gate
cards, and forced-open thoughts" and exits when "tests fail for the actual
missing behaviors, not fixture/setup errors".

Every test here asserts the TARGET behavior, so Phases 2 and 3 land by
deleting a marker rather than by rewriting an assertion. They are
``xfail(strict=True)``: red today, and the moment the behavior lands the
suite goes red again until the marker is removed. That is the honest way to
carry a known-unmet requirement on ``main`` — an unconditionally failing
suite would block every unrelated change, and a plain skip would let the
requirement rot.

``UNIFIED_TURN_BLOCK.md`` §13 forbids treating xfail as acceptance. No
acceptance claim is made here; see ``UNIFIED_TURN_BLOCK_PHASE0.md`` §7.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "kazma-ui" / "kazma_ui"
CHAT_JS = UI / "static" / "js" / "chat.js"
CHAT_HTML = UI / "templates" / "chat.html"
JS_REPRO = ROOT / "tests" / "js" / "test_unified_turn_block_phase0.js"

XFAIL_PHASE2 = pytest.mark.xfail(
    strict=True,
    reason=(
        "UNIFIED_TURN_BLOCK.md Phase 2 (unified renderer and header). "
        "Remove this marker with the change that lands the behavior."
    ),
)
XFAIL_PHASE3 = pytest.mark.xfail(
    strict=True,
    reason=(
        "UNIFIED_TURN_BLOCK.md Phase 3 (grouped approval integration). "
        "Remove this marker with the change that lands the behavior."
    ),
)


def _chat_js() -> str:
    return CHAT_JS.read_text(encoding="utf-8")


def _chat_html() -> str:
    return CHAT_HTML.read_text(encoding="utf-8")


# ══════════════════════════════════════════════════════════════════════════
# The split bar — a second status surface outside the turn block
# ══════════════════════════════════════════════════════════════════════════


def test_no_separate_live_task_card_markup() -> None:
    """Plan §3: "No second fixed or floating status bar."

    ``#live-task-card`` owns phase, elapsed, step count and Stop from
    outside any turn — the same four facts the unified header owns. Two
    surfaces for one turn is the defect, not the styling.
    """
    html = _chat_html()
    assert 'id="live-task-card"' not in html, (
        "the separate live task card is still in the chat template"
    )
    assert 'id="thinking-indicator"' not in html, (
        "the legacy hidden thinking indicator is still in the chat template"
    )


def test_no_independent_task_card_controller() -> None:
    """Plan §9: the task-card controller loses independent phase/content
    ownership; its commands move onto the turn header."""
    src = _chat_js()
    assert "LIVE_TASK_CARD_BEGIN" not in src, (
        "the independent live-task-card state machine is still in chat.js"
    )
    for fn in ("_tcSetPhase", "_tcRender", "_tcTick", "_tcStepsFromDoc"):
        assert f"function {fn}(" not in src, (
            f"{fn} is a second turn-phase authority outside the renderer"
        )


def test_no_live_task_card_styles() -> None:
    """Plan §9: "Old CSS/selectors/imports/localization/tests — remove or
    update alongside the owning change." """
    css = (UI / "static" / "css" / "kazma.css").read_text(encoding="utf-8")
    assert ".live-task-card" not in css, (
        "live-task-card CSS outlived its markup"
    )


# ══════════════════════════════════════════════════════════════════════════
# Forced-open thoughts — event processing overriding a user preference
# ══════════════════════════════════════════════════════════════════════════


def test_live_paint_does_not_reopen_collapsed_thoughts() -> None:
    """Invariant U08: "Event processing never changes disclosure
    preferences."

    ``_paintWorkbenchSlot`` used to remove ``is-collapsed`` and force
    ``aria-expanded="true"`` on every live pass, so a reader who folded the
    panel had it reopened by the next token. Commit ``afbd22dd`` made that
    deliberate after the opposite bug — collapsing at the terminal frame
    yanked the answer out of view — and the two kept trading places because
    neither separated "what the reader asked for" from "what the turn is
    doing".

    Landed in Phase 2: the fold is read from ``turn_preferences.js`` and
    written only by a reader gesture. Behavioral coverage, including that
    50 repaints move nothing, is ``tests/js/test_turn_preferences.js``.
    """
    src = _chat_js()
    start = src.index("function _paintWorkbenchSlot(")
    end = src.index("var _turnRenderers = {", start)
    body = src[start:end]
    assert "remove('is-collapsed'" not in body.replace('"', "'"), (
        "the live workbench painter still force-opens the fold"
    )
    assert "'aria-expanded', 'true'" not in body.replace('"', "'"), (
        "the live workbench painter still forces aria-expanded=true"
    )


def test_expansion_preference_has_an_owner() -> None:
    """Plan §5: expansion state is owned by a preference store the renderer
    reads — not recomputed from execution state on each paint."""
    owner = UI / "static" / "js" / "modules" / "turn_preferences.js"
    assert owner.is_file(), (
        "no module owns turn disclosure preferences; expansion is currently "
        "recomputed by _paintWorkbenchSlot from execution state "
        "(turn_visibility.js is the hidden-tab title badge, not this)"
    )
    src = owner.read_text(encoding="utf-8")
    low = src.lower()
    assert "expand" in low or "collapse" in low, (
        "the preference owner does not model expansion at all"
    )
    # Plan §3: "Preferences never travel as execution facts."
    assert "fetch(" not in src and "XMLHttpRequest" not in src, (
        "the preference store talks to the server; a preference that rides "
        "on an execution record can be replayed onto someone else's screen"
    )
    # A module nobody loads protects nothing.
    chat_html = _chat_html()
    assert "modules/turn_preferences.js" in chat_html, (
        "turn_preferences.js is not loaded by the chat page"
    )
    chat = _chat_js()
    assert "_toggleActivityFold" in chat and "_applyActivityFold" in chat, (
        "chat.js does not route the fold through the preference store"
    )


# ══════════════════════════════════════════════════════════════════════════
# Separate gate cards — the renderer layer, driven under node
# ══════════════════════════════════════════════════════════════════════════


def _js_repro_results() -> dict[str, dict[str, object]]:
    node = shutil.which("node")
    if not node:  # pragma: no cover - CI always has node
        pytest.skip("node not available")
    assert JS_REPRO.is_file(), JS_REPRO
    proc = subprocess.run(
        [node, str(JS_REPRO), "--json"],
        capture_output=True,
        # Explicit UTF-8 — see tests/test_unified_turn_fixtures.py.
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return json.loads(proc.stdout)


_JS_CHECKS = [
    "approvals_is_one_region",
    "four_gates_four_rows_in_ask_order",
    "approvals_stay_above_the_answer",
    "pending_and_settled_share_one_group",
]


@XFAIL_PHASE3
@pytest.mark.parametrize("check", _JS_CHECKS)
def test_renderer_groups_gates_into_one_region(check: str) -> None:
    """``slotPlan`` emits one keyed approval region, not one slot per gate.

    Tracked one behavior at a time so four unmet requirements do not
    collapse into one red dot and get fixed three-quarters of the way.
    """
    result = _js_repro_results()[check]
    assert result["ok"], result["error"]


def test_js_reproduction_covers_the_checks_this_module_names() -> None:
    """A renamed or deleted JS check must not silently stop being tracked."""
    got = set(_js_repro_results())
    missing = set(_JS_CHECKS) - got
    assert not missing, f"JS reproduction no longer defines: {sorted(missing)}"


def test_empty_turn_mints_no_approval_group() -> None:
    """The one target behavior that already holds — locked so the Phase 3
    rewrite cannot regress it while satisfying the other four."""
    result = _js_repro_results()["no_gates_no_group"]
    assert result["ok"], result["error"]


# ══════════════════════════════════════════════════════════════════════════
# tokenAccum — the second text authority
# ══════════════════════════════════════════════════════════════════════════


@XFAIL_PHASE2
def test_no_token_accum_content_decisions() -> None:
    """Plan §9: "``tokenAccum`` and related fallback reads — replace content
    decisions with document selectors."

    AGENTS.md §31B already forbids restoring the ``tokenAccum`` dual-paint.
    What remains is subtler: ``tokenAccum`` is still READ to decide whether
    the turn produced visible content, which is a second answer to a
    question the document already answers.
    """
    src = _chat_js()
    assert "tokenAccum" not in src, (
        "chat.js still carries a second text authority; every read is a "
        "content decision the TurnDocument should be making"
    )


# ══════════════════════════════════════════════════════════════════════════
# Baseline locks — keep the Phase 0 report honest
# ══════════════════════════════════════════════════════════════════════════


def test_phase0_report_exists_and_names_its_evidence() -> None:
    """The report is a deliverable, not a note. If it stops naming the
    modules it inventories, it has stopped being an inventory."""
    report = (ROOT / "docs" / "plans" / "UNIFIED_TURN_BLOCK_PHASE0.md").read_text(
        encoding="utf-8"
    )
    for token in (
        "turn_view.js",
        "turn_document.js",
        "turn_runtime.py",
        "delivery.py",
        "gate_view.py",
        "_paintWorkbenchSlot",
        "tokenAccum",
        "live-task-card",
    ):
        assert token in report, f"Phase 0 report no longer names {token}"


def test_superseded_plans_carry_a_notice() -> None:
    """Plan §2: "Do not leave multiple documents marked binding with
    contradictory layout rules." """
    plans = ROOT / "docs" / "plans"
    for name in (
        "COT_AND_THOUGHTS.md",
        "TURN_RENDER_V2_KEYED_SLOTS.md",
        "HITL_VIEW_MODEL.md",
    ):
        text = (plans / name).read_text(encoding="utf-8")
        assert "SUPERSEDED IN PART" in text, f"{name} has no supersession notice"
        assert "UNIFIED_TURN_BLOCK.md" in text, (
            f"{name} does not say what supersedes it"
        )


# ══════════════════════════════════════════════════════════════════════════
# Phase 2 — the fold follows the reader (driven under node)
# ══════════════════════════════════════════════════════════════════════════


def test_disclosure_preference_behaviors_under_node() -> None:
    """Drive the store and the two chat.js fold helpers on a fake DOM.

    Substring assertions pass happily while the fold still snaps back on
    the next token, which is the bug the whole module exists to end — and
    which has been "fixed" in both directions twice already. The node suite
    re-renders 50 times in every execution state and asserts nothing moves.
    """
    node = shutil.which("node")
    if not node:  # pragma: no cover - CI always has node
        pytest.skip("node not available")
    script = ROOT / "tests" / "js" / "test_turn_preferences.js"
    assert script.is_file()
    proc = subprocess.run(
        [node, str(script)],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_header_model_behaviors_under_node() -> None:
    """The derived header model, driven on real documents.

    Plan §7 requires the header to be a MAPPING from server facts, not a
    second execution state machine. The tests that matter are the refusals:
    a stop request is not a cancellation, a dropped socket is not an
    outcome, a stale pending stamp does not outrank the registry's view,
    and elapsed comes from the server or not at all.
    """
    node = shutil.which("node")
    if not node:  # pragma: no cover - CI always has node
        pytest.skip("node not available")
    script = ROOT / "tests" / "js" / "test_turn_presentation.js"
    assert script.is_file()
    proc = subprocess.run(
        [node, str(script)],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_the_turn_header_is_inside_the_turn() -> None:
    """Plan §3: the header lives in the assistant block, not beside it.

    A header rendered outside the turn is what ``#live-task-card`` was —
    one status surface per PAGE instead of one per turn, which is why it
    needed its own state machine, its own clock and its own idea of when a
    turn was over.
    """
    view = (
        UI / "static" / "js" / "modules" / "turn_view.js"
    ).read_text(encoding="utf-8")
    plan = view.split("function slotPlan(", 1)[1].split("function create(", 1)[0]
    assert "kind: 'header'" in plan, "the renderer plans no header slot"
    assert plan.index("kind: 'header'") < plan.index("kind: 'workbench'"), (
        "the header is not the first slot"
    )
    assert "'header'" in view.split("function adopt(", 1)[1][:1200], (
        "the renderer cannot adopt a header it did not create, so history "
        "hydration would mint a second one"
    )

    chat = _chat_js()
    assert "function _buildTurnHeader(" in chat
    assert "function _paintTurnHeader(" in chat
    assert "KazmaTurnPresentation" in chat, (
        "chat.js derives the header itself instead of asking the one model"
    )
    assert "modules/turn_presentation.js" in _chat_html(), (
        "the presentation model is not loaded by the chat page"
    )
