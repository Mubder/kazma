"""Every pip install in CI and the images is pinned by hash (2026-11-16).

CI's jobs ran ``pip install -e ".[test]"``, ``pip install ruff`` and
``pip install bandit``, and the Dockerfiles ``pip install -e ".[...]"``:
whatever PyPI served that day, never what uv.lock records. OpenSSF
Scorecard's Pinned-Dependencies counted 21 of them. Each now installs a set
exported from uv.lock with ``--require-hashes``, and Kazma itself editable
with ``--no-deps`` (``.github/actions/install-locked``, the Dockerfiles).

The rule is Scorecard's own (``checks/raw/shell_download_validate.go``,
``isUnpinnedPipInstall``): pinned is ``--require-hashes``, an editable install
of a local source with ``--no-deps``, or wheel files only. Every ``run:`` of
every workflow and composite action and every ``RUN`` of every Dockerfile is
read. Negative controls: the old lines are unpinned.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def _workflow_scripts() -> list[tuple[str, str]]:
    found = []
    files = sorted((ROOT / ".github" / "workflows").glob("*.y*ml"))
    files += sorted((ROOT / ".github" / "actions").glob("*/action.y*ml"))
    for path in files:
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        steps = [s for job in (doc.get("jobs") or {}).values() for s in (job.get("steps") or [])]
        steps += list(((doc.get("runs") or {}).get("steps")) or [])
        for step in steps:
            if isinstance(step, dict) and isinstance(step.get("run"), str):
                found.append((path.relative_to(ROOT).as_posix(), step["run"]))
    return found


def _docker_scripts() -> list[tuple[str, str]]:
    found = []
    for path in sorted(ROOT.glob("**/Dockerfile*")):
        if any(part in (".venv", "node_modules", ".git") for part in path.parts):
            continue
        text = path.read_text(encoding="utf-8", errors="replace").replace("\\\n", " ")
        for line in text.splitlines():
            if line.strip().upper().startswith("RUN "):
                found.append((path.relative_to(ROOT).as_posix(), line.strip()[4:]))
    return found


def _commands(script: str) -> list[list[str]]:
    """Each simple command of a shell script, as words."""
    out = []
    for line in re.split(r"\n|&&|\|\||;|\|", script.replace("\\\n", " ")):
        try:
            words = shlex.split(line, comments=True)
        except ValueError:
            words = line.split()
        if words:
            out.append(words)
    return out


def _pip_args(words: list[str]) -> list[str] | None:
    """The words after ``install`` of a pip install, or None for another command."""
    names = [w.rsplit("/", 1)[-1].lower() for w in words]
    for i, name in enumerate(names):
        if name in ("pip", "pip3") and i + 1 < len(words) and words[i + 1] == "install":
            return words[i + 2:]
        if name.startswith("python") and words[i + 1:i + 4] == ["-m", "pip", "install"]:
            return words[i + 4:]
    return None


def unpinned(args: list[str]) -> bool:
    """Scorecard's isUnpinnedPipInstall, on the words after ``install``."""
    no_deps = editable = has_wheel = extra = False
    editable_pinned = True
    # uv's interpreter option names an interpreter, not a package.
    args = [a for i, a in enumerate(args) if not (i and args[i - 1] in ("--python", "-p"))]
    for arg in args:
        if arg.lower() == "--no-deps":
            no_deps = True
            continue
        if arg in ("-e", "--editable"):
            editable = True
            continue
        if arg.lower() == "--require-hashes":
            return editable and (not no_deps or not editable_pinned)
        if re.fullmatch(r"(--?\w+)+", arg):
            continue
        if arg.endswith(".whl"):
            has_wheel = True
            continue
        if editable:
            local = not re.match(r"^(git|svn|hg|bzr)", arg)
            editable_pinned = editable_pinned and local
            continue
        extra = True
    if editable:
        return not no_deps or not editable_pinned
    if extra:
        return True
    return not has_wheel


def _installs() -> list[tuple[str, str]]:
    found = []
    for where, script in _workflow_scripts() + _docker_scripts():
        for words in _commands(script):
            args = _pip_args(words)
            if args is not None:
                found.append((where, " ".join(words)))
    return found


def test_there_are_installs_to_judge():
    assert len(_installs()) >= 3


def test_every_pip_install_is_pinned_by_hash():
    bad = []
    for where, command in _installs():
        args = _pip_args(shlex.split(command))
        if args is not None and unpinned(args):
            bad.append(f"{where}: {command}")
    assert not bad, "pip installs not pinned by hash:\n" + "\n".join(bad)


@pytest.mark.parametrize("old", [
    'pip install -e ".[test]" pillow "pymupdf>=1.24.0,<2" sqlite-vec pypdfium2 numpy prometheus-client',
    "pip install ruff",
    "pip install bandit",
    'pip install --no-cache-dir -e ".[rag,postgres,document-platform]"',
    'python -m pip install --upgrade "setuptools>=83.0.0"',
    "pip install -e .",
])
def test_control_the_old_lines_are_unpinned(old):
    args = _pip_args(shlex.split(old))
    assert args is not None and unpinned(args)


@pytest.mark.parametrize("pinned", [
    'python -m pip install --require-hashes -r "$requirements"',
    "pip install --no-cache-dir --require-hashes -r /tmp/kazma-requirements.txt",
    "python -m pip install --no-deps --no-build-isolation -e .",
    "pip install dist/kazma-0.21.2-py3-none-any.whl",
])
def test_the_new_forms_are_pinned(pinned):
    args = _pip_args(shlex.split(pinned))
    assert args is not None and not unpinned(args)


# ── Tools a job runs through uv (2026-11-16, SonarCloud S8541/S8544) ─────
# `uvx --from sigstore==4.5.1` and `uv run --with` pinned the tool alone: its
# dependencies came fresh from PyPI in the release job, which holds the
# signing identity. And `uv build` fetched hatchling unpinned. A job runs a
# tool from uv.lock (`uv run --frozen` or `--locked`, a dependency group for
# a tool that is not Kazma's) and builds with hash-checked build constraints.


def uv_problems(words: list[str]) -> list[str]:
    """What is unpinned about one uv command, or nothing."""
    if not words:
        return []
    head = words[0].rsplit("/", 1)[-1]
    if head == "uvx":
        return ["uvx resolves the tool's dependencies fresh"]
    if head != "uv" or len(words) < 2:
        return []
    sub, rest = words[1], words[2:]
    problems = []
    if sub == "tool" and rest[:1] in (["run"], ["install"]):
        problems.append("uv tool resolves fresh")
    if sub == "run":
        if "--frozen" not in rest and "--locked" not in rest:
            problems.append("uv run without --frozen or --locked")
        if any(w == "--with" or w.startswith("--with=") or w == "--with-requirements" for w in rest):
            problems.append("uv run --with resolves fresh")
    if sub == "build":
        if not any(w in ("--build-constraints", "-b") or w.startswith("--build-constraints=")
                   for w in rest):
            problems.append("uv build without --build-constraints")
        if "--require-hashes" not in rest:
            problems.append("uv build without --require-hashes")
    return problems


def test_every_uv_tool_run_and_build_in_ci_is_locked():
    offenders = [
        f"{where}: {' '.join(words)} -> {problems}"
        for where, script in _workflow_scripts()
        for words in _commands(script)
        if (problems := uv_problems(words))
    ]
    assert not offenders, "\n".join(offenders)


@pytest.mark.parametrize("old", [
    "uvx --from cyclonedx-bom==8.0.1 cyclonedx-py requirements requirements.lock.txt",
    "uvx --from sigstore==4.5.1 sigstore sign dist/kazma-*.whl",
    "uv run --no-project --with sigstore==4.5.1 python scripts/verify_release_signatures.py dist",
    "uvx --from twine==7.0.0 twine check --strict dist/*/*",
    "uv build --out-dir dist",
    "uv build --wheel --out-dir dist",
])
def test_control_the_old_release_lines_are_unlocked(old):
    assert uv_problems(shlex.split(old))


@pytest.mark.parametrize("new", [
    "uv run --frozen --only-group sign sigstore sign dist/kazma-*.whl",
    "uv run --frozen --only-group sbom cyclonedx-py requirements r.txt",
    "uv run --frozen python scripts/upgrade_drill.py --wheel x.whl",
    'uv build --build-constraints "$RUNNER_TEMP/b.txt" --require-hashes --out-dir dist',
])
def test_the_locked_forms_pass(new):
    assert uv_problems(shlex.split(new)) == []
