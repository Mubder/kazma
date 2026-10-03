"""Kazma's code does not know who its owner is.

Every install has its own owner, and the memory hub records that owner's
names (``memory/self_hub.py``); the code reads them from there. Until
2026-10-03 four copies of the protected-entity list named the author of
Kazma, so every install protected an entity called "mubder" and none
protected its own owner's other names, and prompt notes, tool descriptions
and the fact extractor's prompt taught every model the author's hierarchy
and project names.

The gate reads every string literal in the product packages -- comments are
not code, docstrings are: none names the author, and none outside a
docstring names the author's projects (a docstring may record where a
measurement or an incident came from). The repository's own address
(``Mubder/kazma``) is the product's, not its owner's.
"""

from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

_AUTHOR = re.compile(r"mubder", re.IGNORECASE)
_PROJECTS = re.compile(
    r"(?<![a-z0-9])(?:shipx|kca|hypertfit|indexarc|qudrafit|qudrax|cortexswarm)(?![a-z0-9])",
    re.IGNORECASE,
)
# The repository's address in links, the updater and the release check.
_REPOSITORY = re.compile(r"mubder/kazma", re.IGNORECASE)


def _docstring_nodes(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                ids.add(id(first.value))
    return ids


def owner_identity_in(source: str) -> list[tuple[int, str]]:
    """``(line, text)`` of each string literal in *source* that names the
    author, or (outside a docstring) one of the author's projects."""
    tree = ast.parse(source)
    docstrings = _docstring_nodes(tree)
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        text = _REPOSITORY.sub("", node.value)
        hit = _AUTHOR.search(text)
        if hit is None and id(node) not in docstrings:
            hit = _PROJECTS.search(text)
        if hit is not None:
            found.append((node.lineno, text[max(0, hit.start() - 40): hit.end() + 40].replace("\n", " ")))
    return found


def _product_sources() -> dict[str, str]:
    files = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard",
         "kazma-*/kazma_*/*.py", "kazma-*/kazma_*/**/*.py"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    ).stdout.split()
    out: dict[str, str] = {}
    for rel in files:
        if "/tests/" in rel:
            continue
        path = REPO_ROOT / rel
        if path.is_file():
            out[rel] = path.read_text(encoding="utf-8", errors="replace")
    return out


def test_no_product_string_names_the_owner() -> None:
    sources = _product_sources()
    assert len(sources) > 300, "the product sources were not found"
    offenders = [
        f"{rel}:{line}: ...{text}..."
        for rel, source in sorted(sources.items())
        for line, text in owner_identity_in(source)
    ]
    assert not offenders, (
        "Kazma's code names its author or the author's projects. The owner of an "
        "install is known from the memory hub (memory/self_hub.py, "
        "memory/entity_protection.py); examples use generic names:\n" + "\n".join(offenders)
    )


def test_the_gate_sees_the_shapes_it_replaced() -> None:
    """Negative control: the code this gate replaced, and what it lets through."""
    old = (
        '"""Merge person shells into the hub."""\n'
        'PROTECTED_IDS = frozenset({"user", "assistant", "kazma", "mubder"})\n'
        'NOTE = "Mubder(user) -> has_project -> {kazma|shipx|kca} -> has_part -> details"\n'
        'SQL = "AND LOWER(e.id) NOT IN (\'user\',\'assistant\',\'kazma\',\'mubder\')"\n'
        'EXAMPLE = "Example: `/kb crawl shipx_whatsapp https://example.com`"\n'
        "def merge():\n"
        '    """Fold the author\'s old id: the mubder->user re-orphan bug."""\n'
    )
    assert sorted(line for line, _text in owner_identity_in(old)) == [2, 3, 4, 5, 7]
    # An underscore separates words, as in an entity id.
    assert [line for line, _text in owner_identity_in('ID = "kca_backup"\n')] == [1]

    allowed = (
        '"""Measured on the live install: ShipX held 264 turns."""\n'
        '_GITHUB_REPO = "Mubder/kazma"\n'
        'URL = "https://github.com/Mubder/kazma/issues/20"\n'
        'SITE = "https://github.com/Mubder/KazmaAI"\n'
        "# the author's mubder -> user merge, in a comment\n"
        'WORDS = "kcal and shipxyz are other words"\n'
    )
    assert owner_identity_in(allowed) == []
