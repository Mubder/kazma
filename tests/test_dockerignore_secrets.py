"""The application image never receives Kazma's secret files (audit 2026-11-10).

``Dockerfile`` does ``COPY . .``; the root ``.dockerignore`` named ``.env`` and
``*.db`` only, which match the context root alone, and had no rule for
``.kazma/`` (restic.pass, postgres_superuser.pass, observer.key,
document_broker.token, logs), ``.env.*`` or nested databases. Docker's matching
is implemented here simply (per path, ``**`` across folders, a matched folder
excludes what is inside it, the last matching rule wins), and every name Kazma
declares secret must be excluded at any depth.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

OLD_DOCKERIGNORE = """\
archive/
__pycache__/
*.pyc
*.pyo
.venv/
.git/
.github/
tests/
kazma-data/
docs/
*.md
!README.md
.env
*.db
*.db-journal
*.egg-info/
dist/
build/
.ruff_cache/
.pytest_cache/
.mypy_cache/
.poolside/
secrets/
"""


def _regex(pattern: str) -> re.Pattern[str]:
    out = ""
    i = 0
    while i < len(pattern):
        c = pattern[i]
        if pattern.startswith("**/", i):
            out += "(?:.*/)?"
            i += 3
            continue
        if pattern.startswith("**", i):
            out += ".*"
            i += 2
            continue
        out += "[^/]*" if c == "*" else "[^/]" if c == "?" else re.escape(c)
        i += 1
    return re.compile("^" + out + "$")


def _rules(text: str) -> list[tuple[bool, re.Pattern[str]]]:
    rules = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        negate = line.startswith("!")
        line = line.lstrip("!").strip().rstrip("/").lstrip("/")
        rules.append((negate, _regex(line)))
    return rules


def excluded(text: str, path: str) -> bool:
    """Docker's rule: a path is out when it, or a parent, matches (last rule wins)."""
    parts = path.split("/")
    rules = _rules(text)
    for end in range(1, len(parts) + 1):
        candidate = "/".join(parts[:end])
        verdict = None
        for negate, rx in rules:
            if rx.match(candidate):
                verdict = not negate
        if verdict:
            return True
    return False


def _names() -> list[str]:
    from kazma_core.store_registry import SECRET_FILE_NAMES

    names = set(SECRET_FILE_NAMES) | {".env", ".env.local", ".env.production", "vault.db", "x.db-wal"}
    return sorted(names)


def _paths() -> list[str]:
    out = []
    for name in _names():
        out += [name, f"sub/{name}", f"a/b/{name}", f".kazma/{name}", f"deploy/.kazma/{name}"]
    out += [".kazma/kazma.log", ".kazma/logs/guard.log", "kazma-core/.claude/settings.json", "reports/x.pdf"]
    return out


def test_every_secret_file_name_is_excluded_at_any_depth() -> None:
    text = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    leaked = [p for p in _paths() if not excluded(text, p)]
    assert not leaked, leaked


def test_the_env_template_still_reaches_the_image() -> None:
    text = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    assert not excluded(text, ".env.example")


def test_no_tracked_file_the_image_needs_is_caught_by_the_new_rules() -> None:
    text = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.splitlines()
    old = [p for p in tracked if excluded(OLD_DOCKERIGNORE, p)]
    newly = [p for p in tracked if excluded(text, p) and p not in old]
    # Tracked files the old file already kept out do not matter; none of the
    # rest (the image's source, extras, assets) may be caught by the new rules.
    assert not newly, newly[:20]


def test_negative_control_the_old_file_leaks() -> None:
    leaked = [p for p in _paths() if not excluded(OLD_DOCKERIGNORE, p)]
    assert ".kazma/restic.pass" in leaked and "sub/.env" in leaked and "a/b/vault.db" in leaked
    assert "observer.key" in leaked
