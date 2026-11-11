"""Every GitHub Action a workflow uses is pinned to a commit (2026-11, FA-209),
and every package a workflow or a build script runs through ``uvx`` or ``npx``
names an exact version (2026-11, supply chain).

A tag (``actions/checkout@v7``) is a name its owner can move: a compromised
action account could point it at other code, and every workflow here --
the release one, which signs and publishes, among them -- would run it with
the repository's token. Each ``uses:`` names a 40-hex commit, the tag kept
as a comment for Dependabot and readers. A planted tag is the negative
control.

``uvx`` and ``npx`` are the same door: with no version they run whatever
PyPI or npm serves that day. The release workflow made its SBOM with
``uvx --from cyclonedx-bom``, the PyPI reservation workflow checked its
packages with ``uvx twine``, and ``scripts/vendor_codemirror.py`` minified
the CodeMirror bundle Kazma ships with ``terser@5``. Each now names one
release (``name==1.2.3`` / ``name@1.2.3``); the three old forms are the
negative controls.
"""

from __future__ import annotations

import ast
import re
import shlex
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_USES = re.compile(r"^\s*-?\s*uses:\s*(\S+)", re.M)
_PINNED = re.compile(r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}$")


def _unpinned(text: str) -> list[str]:
    return [ref for ref in _USES.findall(text) if not ref.startswith("./") and not _PINNED.match(ref)]


def test_every_action_is_pinned_to_a_commit():
    found = {}
    for workflow in sorted((ROOT / ".github" / "workflows").glob("*.y*ml")):
        refs = _unpinned(workflow.read_text(encoding="utf-8"))
        if refs:
            found[workflow.name] = refs
    assert not found, f"pin these by commit SHA (keep the tag as a comment): {found}"


def test_negative_control_a_tag_is_found():
    planted = "steps:\n  - uses: actions/checkout@v7\n  - uses: actions/setup-python@" + "a" * 40 + " # v7\n"
    assert _unpinned(planted) == ["actions/checkout@v7"]


# ── packages run through uvx / npx ─────────────────────────────────────────

_RUNNERS = {"uvx", "npx"}
#: Flags whose next word is a value that is not a package.
_VALUE_FLAGS = {"--python", "-p", "--index-url", "--extra-index-url", "--cache-dir", "--registry"}
#: Flags whose next word is a package to run or add.
_PACKAGE_FLAGS = {"--from", "--with", "--package"}
_EXACT = re.compile(r"^(?:@[\w.-]+/)?[\w.-]+(?:==|@)\d+(?:\.\d+)+$")


def _runner_name(word: str) -> str:
    name = Path(str(word)).name.lower()
    for suffix in (".cmd", ".exe"):
        name = name.removesuffix(suffix)
    return name


def unpinned_packages(argv: list[str]) -> list[str]:
    """The packages an ``uvx``/``npx`` argv runs without an exact version."""
    found: list[str] = []
    for start, word in enumerate(argv):
        if _runner_name(word) not in _RUNNERS:
            continue
        packages: list[str] = []
        explicit_from = False
        i = start + 1
        while i < len(argv):
            word = argv[i]
            if word in _PACKAGE_FLAGS and i + 1 < len(argv):
                packages.append(argv[i + 1])
                explicit_from = explicit_from or word in ("--from", "--package")
                i += 2
                continue
            if word in _VALUE_FLAGS:
                i += 2
                continue
            if word.startswith("-"):
                i += 1
                continue
            if not explicit_from:
                packages.append(word)  # the command is the package
            break
        found += [p for p in packages if not _EXACT.match(p)]
    return found


def _shell_lines(text: str) -> list[list[str]]:
    lines = []
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("#") or not re.search(r"\b(?:uvx|npx)\b", line):
            continue
        line = re.sub(r"^(?:-\s*)?run:\s*", "", line)
        for part in re.split(r"&&|\|\||;|\|", line):
            try:
                words = shlex.split(part, posix=True)
            except ValueError:
                words = part.split()
            lines.append(words)
    return lines


def _module_strings(tree: ast.Module) -> dict[str, str]:
    out: dict[str, str] = {}
    for node in tree.body:
        if (isinstance(node, (ast.Assign, ast.AnnAssign)) and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)):
            for target in (node.targets if isinstance(node, ast.Assign) else [node.target]):
                if isinstance(target, ast.Name):
                    out[target.id] = node.value.value
    return out


def _word(node: ast.AST, consts: dict[str, str]) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return node.id if node.id in _RUNNERS else consts.get(node.id, "?")
    if isinstance(node, ast.JoinedStr):
        parts = []
        for value in node.values:
            if isinstance(value, ast.Constant):
                parts.append(str(value.value))
            elif isinstance(value, ast.FormattedValue) and isinstance(value.value, ast.Name):
                parts.append(consts.get(value.value.id, "?"))
            else:
                parts.append("?")
        return "".join(parts)
    return "?"


def python_argvs(source: str) -> list[list[str]]:
    """Each list or tuple of words in *source* that runs ``uvx``/``npx``,
    module-level string constants resolved (``f"terser@{TERSER_VERSION}"``)."""
    tree = ast.parse(source)
    consts = _module_strings(tree)
    argvs = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.List, ast.Tuple)) and node.elts:
            words = [_word(e, consts) for e in node.elts]
            if any(_runner_name(w) in _RUNNERS for w in words):
                argvs.append(words)
    return argvs


def _scanned() -> list[Path]:
    files = sorted((ROOT / ".github" / "workflows").glob("*.y*ml"))
    files += sorted(p for p in (ROOT / "scripts").rglob("*") if p.suffix in (".py", ".sh", ".ps1"))
    return files


def test_every_uvx_and_npx_package_names_an_exact_version():
    offenders: list[str] = []
    seen = 0
    for path in _scanned():
        text = path.read_text(encoding="utf-8", errors="replace")
        argvs = python_argvs(text) if path.suffix == ".py" else _shell_lines(text)
        for argv in argvs:
            seen += any(_runner_name(w) in _RUNNERS for w in argv)
            offenders += [f"{path.relative_to(ROOT).as_posix()}: {p}" for p in unpinned_packages(argv)]
    assert seen >= 3, "the gate found almost no uvx/npx runs; it would be decoration"
    assert not offenders, (
        "uvx/npx runs whatever the registry serves that day: name one release "
        "(uvx --from name==1.2.3, npx name@1.2.3):\n  " + "\n  ".join(offenders)
    )


def test_negative_control_the_three_loose_tools_are_found():
    sbom = "          uvx --from cyclonedx-bom cyclonedx-py requirements requirements.lock.txt \\\n"
    twine = "        run: uvx twine check --strict dist/*/*\n"
    assert [unpinned_packages(a) for a in _shell_lines(sbom)] == [["cyclonedx-bom"]]
    assert [unpinned_packages(a) for a in _shell_lines(twine)] == [["twine"]]
    terser = 'def m(npx, src):\n    return [npx, "--yes", "--prefer-offline", "terser@5", src, "-c"]\n'
    assert [unpinned_packages(a) for a in python_argvs(terser)] == [["terser@5"]]
    # The pinned forms pass, a constant resolved through its f-string.
    assert unpinned_packages(["uvx", "--from", "cyclonedx-bom==8.0.1", "cyclonedx-py"]) == []
    assert unpinned_packages(["uvx", "--from", "twine==7.0.0", "twine", "check"]) == []
    pinned = 'V = "5.51.2"\ndef m(npx, src):\n    return [npx, "--yes", f"terser@{V}", src]\n'
    assert [unpinned_packages(a) for a in python_argvs(pinned)] == [[]]
    # More of the same door.
    assert unpinned_packages(["npx", "-y", "@scope/tool"]) == ["@scope/tool"]
    assert unpinned_packages(["uvx", "ruff@latest", "check"]) == ["ruff@latest"]
    assert unpinned_packages(["uvx", "--with", "requests", "--from", "x==1.0", "x"]) == ["requests"]
    # A comment that names the tool is not a run.
    assert _shell_lines("# uvx twine check\n") == []
