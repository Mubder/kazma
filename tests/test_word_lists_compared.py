"""Two lists are never compared with ``<=`` to ask whether one holds the other.

``list <= list`` is alphabetical order, not "is contained in" (that is a
set's). ``belief_extractor.held_facts`` asked ``wanted <= words`` of two
``query_terms.content_terms``, which answers a LIST: the deep pass was shown
the facts of whoever's name sorted before the turn's first word, and not the
facts of the person the turn named (the memory plan's W6, step 1, live until
2026-11-25). It read right, ran without an error and passed its own gate.

So every ``<``, ``<=``, ``>``, ``>=`` in the packages is read from source:
neither side may be a list. A list is a display or comprehension, ``sorted()``
or ``list()``, ``.split()`` and its like, the answer of a function annotated to
return one, a name, a field or a parameter annotated as one (``Sequence``
too) or given one -- and what is made of it: a slice, a sum, a copy, either
arm of an ``if`` expression, ``await``, ``:=``, an unpacked pair, the tuple of
it. A function is the one its file defines or imports, under whatever name; a
name the one its scope or an enclosing one gives. The review of step 1 planted
27 spellings of the live line and 19 passed the gate as it first was: they are
its controls. A tuple of its own may be ordered: that is a version or a key.
"""

from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_ORDER = (ast.Lt, ast.LtE, ast.Gt, ast.GtE)
_LIST_METHODS = frozenset({"split", "rsplit", "splitlines", "findall", "readlines", "fetchall", "fetchmany"})
_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
_SCOPES = (*_FUNCTIONS, ast.Lambda, ast.ClassDef)
_LISTS = ("list", "List", "Sequence", "MutableSequence", "typing.List", "typing.Sequence", "collections.abc.Sequence")


def _sources() -> list[Path]:
    listed = subprocess.run(["git", "ls-files", "kazma-*/**/*.py", "scripts/*.py"], cwd=ROOT, capture_output=True,
                            text=True, check=True).stdout.split()
    return [ROOT / p for p in listed]


def _says_list(annotation: ast.expr | None) -> bool:
    """Whether *annotation* is a list's (``list[str] | None`` too)."""
    if annotation is None:
        return False
    if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
        try:
            annotation = ast.parse(annotation.value, mode="eval").body
        except SyntaxError:
            return False
    if isinstance(annotation, ast.BinOp):
        return _says_list(annotation.left) or _says_list(annotation.right)
    if isinstance(annotation, ast.Subscript):
        name = ast.unparse(annotation.value)
        if name in ("Optional", "typing.Optional", "Annotated", "Final", "ClassVar"):
            inner = annotation.slice
            return _says_list(inner.elts[0] if isinstance(inner, ast.Tuple) else inner)
        return name in _LISTS
    return ast.unparse(annotation) in _LISTS


def _module_of(path: Path) -> str:
    parts = list(path.relative_to(ROOT).with_suffix("").parts)
    parts = parts[1:] if len(parts) > 1 else parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


class _Known:
    """Which functions of the packages answer a list: by module, and (for a
    method, or a name that came some other way) by a name no other function,
    nor a method of Python's own, shares with another answer."""

    def __init__(self, trees: dict[str, ast.Module]) -> None:
        self.by_module: dict[str, dict[str, bool]] = {}
        lists: set[str] = set()
        others: set[str] = {name for kind in (str, bytes, dict, set, re.Pattern) for name in dir(kind)}
        for module, tree in trees.items():
            own = self.by_module.setdefault(module, {})
            for node in ast.walk(tree):
                if isinstance(node, _FUNCTIONS):
                    says = _says_list(node.returns)
                    own[node.name] = says and own.get(node.name, True)
                    (lists if says else others).add(node.name)
        self.anywhere = lists - others


def _own_nodes(scope: ast.AST):
    """The nodes of *scope* itself, not of a function inside it."""
    todo = list(ast.iter_child_nodes(scope))
    while todo:
        node = todo.pop()
        yield node
        if not isinstance(node, _SCOPES):
            todo.extend(ast.iter_child_nodes(node))


class _Reader:
    """One module's order comparisons with a list."""

    def __init__(self, tree: ast.Module, known: _Known, module: str = "") -> None:
        self.known = known
        self.own = known.by_module.get(module, {})
        self.found: list[tuple[int, str]] = []
        #: name here -> (module, its name there) / module alias -> module
        self.imported: dict[str, tuple[str, str]] = {}
        self.modules: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and not node.level:
                for a in node.names:
                    self.imported[a.asname or a.name] = (node.module, a.name)
                    self.modules[a.asname or a.name] = f"{node.module}.{a.name}"
            elif isinstance(node, ast.Import):
                for a in node.names:
                    self.modules[a.asname or a.name] = a.name
        self.fields: set[str] = set()
        self.classes = {n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
        #: a parameter annotated as one of this file's classes: its fields are read
        self.typed: set[str] = set()
        self.fields = self._fields(tree)
        self._read(tree, set())

    def _fields(self, tree: ast.Module) -> set[str]:
        """The attributes this file annotates as a list, or only ever gives one."""
        lists: set[str] = set()
        other: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                for item in node.body:
                    if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                        (lists if _says_list(item.annotation) else other).add(item.target.id)
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Attribute):
                (lists if _says_list(node.annotation) else other).add(node.target.attr)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Attribute):
                        (lists if self._is_list(node.value, set(), {}) else other).add(target.attr)
        return lists - other

    def _answers(self, func: ast.expr, aliases: dict[str, bool]) -> bool:
        if isinstance(func, ast.Name):
            if func.id in aliases:
                return aliases[func.id]
            if func.id in self.own:
                return self.own[func.id]
            if func.id in self.imported:
                module, name = self.imported[func.id]
                return self._of(module, name)
            return func.id in {"sorted", "list"} or func.id in self.known.anywhere
        if isinstance(func, ast.Attribute):
            if func.attr in _LIST_METHODS:
                return True
            if isinstance(func.value, ast.Name) and func.value.id in self.modules:
                return self._of(self.modules[func.value.id], func.attr)
            if isinstance(func.value, ast.Name) and func.value.id in ("self", "cls") and func.attr in self.own:
                return self.own[func.attr]
            return func.attr in self.known.anywhere
        return False

    def _of(self, module: str, name: str) -> bool:
        there = self.known.by_module.get(module)
        if there is None or name not in there:
            return name in self.known.anywhere
        return there[name]

    def _is_list(self, node: ast.expr, names: set[str], aliases: dict[str, bool]) -> bool:
        def is_list(n: ast.expr) -> bool:
            return self._is_list(n, names, aliases)

        if isinstance(node, (ast.List, ast.ListComp)):
            return True
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "tuple":
                return bool(node.args) and is_list(node.args[0])
            if isinstance(func, ast.Attribute) and func.attr == "copy" and is_list(func.value):
                return True
            if isinstance(func, ast.Attribute) and ast.unparse(func) in ("asyncio.to_thread", "copy.copy",
                                                                         "copy.deepcopy"):
                return bool(node.args) and (self._answers(node.args[0], aliases) if func.attr == "to_thread"
                                            else is_list(node.args[0]))
            return self._answers(func, aliases)
        if isinstance(node, ast.Subscript):
            return isinstance(node.slice, ast.Slice) and is_list(node.value)
        if isinstance(node, ast.BinOp):
            return isinstance(node.op, (ast.Add, ast.Mult)) and (is_list(node.left) or is_list(node.right))
        if isinstance(node, ast.BoolOp):
            return any(is_list(v) for v in node.values)
        if isinstance(node, ast.IfExp):
            return is_list(node.body) or is_list(node.orelse)
        if isinstance(node, (ast.Await, ast.NamedExpr, ast.Starred)):
            return is_list(node.value)
        if isinstance(node, ast.Attribute):
            return (node.attr in self.fields and isinstance(node.value, ast.Name)
                    and (node.value.id in ("self", "cls") or node.value.id in self.typed))
        return isinstance(node, ast.Name) and node.id in names

    def _read(self, scope: ast.AST, around: set[str]) -> None:
        names: set[str] = set()
        other: set[str] = set()
        aliases: dict[str, bool] = {}
        if isinstance(scope, (*_FUNCTIONS, ast.Lambda)):
            a = scope.args
            for p in (*a.posonlyargs, *a.args, *a.kwonlyargs, *(x for x in (a.vararg, a.kwarg) if x)):
                (names if p not in (a.vararg, a.kwarg) and _says_list(p.annotation) else other).add(p.arg)
            other -= names
            self.typed |= {p.arg for p in (*a.posonlyargs, *a.args, *a.kwonlyargs)
                           if p.annotation is not None and ast.unparse(p.annotation).strip("'\"") in self.classes}
        given: list[tuple[ast.expr, ast.expr]] = []
        for node in _own_nodes(scope):
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                (names if _says_list(node.annotation) else other).add(node.target.id)
            elif isinstance(node, ast.Assign):
                given += [pair for target in node.targets for pair in self._pairs(target, node.value)]
            elif isinstance(node, ast.NamedExpr):
                given.append((node.target, node.value))
            elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
                other |= {n.id for n in ast.walk(node.target) if isinstance(n, ast.Name)}
        rebound = names | other | {t.id for t, _v in given if isinstance(t, ast.Name)}
        seen = around - rebound
        for _ in range(3):  # a name given another name's list
            for target, value in given:
                if isinstance(target, ast.Name):
                    (names if self._is_list(value, names | seen, aliases) else other).add(target.id)
                    if isinstance(value, (ast.Name, ast.Attribute)) and self._answers(value, aliases):
                        aliases[target.id] = True
            other -= {t.id for t, v in given if isinstance(t, ast.Name) and self._is_list(v, names | seen, aliases)
                      and not any(isinstance(t2, ast.Name) and t2.id == t.id
                                  and not self._is_list(v2, names | seen, aliases) for t2, v2 in given)
                      and t.id not in {p.arg for p in self._unlisted_params(scope)}}
        certain = (names - other) | seen
        for node in _own_nodes(scope):
            if isinstance(node, _SCOPES):
                self._read(node, set(around) if isinstance(scope, ast.ClassDef) else certain)
            elif isinstance(node, ast.Compare):
                sides = [node.left, *node.comparators]
                for at, op in enumerate(node.ops):
                    if isinstance(op, _ORDER) and (self._is_list(sides[at], certain, aliases)
                                                   or self._is_list(sides[at + 1], certain, aliases)):
                        self.found.append((node.lineno, ast.unparse(node)))

    @staticmethod
    def _unlisted_params(scope: ast.AST) -> list[ast.arg]:
        if not isinstance(scope, (*_FUNCTIONS, ast.Lambda)):
            return []
        a = scope.args
        return [p for p in (*a.posonlyargs, *a.args, *a.kwonlyargs, *(x for x in (a.vararg, a.kwarg) if x))
                if not _says_list(p.annotation)]

    @staticmethod
    def _pairs(target: ast.expr, value: ast.expr) -> list[tuple[ast.expr, ast.expr]]:
        """``a, b = x, y`` as two: what each name is given."""
        if isinstance(target, (ast.Tuple, ast.List)):
            if isinstance(value, (ast.Tuple, ast.List)) and len(value.elts) == len(target.elts):
                return [p for t, v in zip(target.elts, value.elts, strict=True) for p in _Reader._pairs(t, v)]
            # A starred name takes a list whatever is unpacked; another, an unknown.
            unknown = ast.Constant(value=None)
            return [(t.value, ast.List(elts=[], ctx=ast.Load())) if isinstance(t, ast.Starred) else (t, unknown)
                    for t in target.elts]
        return [(target, value)]


def _list_compares(source: str, known: _Known | None = None, module: str = "") -> list[tuple[int, str]]:
    tree = ast.parse(source)
    return _Reader(tree, known or _Known({"": tree, "kazma_core.memory.query_terms": ast.parse(_TERMS)}), module).found


_TERMS = "def content_terms(text: str) -> list[str]: ...\ndef search_terms(text: str) -> list[str]: ...\n"


def test_no_two_lists_are_put_in_order_to_ask_if_one_holds_the_other():
    sources = {p: p.read_text(encoding="utf-8") for p in _sources()}
    assert len(sources) > 500, "the packages' files were not found"
    trees = {_module_of(p): ast.parse(s) for p, s in sources.items()}
    known = _Known(trees)
    assert known.by_module["kazma_core.memory.query_terms"]["content_terms"] is True
    found = [f"{p.relative_to(ROOT).as_posix()}:{line}: {text}"
             for p, source in sources.items() for line, text in _list_compares(source, known, _module_of(p))]
    assert found == [], "compare sets, or say what the order means with a tuple:\n" + "\n".join(found)


_FROM = "from kazma_core.memory.query_terms import content_terms\n"


@pytest.mark.parametrize("planted", [
    # What was live: two lists of words.
    _FROM + "def held(text, subject):\n words = content_terms(text)\n wanted = content_terms(subject)\n"
            " return wanted and wanted <= words\n",
    "def f(text, words: list[str]):\n return text.split() <= words\n",
    "import q\ndef f(a, b):\n return q.content_terms(a) < set(b)\n",
    "def f(a, b):\n kept: list[str] = []\n def g():\n  said = sorted(b)\n  return said >= a\n return g\n",
    # The review's nineteen, as it named them, and their near neighbours.
    _FROM + "def f(a, b):\n return content_terms(a)[:20] <= content_terms(b)[:20]\n",
    _FROM + "def f(a, b, c):\n return content_terms(a) + content_terms(b) >= c\n",
    _FROM + "def f(a, b):\n x, y = content_terms(a), content_terms(b)\n return x <= y\n",
    _FROM + "def f(a, b):\n (x, (y, z)) = (a, (b, content_terms(b)))\n return z <= x\n",
    "def f(a, b):\n first, *rest = a\n return rest <= b\n",
    _FROM + "def f(a, b):\n x = content_terms(a) if a else []\n return x <= b\n",
    _FROM + "def f(a, b):\n return (content_terms(a) if a else b) <= b\n",
    "from kazma_core.memory.query_terms import content_terms as ct\ndef f(a, b):\n return ct(a) <= b\n",
    "from kazma_core.memory import query_terms as qt\ndef f(a, b):\n return qt.content_terms(a) <= b\n",
    "import kazma_core.memory.query_terms as qt\ndef f(a, b):\n return b > qt.search_terms(a)\n",
    _FROM + "def f(a, b):\n terms = content_terms\n return terms(a) <= b\n",
    _FROM + "def f(a, b):\n words = content_terms(a)\n def g():\n  return words <= b\n return g\n",
    "WORDS = ['a', 'b']\ndef f(b):\n return WORDS <= b\n",
    _FROM + "def f(a):\n words = content_terms(a)\n return lambda b: words <= b\n",
    _FROM + "class C:\n def __init__(self, a):\n  self.words = content_terms(a)\n def f(self, b):\n  return self.words <= b\n",
    "class C:\n words: list[str]\ndef f(c: C, b):\n return c.words <= b\n",
    "class C:\n def __init__(self):\n  self.words: list[str] = []\n def f(self, b):\n  return b >= self.words\n",
    "from collections.abc import Sequence\ndef f(a: Sequence[str], b: Sequence[str]):\n return a <= b\n",
    "def f(a: list[str] | None, b):\n return a <= b\n",
    "def f(a: 'list[str]', b):\n return a <= b\n",
    _FROM + "def f(a, b):\n return tuple(content_terms(a)) <= tuple(content_terms(b))\n",
    _FROM + "def f(a, b):\n return (w := content_terms(a)) and w <= b\n",
    _FROM + "def f(a, b):\n return (w := content_terms(a)) <= b\n",
    _FROM + "def f(a, b):\n return content_terms(a).copy() <= b\n",
    "import asyncio\n" + _FROM + "async def f(a, b):\n return await asyncio.to_thread(content_terms, a) <= b\n",
    "async def words(a) -> list[str]: ...\nasync def f(a, b):\n return await words(a) <= b\n",
    # A function of this name elsewhere answers something else: this file's is meant.
    "def content_terms(a) -> list[str]: ...\ndef f(a, b):\n return content_terms(a) <= b\n",
    "class C:\n def words(self) -> list[str]: ...\n def f(self, b):\n  return self.words() <= b\n",
])
def test_control_a_planted_comparison_is_found(planted):
    assert _list_compares(planted)


@pytest.mark.parametrize("fine", [
    _FROM + "def f(a, b):\n return set(content_terms(a)) <= set(content_terms(b))\n",
    _FROM + "def f(a, b):\n terms = len(content_terms(a))\n return terms <= b\n",
    "def f(a, b):\n def g():\n  late = [1]\n  return late\n late = b - a\n return late < 120\n",
    "def f(a, b):\n return version_key(a) > version_key(b)\n",
    "def f(a, b):\n return tuple(int(x) for x in a.split('.')) < (3, 11)\n",
    "def f(a, b):\n return a.split('.')[0] < b\n",
    "def f(a, b):\n words = [1]\n def g(words):\n  return words <= b\n return g\n",
    "def f(a, b):\n return [1, 2].count(a) <= b\n",
    "def content_terms(a) -> int: ...\ndef f(a, b):\n return content_terms(a) <= b\n",
    _FROM + "def f(a, b):\n for w in content_terms(a):\n  if w <= b:\n   return w\n",
])
def test_control_sets_counts_and_keys_are_not(fine):
    assert _list_compares(fine) == []
