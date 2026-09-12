"""A search that only looked at half the workspace must say so.

`iter_source_files` stops dead at `MAX_FILES` (4000) and reports nothing. On
the operator's workspace, 2026-09-12, that produced a silent, total failure of
code search:

    indexable files: 8697   cap: 4000
      4579  core/      <- an accidental LibreOffice checkout
      2453  AppData/   <- a uv cache, created by a tool with APPDATA inside the project
       552  tests/
       530  kazma-core/     <- the actual source
       144  kazma-ui/

`os.walk` is alphabetical, so `AppData` and `core` consumed the entire budget
and **`kazma-core` was never indexed at all**. `codebase_search` answered "no
hits" for code that plainly exists, and took long enough doing it that one call
hit the 120-second tool timeout. Nothing in the output said the index was
partial.

Two changes. Windows user-data trees are skipped outright — they should never
be inside a project, and when they are they are never what you are searching
for. And truncation is reported, because "no hits" and "I only looked at part
of the workspace" are different answers and only one of them is honest.

The cap itself stays. Removing it would trade a wrong answer for a slow one on
a genuinely huge tree; saying so costs nothing and is true either way.
"""

from __future__ import annotations

from kazma_core.code_index.search import format_search
from kazma_core.code_index.walk import SKIP_DIRS, should_skip_dir


# ── the walk ────────────────────────────────────────────────────────────────


def test_windows_user_data_trees_are_skipped():
    """`AppData/Local/uv/cache` alone was 2,453 indexable files."""
    for name in ("AppData", "Application Data", "Local Settings"):
        assert should_skip_dir(name), f"{name} would be indexed"
        assert name in SKIP_DIRS


def test_the_existing_skips_still_hold():
    """The counterweight: this list is load-bearing and easy to break while
    editing. A regression here silently doubles every index."""
    for name in (".venv", "node_modules", ".git", "__pycache__", "site-packages",
                 "dist", "build", "kazma-data"):
        assert should_skip_dir(name)


def test_ordinary_source_dirs_are_not_skipped():
    for name in ("kazma-core", "src", "tests", "app", "lib"):
        assert not should_skip_dir(name), f"{name} must be indexed"


# ── the report ──────────────────────────────────────────────────────────────


def _result(truncated: bool, *, hits: bool = False):
    return {
        "query": "foo",
        "symbols": [{"path": "a.py", "line": 1, "kind": "def", "name": "foo"}] if hits else [],
        "text": [],
        "stats": {"files": 4000, "symbols": 9, "truncated": truncated, "limit": 4000},
    }


def test_no_hits_on_a_truncated_index_says_so():
    """The worst case: the answer looks authoritative and is not."""
    out = format_search(_result(True))
    assert "TRUNCATED" in out
    assert "4000" in out
    assert "incomplete" in out


def test_hits_on_a_truncated_index_say_so_too():
    """Finding something does not mean it found everything -- and a caller that
    got a hit is exactly the one likely to stop looking."""
    out = format_search(_result(True, hits=True))
    assert "a.py:1" in out, "the results must still be returned"
    assert "TRUNCATED" in out


def test_a_complete_index_says_nothing():
    """The counterweight. A warning on every search is a warning nobody reads,
    and this one needs to be believed the day it appears."""
    assert "TRUNCATED" not in format_search(_result(False))
    assert "TRUNCATED" not in format_search(_result(False, hits=True))


def test_the_warning_names_the_fix_not_just_the_problem():
    out = format_search(_result(True))
    assert "workspace root" in out or "foreign trees" in out


# ── the flag itself ─────────────────────────────────────────────────────────


def test_ensure_index_reports_truncation(tmp_path, monkeypatch):
    """Pinned end-to-end: the flag has to come from the indexer, not be
    synthesised by the formatter."""
    import kazma_core.code_index.indexer as idx

    monkeypatch.setattr(idx, "code_index_enabled", lambda: True)
    monkeypatch.setattr(idx, "MAX_FILES", 3, raising=False)

    src = tmp_path / "proj"
    src.mkdir()
    for i in range(6):
        (src / f"m{i}.py").write_text("def f():\n    return 1\n", encoding="utf-8")

    monkeypatch.setattr(idx, "iter_source_files", lambda root: iter(sorted(src.glob("*.py"))[:3]))
    out = idx.ensure_index(src)
    assert out.get("walked") == 3
    assert out.get("truncated") is True, "walking exactly the cap means it stopped early"
