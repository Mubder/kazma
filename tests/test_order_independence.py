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


# ── the workspace binding ──────────────────────────────────────────────────
# A test that built an agent left the MCP rebind subscribed with its executor,
# and a pin one test set named its root in the next; on CI a workspace-context
# test got the previous test's root (2026-09-26, 09-27).

_PROBE_PIN = REPO / "order-independence-probe-root"
_PROBE_EXECUTOR = object()


def _probe_subscriber(root, reason):
    return None


def test_a_workspace_binding_is_left_here():
    """First of a pair: the shape an agent-building test left behind."""
    from kazma_core.workspace import binding, mcp_rebind

    mcp_rebind.install_mcp_workspace_rebind(_PROBE_EXECUTOR)
    binding.subscribe_root_changed(_probe_subscriber)
    binding.configure_workspace(str(_PROBE_PIN))
    assert binding.get_process_pin() == _PROBE_PIN.resolve()


def test_and_the_binding_does_not_reach_the_next_test():
    from kazma_core.workspace import binding, mcp_rebind

    assert binding.get_process_pin() != _PROBE_PIN.resolve()
    assert _probe_subscriber not in binding._subscribers
    assert mcp_rebind._executor_ref is not _PROBE_EXECUTOR


def test_without_the_guard_the_binding_reaches_the_next_test():
    """Negative control: the same pair with test isolation off."""
    proc = _child_pytest(
        f"{HERE}::test_a_workspace_binding_is_left_here",
        f"{HERE}::test_and_the_binding_does_not_reach_the_next_test",
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


# ── a thread left running ──────────────────────────────────────────────────

_THREAD_PROBE = "KAZMA_THREAD_GUARD_PROBE"


def test_a_thread_left_running_is_started_here():
    """Only under the probe variable (a child run below): starts a
    non-daemon thread that never ends -- an unclosed aiosqlite connection's
    worker, in miniature."""
    import threading

    if os.environ.get(_THREAD_PROBE) != "1":
        return
    threading.Thread(target=threading.Event().wait, name="probe-left-running", daemon=False).start()


def test_a_thread_left_running_is_named_and_the_run_ends():
    """The guard: the run ends, fails, and names the test (2026-09-27)."""
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         f"{HERE}::test_a_thread_left_running_is_started_here"],
        cwd=str(REPO), env={**os.environ, _THREAD_PROBE: "1", "KAZMA_TEST_PREIMPORT": "0"},
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,
    )
    assert proc.returncode == 1, proc.stdout[-2000:]
    line = next((ln for ln in proc.stdout.splitlines() if ln.startswith("ERROR conftest.py::thread_left_running")), "")
    assert "probe-left-running" in line and "test_a_thread_left_running_is_started_here" in line, proc.stdout[-2000:]


def test_without_the_guard_the_run_never_ends():
    """Negative control: the same child run with the guard off is still
    running long after its one test passed -- the hang the guard ends."""
    try:
        subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
             f"{HERE}::test_a_thread_left_running_is_started_here"],
            cwd=str(REPO),
            env={**os.environ, _THREAD_PROBE: "1", "KAZMA_TEST_PREIMPORT": "0", "KAZMA_TEST_THREAD_GUARD": "0"},
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=45,
        )
    except subprocess.TimeoutExpired as exc:
        assert "1 passed" in (exc.stdout or b"").decode("utf-8", "replace") if isinstance(exc.stdout, bytes) \
            else "1 passed" in (exc.stdout or "")
        return
    raise AssertionError("with the guard off the child run exited, so the probe proves nothing")
