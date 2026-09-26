#!/usr/bin/env python3
"""Import every product module on its own, each in a fresh interpreter.

A module that imports fine inside the running server can still fail when it
is the FIRST thing a process imports: an import cycle only works when it is
entered at the right module. ``kazma_core.routing_engine`` was one
(2026-09-26). It imported ``kazma_core.swarm.task``, which runs the swarm
package first, whose engine imports ``routing_engine`` back while it is half
built -- ``ImportError: cannot import name 'UnifiedRouter' from partially
initialized module``. The server always imports the swarm package first, so
nothing noticed; a script, skill or command that reached the router first
would have crashed.

``tests/test_imports.py`` imports every module in ONE process, where each
import finds the earlier ones already loaded, so it cannot see this. This does
the opposite: one fresh interpreter per module, with nothing loaded but the
module and what it imports itself.

Usage (repo root):
    python scripts/check_fresh_imports.py                  # every product module
    python scripts/check_fresh_imports.py --jobs 4         # worker processes (default: CPUs)
    python scripts/check_fresh_imports.py kazma_core.x ... # only these

CI runs it as its own job ("Every module imports on its own"), so it adds no
time to the suite; tests/test_fresh_imports.py proves it catches the cycle.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import os
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: Variables a child must never inherit: a database it could write to.
_STRIPPED = ("KAZMA_DATABASE_URL", "DATABASE_URL")


@dataclass(frozen=True)
class Failure:
    module: str
    returncode: int
    detail: str


def product_packages(root: Path = ROOT) -> dict[str, Path]:
    """Every ``kazma-*/kazma_*`` package of the monorepo, by import name."""
    found: dict[str, Path] = {}
    for pkg_dir in sorted(root.glob("kazma-*")):
        for child in sorted(pkg_dir.glob("kazma_*")):
            if (child / "__init__.py").is_file():
                found[child.name] = child
    return found


def iter_modules(root: Path = ROOT) -> Iterator[tuple[str, Path]]:
    """``(dotted name, file)`` for every product module, packages included.

    The one enumeration: ``tests/test_imports.py`` walks this too.
    """
    for package, directory in product_packages(root).items():
        for py in sorted(directory.rglob("*.py")):
            parts = list(py.relative_to(directory).with_suffix("").parts)
            if parts[-1] == "__init__":
                parts = parts[:-1]
            if parts and parts[-1] == "__main__":
                continue
            yield ".".join([package, *parts]), py


def product_modules(root: Path = ROOT) -> list[str]:
    return [name for name, _py in iter_modules(root)]


def _child_env(scratch: Path) -> dict[str, str]:
    """The caller's environment, minus databases, with data and logs in *scratch*.

    Importing must not do I/O, but if a module does, it lands here and not in
    anyone's data directory.
    """
    env = {k: v for k, v in os.environ.items() if k not in _STRIPPED}
    env.update({
        "KAZMA_DB_BACKEND": "sqlite",
        "KAZMA_DATA_DIR": str(scratch / "data"),
        "KAZMA_LOG_FILE": str(scratch / "kazma.log"),
        "KAZMA_OPS_ALERTS": "0",
        "KAZMA_LOOP_STALL_WATCHDOG": "0",
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    return env


def _import_alone(module: str, *, cwd: Path, env: dict[str, str], python: str,
                  timeout: float) -> Failure | None:
    try:
        proc = subprocess.run(
            [python, "-c", f"import {module}"],
            cwd=str(cwd), env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return Failure(module, -1, f"import did not finish in {timeout:.0f}s")
    if proc.returncode == 0:
        return None
    lines = [ln for ln in (proc.stderr or "").splitlines() if ln.strip()]
    return Failure(module, proc.returncode, "\n".join(lines[-4:]))


def check(modules: list[str], *, cwd: Path = ROOT, jobs: int | None = None,
          python: str = sys.executable, timeout: float = 180.0) -> list[Failure]:
    """Import each of *modules* in its own fresh interpreter; return the failures."""
    with tempfile.TemporaryDirectory(prefix="kazma-fresh-imports-") as tmp:
        env = _child_env(Path(tmp))
        workers = max(1, jobs or os.cpu_count() or 4)
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            results = pool.map(
                lambda m: _import_alone(m, cwd=cwd, env=env, python=python, timeout=timeout),
                modules,
            )
            return [f for f in results if f is not None]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("modules", nargs="*", help="dotted module names (default: every product module)")
    ap.add_argument("--jobs", type=int, default=None, help="worker processes (default: CPUs)")
    args = ap.parse_args(argv)
    modules = args.modules or product_modules()
    failures = check(modules, jobs=args.jobs)
    for f in failures:
        print(f"FAIL {f.module} (exit {f.returncode})\n{f.detail}\n", flush=True)
    print(f"[fresh-imports] {len(modules) - len(failures)}/{len(modules)} modules import on their own")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
