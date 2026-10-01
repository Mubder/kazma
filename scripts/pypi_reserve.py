"""Build the packages that hold Kazma's names on PyPI: no code, only a page.

Kazma ships as a git checkout and as GitHub release wheels, never from PyPI
(AGENTS.md section 45, AUD-029). Until the project held its names there,
``kazma`` and the package names around it were anyone's to register, and a
stranger's upload under them would have run on any machine that asked pip for
Kazma by name. Each name gets a reservation: version 0.0.1, no modules, and a
description that says where Kazma really comes from.

``.github/workflows/pypi-reserve.yml`` builds these and, when asked, uploads
them through PyPI Trusted Publishing (no token anywhere). Each name needs its
pending publisher on pypi.org first: docs/docs/security/supply-chain.md says
what to enter.

Usage:
    python scripts/pypi_reserve.py --out dist/pypi-reserve        # every name
    python scripts/pypi_reserve.py --out dist/x --names kazma     # one name
    python scripts/pypi_reserve.py --list                         # the names
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

#: Every name the project holds on PyPI: the distribution (``kazma``), each
#: package directory's name, and the retired ``kazma-memory``.
#: ``tests/test_no_pypi_kazma.py`` holds its list of Kazma names to this one.
NAMES: tuple[str, ...] = (
    "kazma",
    "kazma-cli",
    "kazma-core",
    "kazma-gateway",
    "kazma-memory",
    "kazma-skills",
    "kazma-tui",
    "kazma-ui",
)

VERSION = "0.0.1"

_REPO = "https://github.com/Mubder/kazma"
_SITE = "https://kazma.ai"

_README = """# {name}: a name held by the Kazma project

This package contains no code. The Kazma project holds the name `{name}` on
PyPI so that no one else can publish under it.

Kazma is not installed from PyPI. It is installed from its GitHub releases,
whose wheels carry checksums and build provenance:

- Releases: {repo}/releases
- Source: {repo}
- Documentation: {site}

To check a release before installing it, follow the supply-chain guide:
{site}/docs/security/supply-chain/
"""

_PYPROJECT = """[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "{name}"
version = "{version}"
description = "Held by the Kazma project: contains no code. Kazma installs from its GitHub releases."
readme = "README.md"
requires-python = ">=3.11"
license = {{ text = "MIT" }}
authors = [{{ name = "Mubder Alfaris / KazmaAI" }}]
classifiers = [
    "Development Status :: 7 - Inactive",
    "License :: OSI Approved :: MIT License",
    "Programming Language :: Python :: 3",
]

[project.urls]
Homepage = "{site}"
Source = "{repo}"
Releases = "{repo}/releases"

[tool.hatch.build.targets.wheel]
# Nothing to install: the wheel is its metadata.
bypass-selection = true

[tool.hatch.build.targets.sdist]
include = ["README.md", "pyproject.toml"]
"""


def write_project(name: str, where: Path) -> Path:
    """Write the reservation project for *name* into *where*; returns it."""
    if name not in NAMES:
        raise ValueError(f"{name!r} is not one of the names the project holds")
    where.mkdir(parents=True, exist_ok=True)
    (where / "README.md").write_text(
        _README.format(name=name, repo=_REPO, site=_SITE), encoding="utf-8"
    )
    (where / "pyproject.toml").write_text(
        _PYPROJECT.format(name=name, version=VERSION, repo=_REPO, site=_SITE), encoding="utf-8"
    )
    return where


def build(name: str, out: Path) -> list[Path]:
    """Build *name*'s sdist and wheel into ``out/<name>/``."""
    target = out / name
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    with tempfile.TemporaryDirectory(prefix=f"pypi-reserve-{name}-") as tmp:
        project = write_project(name, Path(tmp))
        uv = shutil.which("uv")
        command = (
            [uv, "build", "--out-dir", str(target), str(project)]
            if uv
            else [sys.executable, "-m", "build", "--outdir", str(target), str(project)]
        )
        subprocess.run(command, check=True)
    # Only the distributions: uv also writes a .gitignore into its output,
    # which an upload of the folder would try to send.
    for path in target.iterdir():
        if not path.name.endswith((".whl", ".tar.gz")):
            path.unlink()
    return sorted(target.iterdir())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, help="folder for the built packages")
    parser.add_argument("--names", help="comma-separated subset (default: every name)")
    parser.add_argument("--list", action="store_true", help="print the names and exit")
    args = parser.parse_args(argv)
    if args.list:
        print("\n".join(NAMES))
        return 0
    if args.out is None:
        parser.error("--out is required")
    names = [n.strip() for n in (args.names or ",".join(NAMES)).split(",") if n.strip()]
    unknown = sorted(set(names) - set(NAMES))
    if unknown:
        parser.error(f"not names the project holds: {', '.join(unknown)}")
    for name in names:
        for path in build(name, args.out):
            print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
