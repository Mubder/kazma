"""The tool registry the matrix shares is the same for every test.

``tests._chat_space_cases._registry()`` builds the product's tool registry
once per process and gives every test the same object. On fae123ffd,
``tests/test_background_uses_creator_policy.py`` wrote a stand-in
``web_search`` straight into its ``_tools`` and never took it out; in the
fast_test chunk that held both files, the space matrix's ``tool:web_search``
case ran 320 files later, called the stand-in, and found no search recorded
(``AssertionError: {"ok":true,"sessions":[],"count":0}``). Each passed alone.

Two fixes, each shown here:

* the creator-policy test puts its stand-in in through its monkeypatch: run
  before the matrix case in one process, with the root conftest's guard off,
  the case still passes;
* the root conftest (``_helper_tool_registry_put_back``) puts the registry
  back after every test and fails the test that left it changed, naming what
  it changed: a planted old-shape write fails its own test and never reaches
  the matrix case. Without the guard (``KAZMA_TEST_ISOLATION=0``) the same
  pair fails the matrix case as CI's chunk did: the negative control.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from tests._chat_space_cases import _registry, put_back_registry, registry_state

REPO = Path(__file__).resolve().parents[1]
HERE = "tests/test_shared_registry_isolation.py"
VICTIM = "tests/test_space_matrix.py::test_case[tool:web_search]"
_PROBE = "KAZMA_SHARED_REGISTRY_PROBE"


def _child_pytest(*node_ids: str, **env_extra: str) -> subprocess.CompletedProcess:
    env = {**os.environ, **env_extra}
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-rA", "-p", "no:cacheprovider", *node_ids],
        cwd=str(REPO), env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=600,
    )


@pytest.mark.skipif(os.environ.get(_PROBE) != "1", reason="run by the child pytest of this file only")
def test_probe_writes_a_stand_in_straight_into_the_shared_registry():
    """The shape the creator-policy test had: written in, never taken out."""
    registry = _registry()

    async def stand_in(*_a, **_k):
        return "results"

    registry._tools["web_search"] = replace(registry.get_tool("web_search"), func=stand_in, is_async=True)


# ── in process ─────────────────────────────────────────────────────────────


def test_the_guard_is_set_up_before_monkeypatch(request, monkeypatch):
    """Its teardown runs after monkeypatch's undo, so a change made through
    monkeypatch is gone before the guard looks, and is never called a leak."""
    names = request.fixturenames
    assert names.index("_helper_tool_registry_put_back") < names.index("monkeypatch"), names


def test_a_change_through_monkeypatch_is_not_a_leak(monkeypatch):
    registry = _registry()
    monkeypatch.setitem(registry._tools, "web_search", replace(registry.get_tool("web_search"), description="x"))
    assert registry.get_tool("web_search").description == "x"


def test_put_back_names_and_undoes_every_kind_of_change():
    registry = _registry()
    before = registry_state()
    assert put_back_registry(before) == []
    tools = registry._tools
    name, other, third = sorted(tools)[:3]
    original, kept = tools[name], tools[third]
    kept_skill = kept.skill_id
    tools[name] = replace(original, description="replaced")
    del tools[other]
    tools["planted_tool"] = original
    kept.skill_id = "native:planted"
    registry.planted_attribute = True
    left = put_back_registry(before)
    assert set(left) == {f"{name} (replaced)", f"{other} (removed)", "planted_tool (added)",
                         f"{third} (changed in place)", "the registry's attributes"}, left
    assert registry._tools is tools and tools[name] is original and other in tools
    assert "planted_tool" not in tools and kept.skill_id == kept_skill
    assert not hasattr(registry, "planted_attribute")
    assert put_back_registry(before) == []


def test_a_replaced_tool_table_is_put_back_by_identity():
    registry = _registry()
    before = registry_state()
    table = registry._tools
    registry._tools = {}
    assert put_back_registry(before) == ["the registry's attributes"]
    assert registry._tools is table and table


# ── in one process, the leaking shape before the case it broke ────────────


def test_the_creator_policy_tests_leave_the_registry_as_they_found_it():
    """The fixed source, with the guard off: the matrix case still passes."""
    proc = _child_pytest("tests/test_background_uses_creator_policy.py", VICTIM, KAZMA_TEST_ISOLATION="0")
    assert proc.returncode == 0, proc.stdout[-3000:]
    assert f"PASSED {VICTIM}" in proc.stdout, proc.stdout[-3000:]


def test_a_left_change_fails_its_own_test_and_never_reaches_the_next():
    proc = _child_pytest(f"{HERE}::test_probe_writes_a_stand_in_straight_into_the_shared_registry", VICTIM,
                         **{_PROBE: "1"})
    assert f"PASSED {VICTIM}" in proc.stdout, proc.stdout[-3000:]
    assert ("ERROR tests/test_shared_registry_isolation.py::test_probe_writes_a_stand_in_straight_into_the_"
            "shared_registry") in proc.stdout, proc.stdout[-3000:]
    assert "left the shared tool registry" in proc.stdout and "web_search (replaced)" in proc.stdout


def test_without_the_guard_the_change_reaches_the_next_test():
    """Negative control: the same pair with test isolation off fails the
    matrix case the way CI's chunk did."""
    proc = _child_pytest(f"{HERE}::test_probe_writes_a_stand_in_straight_into_the_shared_registry", VICTIM,
                         KAZMA_TEST_ISOLATION="0", **{_PROBE: "1"})
    assert proc.returncode == 1, proc.stdout[-3000:]
    assert f"FAILED {VICTIM}" in proc.stdout, proc.stdout[-3000:]
    assert '{"ok":true,"sessions":[],"count":0}' in proc.stdout, proc.stdout[-3000:]
