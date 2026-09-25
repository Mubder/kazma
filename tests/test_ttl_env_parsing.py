"""What the value of a ``*_TTL_SECONDS`` window variable means, per knob.

Three of these parsers (YOLO, per-tool approval grants, ``/long``) matched
digits first, so ``0`` was always clamped to the 60-second minimum, while a
comment and an unreachable branch below promised "0 = no expiry". For a window
in which danger tools skip approval the short reading of zero is the safe one,
so the behaviour stays and the code now says it (2026-09-25). ``/unrestricted``
is the one knob where ``0`` means "until turned off": the product tells the
user exactly that, so it stays too.

Every knob that accepts a no-expiry word must be listed here with the meaning
of ``0`` — a new one cannot pick its reading of zero by accident.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

#: variable -> (module, function, default seconds, what "0" means)
KNOBS: dict[str, tuple[str, str, int, int]] = {
    "KAZMA_YOLO_TTL_SECONDS": ("kazma_core.safety.yolo", "_ttl_seconds", 3600, 60),
    "KAZMA_HITL_GRANT_TTL_SECONDS": ("kazma_core.safety.hitl_grants", "_ttl_seconds", 1800, 60),
    "KAZMA_LONG_TASK_TTL_SECONDS": ("kazma_core.agent.long_task", "_ttl_seconds", 1800, 60),
    "KAZMA_UNRESTRICTED_TTL_SECONDS": (
        "kazma_core.agent.long_task", "unrestricted_ttl_seconds", 3600, 0,
    ),
}


def _read(monkeypatch, var: str, raw: str | None) -> int:
    module, func, _default, _zero = KNOBS[var]
    if raw is None:
        monkeypatch.delenv(var, raising=False)
    else:
        monkeypatch.setenv(var, raw)
    return getattr(importlib.import_module(module), func)()


@pytest.mark.parametrize("var", sorted(KNOBS))
def test_unset_and_unreadable_values_give_the_default(monkeypatch, var):
    default = KNOBS[var][2]
    assert _read(monkeypatch, var, None) == default
    assert _read(monkeypatch, var, "soon") == default


@pytest.mark.parametrize("var", sorted(KNOBS))
def test_the_no_expiry_words(monkeypatch, var):
    for word in ("off", "none", "infinite"):
        assert _read(monkeypatch, var, word) == 0, word


@pytest.mark.parametrize("var", sorted(KNOBS))
def test_numbers_are_seconds_with_a_one_minute_floor(monkeypatch, var):
    assert _read(monkeypatch, var, "600") == 600
    assert _read(monkeypatch, var, "30") == 60


@pytest.mark.parametrize("var", sorted(KNOBS))
def test_what_zero_means(monkeypatch, var):
    expected = KNOBS[var][3]
    assert _read(monkeypatch, var, "0") == expected, (
        f"{var}=0 must give {expected} (a result of 0 means no expiry); the "
        "environment-variables page documents it that way"
    )


def _ttl_knobs_with_no_expiry_words(source: str) -> set[str]:
    """Variables read by a function that also accepts ``infinite``."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        strings = {
            n.value for n in ast.walk(node)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
        }
        if "infinite" in strings:
            found |= {s for s in strings if s.startswith("KAZMA_") and s.endswith("_TTL_SECONDS")}
    return found


def test_every_ttl_knob_with_a_no_expiry_word_is_pinned_here():
    found: set[str] = set()
    for pkg in ("kazma-core", "kazma-ui", "kazma-gateway", "kazma-cli", "kazma-skills", "kazma-tui"):
        for path in (REPO / pkg).rglob("*.py"):
            if any(part == "tests" or part.endswith("_tests") for part in path.parts):
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if "infinite" in text and "_TTL_SECONDS" in text:
                found |= _ttl_knobs_with_no_expiry_words(text)
    assert found == set(KNOBS), (
        f"unpinned: {sorted(found - set(KNOBS))}; gone: {sorted(set(KNOBS) - found)}. "
        "Add the knob to KNOBS with what '0' means for it."
    )


def test_the_scanner_finds_a_new_knob():
    """Negative control."""
    source = (
        "import os\n"
        "def _ttl():\n"
        "    raw = os.environ.get('KAZMA_NEW_TTL_SECONDS', '')\n"
        "    return 0 if raw in ('off', 'infinite') else 60\n"
        "def unrelated():\n"
        "    return os.environ.get('KAZMA_OTHER_TTL_SECONDS')\n"
    )
    assert _ttl_knobs_with_no_expiry_words(source) == {"KAZMA_NEW_TTL_SECONDS"}
