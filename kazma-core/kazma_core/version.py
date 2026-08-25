"""Product version — fixed public base + live git short SHA.

Policy (see ``docs/VERSIONING.md``)
------------------------------------
* **Public base** is static in root ``pyproject.toml`` (today ``0.10.0``).
  It changes only by deliberate hand edit / rare milestone — never by CI.
* **Display version** is always ``{base}+g{shortsha}`` when a commit id is
  known (PEP 440 local segment). Example: ``0.10.0+g4d37b2c``.
* No GitHub Action bumps digits. The SHA is the accuracy signal.

Resolution order for the short SHA
----------------------------------
1. ``KAZMA_GIT_SHA`` env (optional override / packaging)
2. ``GITHUB_SHA`` (GitHub Actions)
3. ``git rev-parse --short=N HEAD`` from the monorepo root
4. No SHA → return the public base alone (wheels without ``.git``)
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = [
    "FALLBACK_BASE_VERSION",
    "get_base_version",
    "get_git_short_sha",
    "get_public_version",
    "get_version",
    "clear_version_cache",
]

# Used when pyproject cannot be read (broken checkout, partial install).
FALLBACK_BASE_VERSION = "0.10.0"

_VERSION_LINE_RE = re.compile(r'(?m)^version\s*=\s*"([^"]+)"')


def clear_version_cache() -> None:
    """Drop cached base/sha (tests or after a deliberate base edit)."""
    get_base_version.cache_clear()
    get_git_short_sha.cache_clear()
    get_version.cache_clear()


def _repo_root() -> Path | None:
    """Locate monorepo root (directory with root ``pyproject.toml`` name=kazma)."""
    here = Path(__file__).resolve()
    # kazma_core/version.py → kazma-core/ → monorepo root
    candidates = [
        here.parents[2],  # …/kazma
        here.parents[1],  # …/kazma-core
        Path.cwd(),
    ]
    for parent in list(here.parents)[:10]:
        candidates.append(parent)
    seen: set[Path] = set()
    for root in candidates:
        try:
            key = root.resolve()
        except OSError:
            continue
        if key in seen:
            continue
        seen.add(key)
        pyproject = key / "pyproject.toml"
        if not pyproject.is_file():
            continue
        try:
            text = pyproject.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        # Prefer the monorepo root package named "kazma"
        if re.search(r'(?m)^name\s*=\s*"kazma"', text):
            return key
        # Accept any pyproject with a top-level version as last resort later
    # Second pass: any pyproject with version=
    for root in seen:
        pyproject = root / "pyproject.toml"
        if pyproject.is_file() and _VERSION_LINE_RE.search(
            pyproject.read_text(encoding="utf-8", errors="replace")
        ):
            return root
    return None


def _strip_local(version: str) -> str:
    """Drop PEP 440 local segment (``+g…``) and whitespace."""
    return version.split("+", 1)[0].strip()


@lru_cache(maxsize=1)
def get_base_version() -> str:
    """Public SemVer from root ``pyproject.toml`` (no ``+local``)."""
    root = _repo_root()
    if root is not None:
        path = root / "pyproject.toml"
        try:
            text = path.read_text(encoding="utf-8")
            match = _VERSION_LINE_RE.search(text)
            if match:
                return _strip_local(match.group(1))
        except OSError as exc:
            logger.debug("version: pyproject read failed: %s", exc)

    # Installed wheel metadata
    try:
        from importlib.metadata import PackageNotFoundError, version as pkg_version

        for dist in ("kazma", "kazma-core", "kazma-cli"):
            try:
                return _strip_local(pkg_version(dist))
            except PackageNotFoundError:
                continue
    except Exception as exc:
        logger.debug("version: importlib.metadata failed: %s", exc)

    return FALLBACK_BASE_VERSION


@lru_cache(maxsize=1)
def get_git_short_sha(length: int = 7) -> str | None:
    """Return short commit id (no leading ``g``), or ``None`` if unknown."""
    for key in ("KAZMA_GIT_SHA", "GITHUB_SHA"):
        raw = (os.environ.get(key) or "").strip()
        if raw:
            # Allow full or short; allow optional leading g
            raw = raw.lstrip("gG")
            if re.fullmatch(r"[0-9a-fA-F]+", raw):
                return raw[:length].lower()

    root = _repo_root() or Path.cwd()
    try:
        result = subprocess.run(
            ["git", "rev-parse", f"--short={length}", "HEAD"],
            cwd=root,
            text=True,
            capture_output=True,
            check=False,
            timeout=3,
        )
        if result.returncode == 0:
            sha = (result.stdout or "").strip().lstrip("gG")
            if re.fullmatch(r"[0-9a-fA-F]+", sha):
                return sha.lower()
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("version: git rev-parse failed: %s", exc)
    return None


@lru_cache(maxsize=1)
def get_version() -> str:
    """Full product version for banners/UI: ``0.10.0+g4d37b2c`` when possible."""
    base = get_base_version()
    sha = get_git_short_sha()
    if sha:
        return f"{base}+g{sha}"
    return base


def get_public_version() -> str:
    """Tag-friendly public version (no local segment). Alias of base."""
    return get_base_version()
