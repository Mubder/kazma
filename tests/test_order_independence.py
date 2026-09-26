"""A test's outcome does not depend on which tests ran before it.

``scripts/fast_test.py`` splits the suite into processes by file,
round-robin, so adding one test file changes every process's neighbours. Any
test that leaned on state another test left behind then passed or failed with
the split -- and was found only when a split happened to expose it
(docs/KNOWN_GAPS.md, "Test baseline"). Three routes carried state from one
test to the next; each is now closed for every test at once:

* the environment: a bare ``os.environ[...] = ...`` outlived its test. The
  root conftest restores the environment after every test.
* import timing: a module first imported while a test's patch was live kept
  the fake (``from owner import name`` copies the object; undoing the patch
  restores only ``owner.name``), and one first imported under a test's
  environment kept what it read. The root conftest imports every product
  module before the first test.
* module attributes: ``mod.attr = fake`` in a test was never undone.
  ``tests/test_debt_ratchet.py`` holds that count at zero.

The first two are shown working here, and shown to be what makes it work:
each negative control runs the same tests in a child pytest with the guard
off and watches them fail.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
HERE = "tests/test_order_independence.py"
_PROBE = "KAZMA_ORDER_INDEPENDENCE_PROBE"


def _child_pytest(*node_ids: str, **env_extra: str) -> subprocess.CompletedProcess:
    env = {**os.environ, **env_extra}
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *node_ids],
        cwd=str(REPO), env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=600,
    )


# ── the environment ────────────────────────────────────────────────────────


def test_a_bare_environment_write_is_made_here():
    """First of a pair. Written the way the 88 bare writes were, never undone."""
    os.environ[_PROBE] = "leaked"
    assert os.environ[_PROBE] == "leaked"


def test_and_does_not_reach_the_next_test():
    assert _PROBE not in os.environ


def test_the_environment_guard_is_set_up_first_and_torn_down_last(request):
    """Every other function fixture's cleanup runs inside the guard's window.

    Pytest orders a conftest's autouse fixtures by name, so a new root
    fixture that sorts before the guard would tear down after it, and an
    environment change it made would survive the test.
    """
    names = request.fixturenames
    guard = names.index("_environment_restored")
    for other in ("_isolate_process_singletons", "_isolated_tenant_context",
                  "_reset_shutdown_event", "_isolated_config_store", "monkeypatch"):
        if other in names:
            assert names.index(other) > guard, f"{other} is set up before the environment guard"


def test_without_the_guard_the_write_reaches_the_next_test():
    """Negative control: the same pair with test isolation off."""
    proc = _child_pytest(
        f"{HERE}::test_a_bare_environment_write_is_made_here",
        f"{HERE}::test_and_does_not_reach_the_next_test",
        KAZMA_TEST_ISOLATION="0",
    )
    assert proc.returncode == 1, proc.stdout[-2000:]
    assert "1 failed, 1 passed" in proc.stdout, proc.stdout[-2000:]


# ── import timing ──────────────────────────────────────────────────────────


def _product_modules() -> list[str]:
    name = "_kazma_check_fresh_imports"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / "check_fresh_imports.py")
        mod = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return sys.modules[name].product_modules()


def test_every_product_module_is_loaded_before_any_test_runs():
    """So no module can first be imported inside a test, under its patches."""
    missing = [m for m in _product_modules() if m not in sys.modules]
    assert not missing, f"{len(missing)} product modules not imported up front: {missing[:10]}"


def test_without_the_preimport_they_are_not():
    """Negative control: in a fresh session that only runs the test above,
    with the preimport off, most of the TUI and the skills are still unloaded."""
    proc = _child_pytest(
        f"{HERE}::test_every_product_module_is_loaded_before_any_test_runs",
        KAZMA_TEST_PREIMPORT="0",
    )
    assert proc.returncode == 1, proc.stdout[-2000:]
    assert "product modules not imported up front" in proc.stdout, proc.stdout[-2000:]
