"""A product module imports on its own, whatever was imported before it.

``scripts/check_fresh_imports.py`` imports every product module in a fresh
interpreter; CI runs it over all of them in its own job. These tests prove the
checker catches the failure it exists for (the ``routing_engine`` cycle,
2026-09-26), that the same cycle with the import deferred passes, why the
one-process gate in ``tests/test_imports.py`` cannot see it, and that the job
is wired.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CI = REPO / ".github" / "workflows" / "ci.yml"
SCRIPT = REPO / "scripts" / "check_fresh_imports.py"


def _checker():
    name = "_kazma_check_fresh_imports"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _write_cycle(root: Path, *, deferred: bool) -> list[str]:
    """The routing_engine shape, in miniature.

    ``cyclepkg/__init__`` imports its engine, the engine imports ``cyclerouter``
    from outside the package, and ``cyclerouter`` imports ``cyclepkg.task``
    back. Entered at the package it loads; entered at the router it does not,
    unless the router defers that import.
    """
    pkg = root / "cyclepkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("from cyclepkg.engine import Engine\n", encoding="utf-8")
    (pkg / "task.py").write_text("class Task:\n    pass\n", encoding="utf-8")
    (pkg / "engine.py").write_text(
        "from cyclerouter import Router\n\n\nclass Engine:\n    router = Router\n",
        encoding="utf-8",
    )
    if deferred:
        head = (
            "from __future__ import annotations\n\nfrom typing import TYPE_CHECKING\n\n"
            "if TYPE_CHECKING:\n    from cyclepkg.task import Task\n"
        )
    else:
        head = "from __future__ import annotations\n\nfrom cyclepkg.task import Task\n"
    (root / "cyclerouter.py").write_text(
        head + "\n\nclass Router:\n    def route(self, task: Task) -> None:\n        return None\n",
        encoding="utf-8",
    )
    return ["cyclepkg", "cyclepkg.engine", "cyclepkg.task", "cyclerouter"]


def test_a_cycle_entered_at_the_wrong_module_is_caught(tmp_path):
    modules = _write_cycle(tmp_path, deferred=False)
    failures = _checker().check(modules, cwd=tmp_path, jobs=2)
    assert [f.module for f in failures] == ["cyclerouter"]
    assert "partially initialized module" in failures[0].detail


def test_the_same_cycle_with_the_import_deferred_passes(tmp_path):
    """Negative control: the fix routing_engine got (TYPE_CHECKING import)."""
    modules = _write_cycle(tmp_path, deferred=True)
    assert _checker().check(modules, cwd=tmp_path, jobs=2) == []


def test_one_process_importing_the_package_first_hides_it(tmp_path):
    """Why tests/test_imports.py cannot see this, and the server never did."""
    _write_cycle(tmp_path, deferred=False)
    proc = subprocess.run(
        [sys.executable, "-c", "import cyclepkg, cyclerouter"],
        cwd=str(tmp_path), capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr


def test_the_routing_engine_imports_on_its_own():
    assert _checker().check(["kazma_core.routing_engine"], jobs=1) == []


def test_it_checks_every_module_the_import_gate_does():
    """One list: tests/test_imports.py walks the checker's enumeration."""
    from tests import test_imports

    checker = _checker()
    assert test_imports.MODULES == list(checker.iter_modules())
    names = checker.product_modules()
    assert {"kazma_core", "kazma_ui", "kazma_gateway", "kazma_tui", "kazma_cli",
            "kazma_skills", "kazma_core.routing_engine"} <= set(names)


def _job(text: str, key: str) -> str | None:
    match = re.search(rf"^  {re.escape(key)}:\n(.*?)(?=^  [A-Za-z0-9_-]+:\n|\Z)", text, re.S | re.M)
    return match.group(1) if match else None


def test_ci_runs_it_over_every_module_as_a_gate():
    job = _job(CI.read_text(encoding="utf-8"), "fresh-imports")
    assert job is not None, "the fresh-imports job is gone from ci.yml"
    run = [ln for ln in job.splitlines() if "check_fresh_imports.py" in ln]
    assert run, "the job no longer runs scripts/check_fresh_imports.py"
    for ln in run:
        # no module names (so every module) and nothing after that could
        # swallow the exit code, such as `|| true`
        tail = ln.split("check_fresh_imports.py", 1)[1]
        assert re.fullmatch(r"\s*(--jobs\s+\d+\s*)?", tail), ln.strip()
