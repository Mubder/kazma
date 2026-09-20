"""Shared unified-turn fixtures — Python side, plus the cross-language diff.

``docs/plans/UNIFIED_TURN_BLOCK.md`` §6:

    Use shared JSON fixtures to prove Python normalization/serialization and
    JavaScript projection agree. Do not rely on two implementations with
    independently written examples.

Two independently written example sets is how the drift below survived:
``tests/test_turn_document.py`` and ``tests/js/test_turn_document.js`` were
both green while ``legacy_turn_id`` minted a different id in each language.
This module reads the same JSON files as ``tests/js/test_unified_turn_fixtures.js``
and then compares the two projections field by field.

Recorded divergences are listed in each fixture's ``known_divergences`` and
in ``docs/plans/UNIFIED_TURN_BLOCK_PHASE0.md`` §6. They are excluded from the
agreement comparison and asserted separately by
:func:`test_recorded_divergence_still_diverges`, which is ``xfail(strict=True)``:
when Phase 1 aligns the two implementations that test passes unexpectedly, the
suite goes red, and the stale record has to be deleted. A divergence cannot be
fixed quietly and it cannot be forgotten.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "unified_turn" / "messages"
JS_DRIVER = ROOT / "tests" / "js" / "test_unified_turn_fixtures.js"


def _fixtures() -> list[dict[str, Any]]:
    out = []
    for path in sorted(FIXTURES.glob("*.json")):
        out.append(json.loads(path.read_text(encoding="utf-8")))
    assert out, f"no fixtures under {FIXTURES}"
    return out


def _project(message: dict[str, Any]) -> dict[str, Any]:
    """The Python projection of one stored assistant row.

    Mirrors ``project()`` in the JavaScript driver. The part key is joined
    with ``":"`` because Python returns a tuple where JavaScript returns the
    equivalent string — see ``tests/fixtures/unified_turn/README.md``.
    """
    from kazma_ui.turn_document import (
        _part_key,
        activity_of,
        hydrate_message,
        text_of,
    )

    hydrated = hydrate_message(message)
    parts = hydrated.get("parts") or []
    return {
        "turn_id": str(hydrated.get("turn_id") or ""),
        "text": text_of(parts),
        "part_keys": [":".join(str(x) for x in _part_key(p)) for p in parts],
        "gates": [
            {
                "interrupt_id": str(p.get("interrupt_id") or ""),
                "tool": str(p.get("tool") or ""),
                "state": str(p.get("state") or ""),
            }
            for p in parts
            if p.get("type") == "hitl"
        ],
        "activity": activity_of(parts),
    }


def _dense(obj: Any) -> Any:
    """Drop null values so the comparison is about content, not about how
    each language spells "absent"."""
    if isinstance(obj, list):
        return [_dense(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _dense(v) for k, v in obj.items() if v is not None}
    return obj


def _js_projections() -> dict[str, Any]:
    node = shutil.which("node")
    if not node:  # pragma: no cover - CI always has node
        pytest.skip("node not available")
    assert JS_DRIVER.is_file(), JS_DRIVER
    proc = subprocess.run(
        [node, str(JS_DRIVER), "--emit"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return json.loads(proc.stdout)


@pytest.mark.parametrize("fixture", _fixtures(), ids=lambda f: f["name"])
def test_python_projection_matches_fixture(fixture: dict[str, Any]) -> None:
    """The Python normalizer produces what the shared fixture declares."""
    got = _dense(_project(fixture["message"]))
    diverged = {d["field"] for d in fixture.get("known_divergences", [])}
    for field, want in fixture["expect"].items():
        if field in diverged:
            continue
        assert got[field] == _dense(want), (
            f"{fixture['name']}.{field}: Python projection does not match "
            f"the shared fixture"
        )


def test_javascript_driver_self_check() -> None:
    """The JavaScript side agrees with the same files, under bare node."""
    node = shutil.which("node")
    if not node:  # pragma: no cover - CI always has node
        pytest.skip("node not available")
    proc = subprocess.run(
        [node, str(JS_DRIVER)], capture_output=True, text=True, timeout=120
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


@pytest.mark.parametrize("fixture", _fixtures(), ids=lambda f: f["name"])
def test_python_and_javascript_agree(fixture: dict[str, Any]) -> None:
    """The two implementations project the same row the same way.

    This is the test the drift needed. A field listed in the fixture's
    ``known_divergences`` is excluded here and owned by
    :func:`test_recorded_divergence_still_diverges` instead — excluding it
    silently would be the same mistake one layer down.
    """
    js_all = _js_projections()
    name = fixture["name"]
    assert name in js_all, f"JavaScript driver did not project {name}"
    py = _dense(_project(fixture["message"]))
    js = _dense(js_all[name])
    diverged = {d["field"] for d in fixture.get("known_divergences", [])}
    for field in sorted(set(py) | set(js)):
        if field in diverged:
            continue
        assert py.get(field) == js.get(field), (
            f"{name}.{field}: Python and JavaScript disagree\n"
            f"  python:     {py.get(field)!r}\n"
            f"  javascript: {js.get(field)!r}"
        )


_DIVERGENCES = [
    (fx["name"], d)
    for fx in _fixtures()
    for d in fx.get("known_divergences", [])
]


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Recorded Phase 0 divergence. UNIFIED_TURN_BLOCK.md Phase 1 aligns "
        "these; when it does this xfail flips to XPASS and the record must "
        "be deleted from the fixture. It is a removal obligation, never "
        "acceptance evidence."
    ),
)
@pytest.mark.parametrize(
    ("name", "divergence"), _DIVERGENCES, ids=[n for n, _ in _DIVERGENCES]
)
def test_recorded_divergence_still_diverges(
    name: str, divergence: dict[str, Any]
) -> None:
    """Assert the aligned behavior the recorded divergence currently denies."""
    fixture = next(f for f in _fixtures() if f["name"] == name)
    py = _project(fixture["message"])
    js = _js_projections()[name]
    field = divergence["field"]
    assert py.get(field) == js.get(field), (
        f"{name}.{field} still diverges ({divergence['cause']})"
    )


def test_layout_fixture_is_the_agreed_scenario() -> None:
    """The agreed layout fixture states the four-gate scenario unambiguously.

    Every layer of the plan — harness, acceptance matrix, convergence oracle,
    browser evidence — points at this one file, so "four requests, one group"
    cannot come to mean four different things in four suites.
    """
    path = (
        ROOT / "tests" / "fixtures" / "unified_turn" / "layout"
        / "four_sequential_gates.json"
    )
    fx = json.loads(path.read_text(encoding="utf-8"))
    assert len(fx["script"]) == 4
    assert len({step["tool"] for step in fx["script"]}) == 3, (
        "the fixture must repeat one tool, or it does not exercise "
        "'repeated identical tool, distinct gate ids'"
    )
    assert [s["decision"] for s in fx["script"]].count("deny") == 1, (
        "one denial is required: denial must not end the turn"
    )
    layout = fx["expect_layout"]
    assert layout["turn_blocks"] == 1
    assert layout["approval_groups"] == 1
    assert layout["approval_rows"] == 4
    assert layout["answer_regions"] == 1
    assert layout["bottom_status_bars"] == 0
    assert layout["activity_collapsed_by_default"] is True
    assert len(fx["expect_rows"]) == 4
    denied = [r for r in fx["expect_rows"] if r["decision"] == "Denied"]
    assert denied and denied[0]["execution"] == "Not run", (
        "decision and execution are separate columns (plan §3)"
    )
