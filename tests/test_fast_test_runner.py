"""fast_test.py sanity floor (audit H-14) + traceback digest."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]


def _load_fast_test():
    path = _REPO / "scripts" / "fast_test.py"
    spec = importlib.util.spec_from_file_location("_fast_test_mod", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_zero_passed_is_nonzero_exit() -> None:
    ft = _load_fast_test()
    assert ft.suite_exit_code({"passed": 0}, failed=[], poison=[]) == 2
    assert ft.suite_exit_code({"passed": 10}, failed=[], poison=[]) == 2


def test_real_failures_exit_1() -> None:
    ft = _load_fast_test()
    assert ft.suite_exit_code(
        {"passed": 7000}, failed=["tests/x.py::t"], poison=[]
    ) == 1


def test_healthy_suite_exits_0() -> None:
    ft = _load_fast_test()
    assert ft.suite_exit_code({"passed": 7428}, failed=[], poison=[]) == 0


def test_failure_digest_has_negative_control() -> None:
    """A log with FAILED ids but no banner still yields a tail."""
    ft = _load_fast_test()
    log = "some output\nFAILED tests/foo.py::test_bar - assert 0\n"
    digest = ft._failure_digest(log, limit=500)
    assert "FAILED tests/foo.py::test_bar" in digest

    bannered = (
        "=========================== FAILURES ===========================\n"
        "E   assert 1 == 2\n"
        "=========================== short test summary info ===========================\n"
    )
    assert "assert 1 == 2" in ft._failure_digest(bannered, limit=500)
