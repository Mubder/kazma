"""Two lists are never compared with ``<=`` to ask whether one holds the other.

``list <= list`` is alphabetical order, not "is contained in" (that is a
set's). ``belief_extractor.held_facts`` asked ``wanted <= words`` of two
``query_terms.content_terms``, which answers a LIST: the deep pass was shown
the facts of whoever's name sorted before the turn's first word, and not the
facts of the person the turn named (the memory plan's W6, step 1, live until
2026-11-25). It read right, ran without an error and passed its own gate.

So every ``<``, ``<=``, ``>``, ``>=`` in the packages is read from source:
neither side may be a list -- a list display or comprehension, ``sorted()``,
``.split()``, ``.findall()``, a parameter or a name annotated as a list, or the
answer of a function annotated to return one (``content_terms``,
``search_terms`` and the others, found by their annotation). A
tuple may be: that is how a version or a key is ordered.
"""

from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_ORDER = (ast.Lt, ast.LtE, ast.Gt, ast.GtE)
_LIST_METHODS = frozenset({"split", "rsplit", "splitlines", "findall", "readlines", "fetchall"})
_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)


def _sources() -> list[Path]:
    listed = subprocess.run(["git", "ls-files", "kazma-*/**/*.py", "scripts/*.py"], cwd=ROOT, capture_output=True,
                            text=True, check=True).stdout.split()
    return [ROOT / p for p in listed]


def _says_list(annotation: ast.expr | None) -> bool:
    if annotation is None:
        return False
    text = ast.unparse(annotation).strip("'\"")
    return text == "list" or text.startswith(("list[", "List["))


def _answers_a_list(trees: dict[Path, ast.Module]) -> set[str]:
    """The functions annotated to return a list, unless another function of
    the same name, or a method of Python's own (``str.find``), answers
    something else."""
    lists: set[str] = set()
    others: set[str] = {name for kind in (str, bytes, dict, set, re.Pattern) for name in dir(kind)}
    for tree in trees.values():
        for node in ast.walk(tree):
            if isinstance(node, _FUNCTIONS):
                (lists if _says_list(node.returns) else others).add(node.name)
    return lists - others


def _own_nodes(scope: ast.AST):
    """The nodes of *scope* itself, not of a function inside it."""
    todo = list(ast.iter_child_nodes(scope))
    while todo:
        node = todo.pop()
        yield node
        if not isinstance(node, (*_FUNCTIONS, ast.Lambda, ast.ClassDef)):
            todo.extend(ast.iter_child_nodes(node))


def _list_compares(tree: ast.Module, answering: set[str]) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []

    def is_list(node: ast.expr, names: set[str]) -> bool:
        if isinstance(node, (ast.List, ast.ListComp)):
            return True
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                return node.func.id in {"sorted", "list"} or node.func.id in answering
            return isinstance(node.func, ast.Attribute) and (
                node.func.attr in _LIST_METHODS or node.func.attr in answering)
        if isinstance(node, ast.BoolOp):
            return any(is_list(v, names) for v in node.values)
        return isinstance(node, ast.Name) and node.id in names

    def read(scope: ast.AST) -> None:
        names: set[str] = set()
        other: set[str] = set()
        if isinstance(scope, _FUNCTIONS):
            a = scope.args
            names |= {p.arg for p in (*a.posonlyargs, *a.args, *a.kwonlyargs) if _says_list(p.annotation)}
        for _ in range(2):  # a name given another name's list
            for node in _own_nodes(scope):
                if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and _says_list(node.annotation):
                    names.add(node.target.id)
                elif isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            (names if is_list(node.value, names) else other).add(target.id)
        certain = names - other
        for node in _own_nodes(scope):
            if isinstance(node, _FUNCTIONS):
                read(node)
            elif isinstance(node, ast.ClassDef):
                read(node)
            elif isinstance(node, ast.Lambda):
                read(node)
            elif isinstance(node, ast.Compare):
                sides = [node.left, *node.comparators]
                for at, op in enumerate(node.ops):
                    if isinstance(op, _ORDER) and (is_list(sides[at], certain) or is_list(sides[at + 1], certain)):
                        found.append((node.lineno, ast.unparse(node)))

    read(tree)
    return found


def test_no_two_lists_are_put_in_order_to_ask_if_one_holds_the_other():
    trees = {p: ast.parse(p.read_text(encoding="utf-8")) for p in _sources()}
    assert len(trees) > 500, "the packages' files were not found"
    answering = _answers_a_list(trees)
    assert {"content_terms", "search_terms"} <= answering
    found = [f"{p.relative_to(ROOT).as_posix()}:{line}: {text}"
             for p, tree in trees.items() for line, text in _list_compares(tree, answering)]
    assert found == [], "compare sets, or say what the order means with a tuple:\n" + "\n".join(found)


@pytest.mark.parametrize("planted", [
    # What was live: two lists of words.
    "def held(text, subject):\n words = content_terms(text)\n wanted = content_terms(subject)\n return wanted and wanted <= words\n",
    "def f(text, words: list[str]):\n return text.split() <= words\n",
    "def f(a, b):\n return q.content_terms(a) < set(b)\n",
    "def f(a, b):\n kept: list[str] = []\n def g():\n  said = sorted(b)\n  return said >= a\n return g\n",
])
def test_control_a_planted_comparison_is_found(planted):
    assert _list_compares(ast.parse(planted), {"content_terms"})


@pytest.mark.parametrize("fine", [
    "def f(a, b):\n return set(content_terms(a)) <= set(content_terms(b))\n",
    "def f(a, b):\n terms = len(content_terms(a))\n return terms <= b\n",
    "def f(a, b):\n def g():\n  late = [1]\n  return late\n late = b - a\n return late < 120\n",
    "def f(a, b):\n return version_key(a) > version_key(b)\n",
])
def test_control_sets_counts_and_keys_are_not(fine):
    assert _list_compares(ast.parse(fine), {"content_terms"}) == []
