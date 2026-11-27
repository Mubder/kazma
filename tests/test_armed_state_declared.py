"""Every change of the second lock's armed state in the tests is declared (S4-01 step 5 review L3).

A test that disarms the lock runs unarmed in the armed run too, so what it
proves is the unarmed reading. That is right for a test of how an install with
Spaces off reads today, or of the lock itself -- and a hidden disarm is how a
test quietly stops proving the armed reading. So every disarm or arm goes
through ``tests._as_request.not_armed`` / ``lock_armed``, which one search
finds, and only three files arm or disarm by hand:

* ``tests/_as_request.py`` -- the two helpers themselves;
* ``tests/_second_lock_armed.py`` -- the armed run's plugin (``_arm`` at the
  session and each module);
* ``tests/_sweeps_armed_child.py`` -- a child process of the sweeps
  comparison, which arms its own interpreter.

The gate reads every git-listed test file and ``conftest.py``; the planted
controls show it finds each form.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

#: The files that may change the armed state by hand, and why.
BY_HAND = {
    "tests/_as_request.py": "not_armed / lock_armed: the declared way",
    "tests/_second_lock_armed.py": "the armed run's plugin arms the session and each module",
    "tests/_sweeps_armed_child.py": "a child process of the sweeps comparison arms its own interpreter",
}


def _changes_of_the_armed_state(source: str) -> list[int]:
    """Line numbers where *source* sets ``_armed`` or calls ``_arm``."""
    found: list[int] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name == "_arm":
                found.append(node.lineno)
            elif name == "setattr" and node.args:
                texts = [a.value for a in node.args[:2] if isinstance(a, ast.Constant) and isinstance(a.value, str)]
                if any(t == "_armed" or t.endswith("._armed") for t in texts):
                    found.append(node.lineno)
        elif isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(t, ast.Attribute) and t.attr == "_armed" for t in targets):
                found.append(node.lineno)
    return sorted(found)


def _test_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "--", "tests/*.py", "tests/**/*.py", "conftest.py"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout
    return sorted({line.strip() for line in out.splitlines() if line.strip()})


def test_every_change_of_the_armed_state_is_declared() -> None:
    files = _test_files()
    assert "tests/test_pg_space_pool.py" in files, "the gate must read the git-listed tests"
    undeclared = []
    for rel in files:
        if rel in BY_HAND:
            continue
        path = ROOT / rel
        if not path.exists():
            continue
        lines = _changes_of_the_armed_state(path.read_text(encoding="utf-8"))
        undeclared += [f"{rel}:{n}" for n in lines]
    assert not undeclared, (
        "the second lock armed or disarmed by hand -- use tests._as_request.not_armed(monkeypatch) "
        "or lock_armed(monkeypatch, value), so one search finds every test that does not prove "
        f"the armed reading: {undeclared}"
    )


def test_the_files_allowed_by_hand_still_do_it() -> None:
    """A stale entry would let a later hand-made change through unseen."""
    for rel, why in BY_HAND.items():
        path = ROOT / rel
        assert path.exists(), f"{rel} is gone: drop it from BY_HAND ({why})"
        assert _changes_of_the_armed_state(path.read_text(encoding="utf-8")), (
            f"{rel} no longer changes the armed state: drop it from BY_HAND ({why})"
        )


@pytest.mark.parametrize(
    "planted",
    [
        'monkeypatch.setattr(row_security, "_armed", False)',
        "mp.setattr(lock, '_armed', True)",
        'monkeypatch.setattr("kazma_core.db.row_security._armed", False)',
        "row_security._armed = True",
        "lock._armed: bool = False",
        "row_security._arm(False)",
        'setattr(row_security, "_armed", True)',
    ],
)
def test_the_gate_finds_a_planted_change(planted: str) -> None:
    assert _changes_of_the_armed_state(f"x = 1\n{planted}\n") == [2]


def test_the_gate_leaves_the_declared_helpers_and_reads_alone() -> None:
    source = (
        "not_armed(monkeypatch)\n"
        "lock_armed(monkeypatch, True)\n"
        "if row_security.armed() or row_security._armed:\n"
        "    pass\n"
        "def test_x_armed(armed): ...\n"
    )
    assert _changes_of_the_armed_state(source) == []
