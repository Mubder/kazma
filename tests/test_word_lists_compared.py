"""Two lists are never compared with ``<=`` to ask whether one holds the other.

``list <= list`` is alphabetical order, not "is contained in" (that is a
set's). ``belief_extractor.held_facts`` asked ``wanted <= words`` of two
``query_terms.content_terms``, which answers a LIST: the deep pass was shown
the facts of whoever's name sorted before the turn's first word, and not the
facts of the person the turn named (the memory plan's W6, step 1, live until
2026-11-25). It read right, ran without an error and passed its own gate.

So every ``<``, ``<=``, ``>``, ``>=`` in the packages -- the sign, its
``operator`` function or its method -- is read from source: neither side may
be a list. A list is a display or comprehension, ``sorted()``
or ``list()``, ``.split()`` and its like, the answer of a function annotated to
return one, a name, a field or a parameter annotated as one (``Sequence``
too) or given one -- and what is made of it: a slice, a sum, a copy, either
arm of an ``if`` expression, ``await``, ``:=``, an unpacked pair, the tuple of
it, one of a dict's or a list's lists, a parameter's default. A function is the
one its file defines or imports, under whatever name; a name the one its scope
or an enclosing one gives. The reviews of step 1 planted spellings of the live
line that passed the gate as it was then, and those spellings, rewritten here,
are its controls. What it cannot see: a list that comes through a function
with no annotation, another file's field, or a container built up by calls. A
tuple of its own may be ordered: that is a version or a key.
"""

from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_ORDER = (ast.Lt, ast.LtE, ast.Gt, ast.GtE)
_ORDER_NAMES = frozenset({"lt", "le", "gt", "ge"})
_ORDER_METHODS = frozenset(f"__{name}__" for name in _ORDER_NAMES)
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


def _says_lists(annotation: ast.expr | None) -> bool:
    """Whether *annotation* is a container's of lists (``dict[str, list[str]]``)."""
    if not isinstance(annotation, ast.Subscript):
        return False
    inner = annotation.slice.elts if isinstance(annotation.slice, ast.Tuple) else [annotation.slice]
    return ast.unparse(annotation.value) in (*_LISTS, "dict", "Mapping", "tuple") and _says_list(inner[-1])


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
            if isinstance(node, ast.ImportFrom) and (node.module or node.level):
                package = module.split(".")[:-node.level] if node.level else []
                source = ".".join([*package, *([node.module] if node.module else [])])
                for a in node.names:
                    self.imported[a.asname or a.name] = (source, a.name)
                    self.modules[a.asname or a.name] = f"{source}.{a.name}"
            elif isinstance(node, ast.Import):
                for a in node.names:
                    self.modules[a.asname or a.name] = a.name
        self.fields: set[str] = set()
        self.classes = {n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
        #: a parameter annotated as one of this file's classes: its fields are read
        self.typed: set[str] = set()
        #: names of containers whose values are lists
        self.holders: set[str] = set()
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
                of = node.args[0] if node.args else None
                if isinstance(of, ast.GeneratorExp) and len(of.generators) == 1:  # tuple(w for w in words)
                    over = of.generators[0]
                    return ast.unparse(of.elt) == ast.unparse(over.target) and is_list(over.iter)
                return of is not None and is_list(of)
            if isinstance(func, ast.Attribute) and func.attr == "get" and self._holds_lists(func.value, is_list):
                return True
            if isinstance(func, ast.Attribute) and func.attr == "copy" and is_list(func.value):
                return True
            if isinstance(func, ast.Attribute) and ast.unparse(func) in ("asyncio.to_thread", "copy.copy",
                                                                         "copy.deepcopy"):
                return bool(node.args) and (self._answers(node.args[0], aliases) if func.attr == "to_thread"
                                            else is_list(node.args[0]))
            return self._answers(func, aliases)
        if isinstance(node, ast.Subscript):
            if not isinstance(node.slice, ast.Slice):
                return self._holds_lists(node.value, is_list)
            return is_list(node.value)
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

    def _holds_lists(self, node: ast.expr, is_list) -> bool:
        """Whether *node* is a container whose values are lists."""
        if isinstance(node, ast.Dict):
            return bool(node.values) and all(is_list(v) for v in node.values)
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            return bool(node.elts) and all(is_list(v) for v in node.elts)
        if isinstance(node, ast.ListComp):
            return is_list(node.elt)
        if isinstance(node, ast.DictComp):
            return is_list(node.value)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "values":
            return self._holds_lists(node.func.value, is_list)
        return isinstance(node, ast.Name) and node.id in self.holders

    def _read(self, scope: ast.AST, around: set[str]) -> None:
        """One scope's comparisons, then those of the scopes inside it; what
        its names are is not known outside it."""
        holders, typed = set(self.holders), set(self.typed)
        self._read_scope(scope, around)
        self.holders, self.typed = holders, typed

    def _read_scope(self, scope: ast.AST, around: set[str]) -> None:
        names: set[str] = set()
        other: set[str] = set()
        aliases: dict[str, bool] = {}
        if isinstance(scope, (*_FUNCTIONS, ast.Lambda)):
            a = scope.args
            for p in (*a.posonlyargs, *a.args, *a.kwonlyargs, *(x for x in (a.vararg, a.kwarg) if x)):
                (names if p not in (a.vararg, a.kwarg) and _says_list(p.annotation) else other).add(p.arg)
                if _says_lists(p.annotation):
                    self.holders.add(p.arg)
            placed = [*a.posonlyargs, *a.args]
            for p, default in [*zip(placed[len(placed) - len(a.defaults):], a.defaults, strict=True),
                               *zip(a.kwonlyargs, a.kw_defaults, strict=True)]:
                if p.annotation is None and default is not None and self._is_list(default, set(around), {}):
                    names.add(p.arg)
            other -= names
            self.typed |= {p.arg for p in (*a.posonlyargs, *a.args, *a.kwonlyargs)
                           if p.annotation is not None and ast.unparse(p.annotation).strip("'\"") in self.classes}
        given: list[tuple[ast.expr, ast.expr]] = []
        loops: list[tuple[ast.expr, ast.expr]] = []
        for node in _own_nodes(scope):
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                (names if _says_list(node.annotation) else other).add(node.target.id)
                if _says_lists(node.annotation):
                    self.holders.add(node.target.id)
            elif isinstance(node, ast.Assign):
                given += [pair for target in node.targets for pair in self._pairs(target, node.value)]
            elif isinstance(node, ast.NamedExpr):
                given.append((node.target, node.value))
            elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
                loops.append((node.target, node.iter))
        # ``x = None`` before a branch gives a list: not another kind of value.
        given = [(t, v) for t, v in given if not (isinstance(v, ast.Constant) and v.value is None)
                 or not isinstance(t, ast.Name) or t.id.startswith("__unpacked")]
        looped = {n.id for target, _i in loops for n in ast.walk(target) if isinstance(n, ast.Name)}
        rebound = names | other | looped | {t.id for t, _v in given if isinstance(t, ast.Name)}
        seen = around - rebound
        self.holders -= {t.id for t, _v in given if isinstance(t, ast.Name)} | looped
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

        def is_list(node: ast.expr) -> bool:
            return self._is_list(node, certain, aliases)

        for target, value in given:
            if isinstance(target, ast.Name) and self._holds_lists(value, is_list):
                self.holders.add(target.id)
        over: dict[str, list[bool]] = {}  # a loop's name: over lists, in each loop that uses it
        for target, iterated in loops:
            last = target.elts[-1] if isinstance(target, ast.Tuple) and target.elts else target
            items = (isinstance(iterated, ast.Call) and isinstance(iterated.func, ast.Attribute)
                     and iterated.func.attr == "items" and self._holds_lists(iterated.func.value, is_list))
            lists = items or (last is target and self._holds_lists(iterated, is_list))
            for n in ast.walk(target):
                if isinstance(n, ast.Name):
                    over.setdefault(n.id, []).append(lists and n is last)
        certain = (certain - looped) | {name for name, each in over.items() if all(each)}
        for node in _own_nodes(scope):
            if isinstance(node, _SCOPES):
                self._read(node, set(around) if isinstance(scope, ast.ClassDef) else certain)
            elif isinstance(node, ast.Compare):
                sides = [node.left, *node.comparators]
                for at, op in enumerate(node.ops):
                    if isinstance(op, _ORDER) and (is_list(sides[at]) or is_list(sides[at + 1])):
                        self.found.append((node.lineno, ast.unparse(node)))
            elif isinstance(node, ast.Call) and self._order_call(node.func):
                sides = [*node.args[:2], *([node.func.value] if len(node.args) == 1 else [])]
                if any(is_list(s) for s in sides):
                    self.found.append((node.lineno, ast.unparse(node)))

    def _order_call(self, func: ast.expr) -> bool:
        """``operator.le``, however it is named here, or ``x.__le__``."""
        if isinstance(func, ast.Name):
            module, name = self.imported.get(func.id, ("", ""))
            return module == "operator" and name in _ORDER_NAMES
        if not isinstance(func, ast.Attribute):
            return False
        if isinstance(func.value, ast.Name) and self.modules.get(func.value.id) == "operator":
            return func.attr in _ORDER_NAMES
        return func.attr in _ORDER_METHODS

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
    # The second review's.
    _FROM + "def f(a, b):\n by = {'a': content_terms(a), 'b': content_terms(b)}\n return by['a'] <= by['b']\n",
    "def f(by: dict[str, list[str]], b):\n return by['a'] <= b\n",
    "def f(by: dict[str, list[str]], b):\n return by.get('a') <= b\n",
    "def f(by: dict[str, list[str]], b):\n return [k for k, words in by.items() if words <= b]\n",
    _FROM + "def f(a, b, c):\n for words in (content_terms(a), content_terms(b)):\n  if words <= c:\n   return words\n",
    _FROM + "def f(a, b):\n pair = [content_terms(a), content_terms(b)]\n return pair[0] <= pair[1]\n",
    "def f(all_words: list[list[str]], b):\n return any(words <= b for words in all_words)\n",
    "import operator\n" + _FROM + "def f(a, b):\n return operator.le(content_terms(a), b)\n",
    "from operator import ge as holds\n" + _FROM + "def f(a, b):\n return holds(b, content_terms(a))\n",
    _FROM + "def f(a, b):\n return content_terms(a).__le__(b)\n",
    _FROM + "def f(a, b):\n return b.__ge__(content_terms(a))\n",
    "def f(b, words=['a', 'b']):\n return words <= b\n",
    _FROM + "def f(a, b):\n words = None\n if a:\n  words = content_terms(a)\n return words <= b\n",
    _FROM + "def f(a, b):\n return tuple(w for w in content_terms(a)) <= tuple(b)\n",
])
def test_control_a_planted_comparison_is_found(planted):
    assert _list_compares(planted)


def test_control_a_relative_import_is_the_function_it_names():
    planted = "from .query_terms import content_terms as ct\ndef f(a, b):\n return ct(a) <= b\n"
    known = _Known({"kazma_core.memory.query_terms": ast.parse(_TERMS),
                    "kazma_core.other": ast.parse("def content_terms(a) -> int: ...\n")})
    assert _list_compares(planted, known, "kazma_core.memory.held_facts")
    assert not _list_compares(planted.replace(".query_terms", "..other"), known, "kazma_core.memory.held_facts")


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
    "def f(a, b):\n x = None\n if a:\n  x = len(a)\n return x <= b\n",
    "def f(by: dict[str, int], b):\n return by['a'] <= b and any(v <= b for v in by.values())\n",
    "import operator\ndef f(a, b):\n return operator.le(len(a), b)\n",
    "def f(b, most=3):\n return most <= b\n",
])
def test_control_sets_counts_and_keys_are_not(fine):
    assert _list_compares(fine) == []
