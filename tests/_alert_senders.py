"""Every alert each sender can raise, read from the senders' own source.

Shared by the alert catalog's gates (tests/test_alert_catalog.py). Four
senders, four readers:

* Kazma's ops alerts: every call of ``ops_alerts.alert`` in the product
  packages, its key resolved through f-strings, ``a if c else b``, local
  assignments and the module's own wrappers (``_alert(key, ...)``: a
  function whose key is one of its parameters is followed to its callers).
  A key the resolver cannot read is "unresolved" and must be declared by the
  gate with where its values come from.
* The lifecycle messages: the event table of ``lifecycle_notifier``.
* The guard: the ``kind=`` of every ``_page(...)`` call in kazma_guard.py.
* Kazma Watch: the conditions ``diagnose.js`` returns, and the recovery page
  ``incidents.js`` sends.

A key is a pattern: ``*`` stands for a part only known at run time
(``llm.provider_unreachable.*``).
"""

from __future__ import annotations

import ast
import itertools
import re
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ALERT_MODULE = "kazma_core.observability.ops_alerts"
GUARD = REPO / "scripts" / "service" / "kazma_guard.py"
OBSERVER_SRC = REPO / "deploy" / "observer" / "src"
LIFECYCLE = REPO / "kazma-core" / "kazma_core" / "lifecycle_notifier.py"

WILD = "\x00wild"


@dataclass(frozen=True)
class Param:
    name: str


@dataclass
class AlertSite:
    """One place an ops alert is raised."""

    rel: str
    line: int
    function: str
    #: The keys it can raise, as patterns; None when the key is not readable.
    patterns: tuple[str, ...] | None
    #: The literal severity of a direct call with a literal key, else None.
    severity: str | None = None
    direct: bool = True
    notes: list[str] = field(default_factory=list)


# -- the ops alerts ----------------------------------------------------------


def product_files(root: Path = REPO) -> list[Path]:
    out = []
    for pkg in sorted(root.glob("kazma-*/kazma_*")):
        if not pkg.is_dir():
            continue
        for path in sorted(pkg.rglob("*.py")):
            if {"tests", "_tests", "__pycache__"} & set(path.parts):
                continue
            out.append(path)
    return out


def _alert_refs(tree: ast.AST) -> tuple[set[str], set[str]]:
    """Names bound to ``alert`` and to the ``ops_alerts`` module."""
    funcs: set[str] = set()
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module == ALERT_MODULE:
                funcs |= {a.asname or a.name for a in node.names if a.name == "alert"}
            elif node.module == "kazma_core.observability":
                mods |= {a.asname or a.name for a in node.names if a.name == "ops_alerts"}
        elif isinstance(node, ast.Import):
            mods |= {a.asname or a.name for a in node.names if a.name == ALERT_MODULE}
    return funcs, mods


def _is_alert(node: ast.AST, funcs: set[str], mods: set[str]) -> bool:
    if isinstance(node, ast.Name):
        return node.id in funcs
    return (isinstance(node, ast.Attribute) and node.attr == "alert"
            and ast.unparse(node.value) in mods)


def _ref_name(node: ast.AST) -> str | None:
    """``f`` for ``f`` / ``self.f`` / ``cls.f``."""
    if isinstance(node, ast.Name):
        return node.id
    if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
            and node.value.id in ("self", "cls")):
        return node.attr
    return None


class _Scope:
    def __init__(self, func: ast.AST | None) -> None:
        self.func = func
        self.params: list[str] = []
        self.assigned: dict[str, list[ast.AST]] = {}
        if isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            a = func.args
            self.params = [p.arg for p in (*a.posonlyargs, *a.args, *a.kwonlyargs)]
            for node in ast.walk(func):
                if (isinstance(node, ast.Assign) and len(node.targets) == 1
                        and isinstance(node.targets[0], ast.Name)):
                    self.assigned.setdefault(node.targets[0].id, []).append(node.value)

    @property
    def name(self) -> str:
        return getattr(self.func, "name", "<module>")


def _evaluate(expr: ast.AST, scope: _Scope, depth: int = 0) -> list[tuple] | None:
    """The templates *expr* can take: tuples of literal text, Param, WILD."""
    if depth > 8:
        return None
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        return [(expr.value,)]
    if isinstance(expr, ast.JoinedStr):
        parts = []
        for value in expr.values:
            if isinstance(value, ast.Constant):
                parts.append([(str(value.value),)])
            else:
                inner = value.value
                got = _evaluate(inner, scope, depth + 1) if isinstance(inner, ast.Name) else None
                parts.append(got or [(WILD,)])
        return [tuple(itertools.chain.from_iterable(c)) for c in itertools.product(*parts)]
    if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Add):
        left = _evaluate(expr.left, scope, depth + 1)
        right = _evaluate(expr.right, scope, depth + 1)
        if left is None and right is None:
            return None
        return [a + b for a in (left or [(WILD,)]) for b in (right or [(WILD,)])]
    if isinstance(expr, ast.IfExp):
        body = _evaluate(expr.body, scope, depth + 1)
        other = _evaluate(expr.orelse, scope, depth + 1)
        return None if body is None or other is None else body + other
    if isinstance(expr, ast.Name):
        if expr.id in scope.assigned:
            got = [_evaluate(v, scope, depth + 1) for v in scope.assigned[expr.id]]
            if all(g is not None for g in got):
                return [t for g in got for t in g]
            return None
        if expr.id in scope.params:
            return [(Param(expr.id),)]
    return None


def _render(template: tuple) -> str:
    text = "".join("*" if p == WILD or isinstance(p, Param) else p for p in template)
    return re.sub(r"\*+", "*", text)


def _parents(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    out = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            out[child] = node
    return out


def _enclosing(node: ast.AST, parents: dict) -> ast.AST | None:
    while node in parents:
        node = parents[node]
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return node
    return None


def _key_arg(call: ast.Call, start: int, params: list[str] | None, param: str | None) -> ast.AST | None:
    """The argument of *call* that lands in the key parameter.

    *start* is where the callee's arguments begin in ``call.args`` (1 for
    ``to_thread(alert, key, ...)``); *params* the callee's parameter names
    (without ``self``) and *param* the key's -- None means ``alert`` itself,
    whose key is its first parameter, ``key``.
    """
    names = params if params is not None else ["key"]
    want = param if param is not None else "key"
    idx = names.index(want) if want in names else 0
    positional = call.args[start:]
    if idx < len(positional) and not isinstance(positional[idx], ast.Starred):
        return positional[idx]
    for kw in call.keywords:
        if kw.arg == want:
            return kw.value
    return None


def _severity(call: ast.Call, start: int) -> str | None:
    for kw in call.keywords:
        if kw.arg == "severity":
            return kw.value.value if isinstance(kw.value, ast.Constant) else None
    positional = call.args[start:]
    if len(positional) >= 4 and isinstance(positional[3], ast.Constant):  # never in practice
        return str(positional[3].value)
    return "warn"


def scan_source(text: str, rel: str) -> list[AlertSite]:
    """Every ops alert *text* raises, with its keys resolved."""
    tree = ast.parse(text)
    funcs, mods = _alert_refs(tree)
    if not funcs and not mods:
        return []
    parents = _parents(tree)
    scopes: dict[ast.AST | None, _Scope] = {}

    def scope_of(node: ast.AST) -> _Scope:
        func = _enclosing(node, parents)
        if func not in scopes:
            scopes[func] = _Scope(func)
        return scopes[func]

    # Each raising call: (call, start, callee) -- callee None for alert itself,
    # else the wrapper's function node.
    defs = {n.name: n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}

    def calls_of(is_target) -> list[tuple[ast.Call, int]]:
        found = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if is_target(node.func):
                found.append((node, 0))
                continue
            # Handed to a runner: to_thread(alert, key, ...) or
            # run_in_executor(None, alert, key, ...). A function passed as
            # a later argument is a value, not the call.
            args = node.args
            if args and is_target(args[0]):
                found.append((node, 1))
            elif (len(args) > 1 and isinstance(args[0], ast.Constant) and args[0].value is None
                  and is_target(args[1])):
                found.append((node, 2))
        return found

    # Templates per raising function: {func node: [(templates, site info)]}
    raw: list[tuple[ast.Call, int, list[tuple] | None, _Scope, str | None]] = []
    for call, start in calls_of(lambda f: _is_alert(f, funcs, mods)):
        sc = scope_of(call)
        key = _key_arg(call, start, None, None)
        templates = _evaluate(key, sc) if key is not None else None
        sev = None
        if templates is not None and all(isinstance(p, str) for t in templates for p in t):
            sev = _severity(call, start)
        raw.append((call, start, templates, sc, sev))

    def site(call: ast.Call, sc: _Scope, templates: list[tuple] | None, sev: str | None,
             direct: bool) -> AlertSite:
        keys = None if templates is None else tuple(sorted({_render(t) for t in templates}))
        return AlertSite(rel, call.lineno, sc.name, keys, sev, direct=direct)

    sites: list[AlertSite] = []
    pending = list(raw)
    # A wrapper of a wrapper is followed; a chain deeper than this is
    # reported with its parameters as wildcards.
    for depth in range(6):
        nxt = []
        for call, start, templates, sc, sev in pending:
            params = {p.name for t in (templates or []) for p in t if isinstance(p, Param)}
            wname = getattr(sc.func, "name", None)
            callers = (calls_of(lambda f, w=wname: _ref_name(f) == w)
                       if params and wname in defs else [])
            if not callers:
                # A literal key, an unreadable one, or a wrapper nothing in
                # this module calls (its parameters stand for anything).
                sites.append(site(call, sc, templates, sev, direct=depth == 0))
                continue
            callee_params = [p for p in sc.params if p not in ("self", "cls")]
            for ccall, cstart in callers:
                csc = scope_of(ccall)
                subs: dict[str, list[tuple]] = {}
                for p in params:
                    arg = _key_arg(ccall, cstart, callee_params, p)
                    subs[p] = (_evaluate(arg, csc) if arg is not None else None) or [(WILD,)]
                expanded = []
                for t in templates:
                    pieces = [subs[p.name] if isinstance(p, Param) else [(p,)] for p in t]
                    expanded += [tuple(itertools.chain.from_iterable(c)) for c in itertools.product(*pieces)]
                nxt.append((ccall, cstart, expanded, csc, None))
        if not nxt:
            break
        pending = nxt
    else:
        sites += [site(call, sc, templates, None, direct=False) for call, _s, templates, sc, _v in pending]
    return sites


def ops_alert_sites(root: Path = REPO) -> list[AlertSite]:
    sites = []
    for path in product_files(root):
        text = path.read_text(encoding="utf-8", errors="replace")
        if "ops_alerts" not in text:
            continue
        sites += scan_source(text, path.relative_to(root).as_posix())
    return sites


def literal_dict_keys(rel: str, name: str, root: Path = REPO) -> list[str]:
    """The string keys of the module-level dict literal *name* in *rel*."""
    tree = ast.parse((root / rel).read_text(encoding="utf-8"))
    for node in tree.body:
        target = (node.targets[0] if isinstance(node, ast.Assign) and len(node.targets) == 1
                  else node.target if isinstance(node, ast.AnnAssign) else None)
        if isinstance(target, ast.Name) and target.id == name and isinstance(node.value, ast.Dict):
            return [k.value for k in node.value.keys if isinstance(k, ast.Constant)]
    raise LookupError(f"{name} is not a dict literal in {rel}")


def literal_dict_values(rel: str, key: str, root: Path = REPO) -> list[str]:
    """Every string value of an entry ``key: "..."`` of any dict literal in *rel*."""
    tree = ast.parse((root / rel).read_text(encoding="utf-8"))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values):
                if (isinstance(k, ast.Constant) and k.value == key
                        and isinstance(v, ast.Constant) and isinstance(v.value, str)):
                    out.append(v.value)
    return out


# -- the lifecycle messages ----------------------------------------------------


def lifecycle_events(text: str | None = None) -> set[str]:
    """The keys of ``lifecycle_notifier._EVENTS`` (a card head per event)."""
    tree = ast.parse(text if text is not None else LIFECYCLE.read_text(encoding="utf-8"))
    for node in tree.body:
        target = (node.target if isinstance(node, ast.AnnAssign)
                  else node.targets[0] if isinstance(node, ast.Assign) else None)
        if isinstance(target, ast.Name) and target.id == "_EVENTS" and isinstance(node.value, ast.Dict):
            return {k.value for k in node.value.keys if isinstance(k, ast.Constant)}
    raise LookupError("_EVENTS is not a dict literal in lifecycle_notifier.py")


# -- the guard ------------------------------------------------------------------


@dataclass
class GuardPage:
    line: int
    kinds: tuple[str, ...] | None  # None: no readable kind
    severity: str | None = None  # the literal first argument, else None


def guard_pages(text: str | None = None) -> list[GuardPage]:
    """Every ``_page(...)`` call of the guard with the kinds it can page."""
    tree = ast.parse(text if text is not None else GUARD.read_text(encoding="utf-8"))
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        # _page(...), self._page(...), guard._page(...), self.guard._page(...)
        if not ((isinstance(f, ast.Name) and f.id == "_page")
                or (isinstance(f, ast.Attribute) and f.attr == "_page")):
            continue
        kind = next((kw.value for kw in node.keywords if kw.arg == "kind"), None)
        kinds: tuple[str, ...] | None = None
        if isinstance(kind, ast.Constant) and isinstance(kind.value, str):
            kinds = (kind.value,)
        elif (isinstance(kind, ast.IfExp) and isinstance(kind.body, ast.Constant)
              and isinstance(kind.orelse, ast.Constant)):
            kinds = (str(kind.body.value), str(kind.orelse.value))
        first = node.args[0] if node.args else None
        severity = first.value if isinstance(first, ast.Constant) and isinstance(first.value, str) else None
        out.append(GuardPage(node.lineno, kinds, severity))
    return sorted(out, key=lambda p: p.line)


# -- Kazma Watch -----------------------------------------------------------------


def watch_conditions(diagnose_js: str | None = None) -> tuple[set[str], set[str]]:
    """(every condition diagnose.js returns, the ones it counts as good)."""
    text = diagnose_js if diagnose_js is not None else (OBSERVER_SRC / "diagnose.js").read_text(encoding="utf-8")
    found = set(re.findall(r"""condition:\s*"([a-z_]+)\"""", text))
    good_m = re.search(r"GOOD\s*=\s*new Set\(\[([^\]]*)\]\)", text)
    good = set(re.findall(r'"([a-z_]+)"', good_m.group(1))) if good_m else set()
    return found, good


def watch_page_kinds(incidents_js: str | None = None) -> set[str]:
    """The page kinds incidents.js sends (open, remind, recovered)."""
    text = incidents_js if incidents_js is not None else (OBSERVER_SRC / "incidents.js").read_text(encoding="utf-8")
    return set(re.findall(r"""kind:\s*"([a-z_]+)\"""", text))


# -- matching --------------------------------------------------------------------


def catalog_pattern(key: str) -> str:
    """A catalog key as a pattern: each ``{placeholder}`` becomes ``*``."""
    return re.sub(r"\{[a-z_]+\}", "*", key)


def covers(pattern: str, key_pattern: str) -> bool:
    """True when every key *key_pattern* can be is matched by *pattern*.

    A ``*`` stands for one part of a dotted key (``runs.*.*`` is not
    ``runs.stalled.*``, and ``x.*`` does not swallow ``x.*.resolved``); a
    wildcard of *key_pattern* is matched only by a wildcard of *pattern*."""
    regex = "^" + "[^.]+".join(re.escape(p) for p in pattern.split("*")) + "$"
    return re.match(regex, key_pattern.replace("*", "\x01")) is not None
