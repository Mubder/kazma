"""Debt that cannot grow: counts that may only go down.

The 2026-09-22 audit found 3,846 ``except Exception``/bare ``except`` handlers
in product code, 577 of them with a body of just ``pass``. The one place that
most needed a guard (the supervisor's snapshot capture) had none, while
thousands of calls that did not need one swallowed everything. Nobody is going
to rewrite 3,846 handlers in one change, and a gate that is red on day one
gets ignored (docs/KNOWN_GAPS.md). So this is a ratchet:

* a count ABOVE the baseline fails — new debt, fix it or justify it by
  raising the number in review, where someone has to look at it;
* a count BELOW the baseline also fails, with the new number — lock the gain
  in by lowering the baseline in the same change.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Lower these whenever the counts drop. Never raise them casually.
BASELINE = {
    # except Exception / except BaseException / bare except, any body
    "blind_except": 3845,
    # ...whose body is only `pass` (or a docstring): the error vanishes
    "silent_except": 574,
}


def _is_blind(handler: ast.ExceptHandler) -> bool:
    t = handler.type
    return t is None or (isinstance(t, ast.Name) and t.id in ("Exception", "BaseException"))


def _is_silent(handler: ast.ExceptHandler) -> bool:
    return all(
        isinstance(s, ast.Pass)
        or (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))
        for s in handler.body
    )


def debt_counts(sources: list[str]) -> dict[str, int]:
    counts = {"blind_except": 0, "silent_except": 0}
    for text in sources:
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler) and _is_blind(node):
                counts["blind_except"] += 1
                if _is_silent(node):
                    counts["silent_except"] += 1
    return counts


def _product_sources() -> list[str]:
    files = subprocess.run(
        ["git", "ls-files", "kazma-*/*.py", "kazma-*/**/*.py"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    ).stdout.split()
    return [
        (REPO_ROOT / f).read_text(encoding="utf-8", errors="replace")
        for f in files
        if "_tests" not in f and "/tests/" not in f and (REPO_ROOT / f).is_file()
    ]


def test_exception_debt_only_goes_down():
    counts = debt_counts(_product_sources())
    grew = {k: (v, BASELINE[k]) for k, v in counts.items() if v > BASELINE[k]}
    shrank = {k: (v, BASELINE[k]) for k, v in counts.items() if v < BASELINE[k]}
    assert not grew, (
        "New blind/silent exception handlers. Catch the exception you expect, "
        "or log it (logger.exception / exc_info=True) instead of passing:\n  "
        + "\n  ".join(f"{k}: {now} > baseline {base}" for k, (now, base) in grew.items())
    )
    assert not shrank, (
        "Debt went down — lock it in by lowering BASELINE in this file:\n  "
        + "\n  ".join(f"{k}: set to {now} (was {base})" for k, (now, base) in shrank.items())
    )


def test_ratchet_counts_what_it_says():
    """Negative control (§28)."""
    source = '''
try:
    a()
except Exception:
    pass
try:
    b()
except:
    log()
try:
    c()
except ValueError:
    pass
try:
    d()
except BaseException:
    "documented, still silent"
'''
    assert debt_counts([source]) == {"blind_except": 3, "silent_except": 2}
