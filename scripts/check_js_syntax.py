"""Every JavaScript file in the repository parses.

    python scripts/check_js_syntax.py [--node PATH]

Exit 0 when every tracked ``.js``/``.mjs``/``.cjs`` file parses, 1 naming the
ones that do not. A ``.js`` file is read as a script first and, when that
fails, as an ES module (k6 scripts, the docs site's config, a page's
``type="module"`` entry); ``.mjs`` is a module and ``.cjs`` a script.

CI checked only ``kazma-ui/kazma_ui/static/js``. ``loadtests/k6_swarm.js``
began with eight lines of Python imports, so neither k6 nor anything else could
run it, and CodeQL reported the file it could not read as a warning on every
analysis (2026-11-14).
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def tracked_js(repo: Path = REPO) -> list[Path]:
    """Every JavaScript file git tracks, outside ``node_modules``."""
    out = subprocess.run(
        ["git", "-C", str(repo), "ls-files", "-z", "--", "*.js", "*.mjs", "*.cjs"],
        capture_output=True, check=True,
    ).stdout.decode("utf-8")
    return [repo / p for p in out.split("\0") if p and "node_modules/" not in p]


def _node_check(node: str, path: Path) -> str | None:
    """None when ``node --check`` accepts *path*, else its error."""
    done = subprocess.run([node, "--check", str(path)], capture_output=True,
                          text=True, encoding="utf-8", errors="replace",
                          stdin=subprocess.DEVNULL, timeout=60)
    return None if done.returncode == 0 else (done.stderr or done.stdout).strip()


def parse_error(node: str, path: Path) -> str | None:
    """None when *path* parses as what its name says, else why it does not.

    A ``.js`` file is copied as ``.cjs`` and as ``.mjs`` and each is checked:
    ``node --check`` on a ``.js`` file leaves the choice to node's module
    detection, which accepted the old k6 script (valid as neither) with exit 0.
    """
    if path.suffix != ".js":
        return _node_check(node, path)
    with tempfile.TemporaryDirectory(prefix="kazma-js-") as tmp:
        script, module = Path(tmp) / (path.stem + ".cjs"), Path(tmp) / (path.stem + ".mjs")
        script.write_bytes(path.read_bytes())
        module.write_bytes(path.read_bytes())
        as_script = _node_check(node, script)
        if as_script is None:
            return None
        as_module = _node_check(node, module)
    return None if as_module is None else f"as a script: {as_script}\nas a module: {as_module}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--node", default=shutil.which("node"))
    args = parser.parse_args(argv)
    if not args.node:
        print("node is not installed")
        return 2
    files = tracked_js()
    with ThreadPoolExecutor(max_workers=8) as pool:
        errors = list(pool.map(lambda p: parse_error(args.node, p), files))
    failed = [(p, e) for p, e in zip(files, errors) if e]
    for path, error in failed:
        print(f"FAILED: {path.relative_to(REPO).as_posix()}\n{error}\n")
    if failed:
        print(f"{len(failed)} of {len(files)} JavaScript files do not parse")
        return 1
    print(f"All {len(files)} JavaScript files parse")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
