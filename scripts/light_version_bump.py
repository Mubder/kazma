#!/usr/bin/env python3
"""Light product versioning for Kazma (0.x).

Policy
------
* **Auto / default:** only the **patch** digit moves
  (``0.12.0`` → ``0.12.1`` → ``0.12.2``). The middle digit never auto-jumps.
* **Commit id in the version string (PEP 440 local):**
  ``0.12.1+g92c55af`` so each release points at a concrete commit.
  (``+g…`` is the portable form of “commit code”; four-dot schemes like
  ``0.12.1.abc`` are not PEP 440 and break packaging tools.)
* **Minor** (middle digit, e.g. 12 → 13) and **major** require
  ``--confirm CONFIRM`` — used only from the manual Release workflow.

Git tags stay clean: ``v0.12.1`` (no ``+``). Files store the full local
version including ``+gSHA``.

Usage
-----
::

    python scripts/light_version_bump.py --level patch --write
    python scripts/light_version_bump.py --level minor --confirm CONFIRM --write --tag
    python scripts/light_version_bump.py --dry-run
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
KAZMA_YAML = ROOT / "kazma.yaml"
CHANGELOG = ROOT / "CHANGELOG.md"

_VERSION_RE = re.compile(
    r"""^
    (?P<major>0|[1-9]\d*)
    \.
    (?P<minor>0|[1-9]\d*)
    \.
    (?P<patch>0|[1-9]\d*)
    (?:
        (?P<pre>[a-zA-Z0-9.\-]+)?
    )?
    (?:
        \+(?P<local>[a-zA-Z0-9.\-]+)
    )?
    $
    """,
    re.VERBOSE,
)


def _run(cmd: list[str], *, check: bool = True) -> str:
    result = subprocess.run(
        cmd,
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"command failed ({result.returncode}): {' '.join(cmd)}\n"
            f"{result.stderr or result.stdout}"
        )
    return (result.stdout or "").strip()


def short_sha(length: int = 7) -> str:
    return _run(["git", "rev-parse", f"--short={length}", "HEAD"])


def read_pyproject_version() -> str:
    text = PYPROJECT.read_text(encoding="utf-8")
    match = re.search(r'(?m)^version\s*=\s*"([^"]+)"', text)
    if not match:
        raise RuntimeError("project.version not found in pyproject.toml")
    return match.group(1).strip()


def parse_base(version: str) -> tuple[int, int, int]:
    """Return (major, minor, patch), stripping any local ``+g…`` suffix."""
    base = version.split("+", 1)[0].strip()
    # Drop legacy pre segments for arithmetic (0.12.1rc1 → treat carefully)
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)", base)
    if not m:
        raise RuntimeError(f"unparseable version: {version!r}")
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def format_public(major: int, minor: int, patch: int) -> str:
    return f"{major}.{minor}.{patch}"


def format_full(major: int, minor: int, patch: int, sha: str | None) -> str:
    public = format_public(major, minor, patch)
    if not sha:
        return public
    # PEP 440 local version — commit code without breaking packaging.
    return f"{public}+g{sha}"


def bump_triplet(
    major: int, minor: int, patch: int, level: str
) -> tuple[int, int, int]:
    level = level.lower().strip()
    if level == "patch":
        return major, minor, patch + 1
    if level == "minor":
        return major, minor + 1, 0
    if level == "major":
        return major + 1, 0, 0
    raise ValueError(f"unknown level: {level}")


def write_version_files(full_version: str) -> None:
    # pyproject.toml
    text = PYPROJECT.read_text(encoding="utf-8")
    new_text, n = re.subn(
        r'(?m)^(version\s*=\s*")([^"]+)(")',
        rf"\g<1>{full_version}\g<3>",
        text,
        count=1,
    )
    if n != 1:
        raise RuntimeError("failed to update pyproject.toml version")
    PYPROJECT.write_text(new_text, encoding="utf-8")

    # kazma.yaml agent.version
    if KAZMA_YAML.is_file():
        ytext = KAZMA_YAML.read_text(encoding="utf-8")
        ynew, yn = re.subn(
            r"(?m)^(  version:\s*).+$",
            rf"\g<1>{full_version}",
            ytext,
            count=1,
        )
        if yn:
            KAZMA_YAML.write_text(ynew, encoding="utf-8")


def prepend_changelog(public: str, full: str, level: str) -> None:
    if not CHANGELOG.is_file():
        return
    from datetime import date

    entry = (
        f"## v{public} ({date.today().isoformat()})\n\n"
        f"- Release **{full}** (level={level}; auto light bump / confirmed)\n\n"
    )
    body = CHANGELOG.read_text(encoding="utf-8")
    # Insert after first H1 if present, else at top
    if body.startswith("#"):
        lines = body.splitlines(keepends=True)
        # after title line + blank
        insert_at = 0
        for i, line in enumerate(lines[:5]):
            if line.startswith("# "):
                insert_at = i + 1
                if insert_at < len(lines) and lines[insert_at].strip() == "":
                    insert_at += 1
                break
        new_body = "".join(lines[:insert_at]) + entry + "".join(lines[insert_at:])
    else:
        new_body = entry + body
    CHANGELOG.write_text(new_body, encoding="utf-8")


def last_public_tag() -> str | None:
    out = _run(
        ["git", "tag", "-l", "v[0-9]*", "--sort=-v:refname"],
        check=False,
    )
    for line in out.splitlines():
        tag = line.strip()
        if re.match(r"^v\d+\.\d+\.\d+$", tag):
            return tag
    return None


def should_skip_auto() -> bool:
    """Skip when HEAD is already the release commit for the current public tag."""
    tag = last_public_tag()
    if not tag:
        return False
    head = _run(["git", "rev-parse", "HEAD"])
    tagged = _run(["git", "rev-list", "-n", "1", tag], check=False)
    if tagged and tagged == head:
        return True
    # Also skip pure release/chore commits only? Keep simple: bump if not on tag.
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--level",
        choices=("patch", "minor", "major"),
        default="patch",
        help="Digit to bump (default: patch — light)",
    )
    parser.add_argument(
        "--confirm",
        default="",
        help='Must be exactly "CONFIRM" for minor or major bumps',
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Write version files (otherwise dry-run only)",
    )
    parser.add_argument(
        "--tag",
        action="store_true",
        help="Create annotated git tag vMAJOR.MINOR.PATCH",
    )
    parser.add_argument(
        "--commit",
        action="store_true",
        help="git commit the version files",
    )
    parser.add_argument(
        "--no-sha",
        action="store_true",
        help="Do not append +gSHORTSHA local segment",
    )
    parser.add_argument(
        "--skip-if-tagged",
        action="store_true",
        help="Exit 0 without changes if HEAD already matches latest v* tag",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Alias for default (no --write); print planned version",
    )
    args = parser.parse_args(argv)

    level = args.level
    if level in ("minor", "major") and args.confirm != "CONFIRM":
        print(
            f"error: level={level} requires --confirm CONFIRM "
            f"(middle/major digits are manual-only)",
            file=sys.stderr,
        )
        return 2

    if args.skip_if_tagged and should_skip_auto():
        print("HEAD already tagged — skip light bump")
        return 0

    current = read_pyproject_version()
    major, minor, patch = parse_base(current)
    nmaj, nmin, npat = bump_triplet(major, minor, patch, level)
    sha = None if args.no_sha else short_sha(7)
    public = format_public(nmaj, nmin, npat)
    full = format_full(nmaj, nmin, npat, sha)

    print(f"current : {current}")
    print(f"level   : {level}")
    print(f"public  : {public}  (tag would be v{public})")
    print(f"full    : {full}")
    if level == "minor":
        print(f"note    : middle digit {minor} → {nmin} (confirmed)")
    if level == "major":
        print(f"note    : major {major} → {nmaj} (confirmed)")

    if args.dry_run or not args.write:
        if not args.write:
            print("(dry-run — pass --write to apply)")
        return 0

    write_version_files(full)
    prepend_changelog(public, full, level)
    print(f"wrote   : pyproject.toml + kazma.yaml → {full}")

    if args.commit:
        _run(["git", "add", str(PYPROJECT), str(KAZMA_YAML), str(CHANGELOG)])
        msg = f"chore(release): version {current} → {full} [skip ci]"
        _run(["git", "commit", "-m", msg])
        print(f"commit  : {msg}")

    if args.tag:
        tag = f"v{public}"
        # Avoid duplicate tags
        existing = _run(["git", "tag", "-l", tag], check=False)
        if existing.strip() == tag:
            print(f"tag     : {tag} already exists — leave as-is")
        else:
            _run(
                [
                    "git",
                    "tag",
                    "-a",
                    tag,
                    "-m",
                    f"Kazma {full}",
                ]
            )
            print(f"tag     : {tag} ({full})")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
