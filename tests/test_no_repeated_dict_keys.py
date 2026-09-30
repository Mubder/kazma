"""No dict literal names the same key twice.

Python keeps the LAST value of a repeated key and says nothing. On 2026-09-30
the Settings catalog defined the four provider capability labels twice (a
2026-09-13 set and a 2026-09-28 set with different Arabic): the first set
could never show, and a fix made to it would have changed nothing. Ruff calls
this F601, but CI's lint step is advisory, so this is the gate.

Every Python file the repository tracks (and untracked, not ignored ones),
product and tests alike: a repeated key in a test's table silently drops a
case.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def repeated_keys(source: str) -> list[tuple[int, object]]:
    """(line, key) for every constant key a dict literal repeats."""
    found: list[tuple[int, object]] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Dict):
            continue
        seen: set[object] = set()
        for key in node.keys:
            # ``**spread`` has no key; computed keys cannot be judged statically.
            if not isinstance(key, ast.Constant):
                continue
            # The value itself, as the dict compares it: 1, 1.0 and True are
            # one key; 1 and "1" are two.
            if key.value in seen:
                found.append((key.lineno, key.value))
            seen.add(key.value)
    return found


def _python_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "*.py"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    ).stdout.split()
    return [REPO_ROOT / rel for rel in out if (REPO_ROOT / rel).is_file()]


def test_no_dict_literal_repeats_a_key() -> None:
    files = _python_files()
    assert len(files) > 500, "the file listing is broken"
    problems = []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
            hits = repeated_keys(text)
        except (SyntaxError, UnicodeDecodeError):
            continue
        rel = path.relative_to(REPO_ROOT).as_posix()
        problems.extend(f"{rel}:{line} {key!r}" for line, key in hits)
    assert not problems, (
        "A dict literal repeats a key; Python keeps the last value and drops "
        "the first without a word. Keep one:\n  " + "\n  ".join(problems)
    )


def test_negative_control_a_repeated_key_is_found() -> None:
    source = 'CATALOG = {\n    "a": 1,\n    "b": 2,\n    "a": 3,\n}\n'
    assert repeated_keys(source) == [(4, "a")]
    # Equal keys of different types collide in a dict, so they count.
    assert repeated_keys("X = {1: 'a', True: 'b'}\n") == [(1, True)]


def test_what_is_not_a_repeat() -> None:
    # 1 and "1" are different keys; spreads and computed keys are not judged.
    source = 'X = {1: "a", "1": "b", **base, **other, f(): 1, f(): 2}\n'
    assert repeated_keys(source) == []
