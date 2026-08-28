"""The manifest has to bite, or it is just a nicer-looking comment.

The repetition breaker was documented, wired, unit-tested and green while
being incapable of firing. Documentation did not catch that and review did
not catch that. What catches it is a build that fails when the code a
claim points at, or the test that proves it, stops existing.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from kazma_core.observability.resilience_manifest import MECHANISMS, unproven

_ROOT = Path(__file__).resolve().parents[1]


def _ids(ms):
    return [m.name for m in ms]


@pytest.mark.parametrize("m", MECHANISMS, ids=_ids(MECHANISMS))
def test_the_code_a_claim_points_at_exists(m):
    """A mechanism whose implementation was renamed or deleted is a claim
    the system no longer backs."""
    if m.module.endswith(".py"):
        path = _ROOT / m.module
        assert path.exists(), f"{m.name}: {m.module} is gone"
        assert m.symbol in path.read_text(encoding="utf-8"), (
            f"{m.name}: {m.symbol} no longer in {m.module}"
        )
        return
    mod = importlib.import_module(m.module)
    assert hasattr(mod, m.symbol), f"{m.name}: {m.module}.{m.symbol} is gone"


@pytest.mark.parametrize("m", MECHANISMS, ids=_ids(MECHANISMS))
def test_every_claim_has_a_live_proof(m):
    """Deleting the test that keeps a mechanism honest must be a red build,
    not a silent loss of coverage."""
    proof = _ROOT / m.proof
    assert proof.exists(), f"{m.name}: proof {m.proof} is missing"
    body = proof.read_text(encoding="utf-8")
    assert "def test_" in body, f"{m.name}: {m.proof} contains no tests"


def test_no_mechanism_is_left_without_a_proof():
    missing = [m.name for m in MECHANISMS if not m.proof.strip()]
    assert not missing, f"mechanisms with no proof at all: {missing}"


def test_names_are_unique():
    names = [m.name for m in MECHANISMS]
    assert len(names) == len(set(names))


def test_the_loop_breaker_proof_drives_the_supervisor():
    """The specific lesson of 2026-08-28.

    detect_tool_loop was correct the whole time. The supervisor could not
    reach it. A proof that exercises the detector would have stayed green
    through the entire outage window, so this entry's proof must drive
    supervisor_node itself.
    """
    from kazma_core.observability.resilience_manifest import by_name

    m = by_name("repetition loop breaker")
    body = (_ROOT / m.proof).read_text(encoding="utf-8")
    assert "supervisor_node" in body, (
        "the loop-breaker proof must drive the supervisor, not the detector"
    )


def test_unproven_mechanisms_are_reported_honestly():
    """This count is only useful while it stays honest. If it ever reads
    zero, that should be because faults were observed and recovered -- not
    because someone flipped the flags."""
    names = {m.name for m in unproven()}
    assert names, "a manifest where everything is proven deserves suspicion"
    for m in unproven():
        assert m.proof, f"{m.name} is unproven AND unproved -- pick one"
