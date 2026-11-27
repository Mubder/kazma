"""Exception text Kazma did not write never reaches a response (2026-11-14).

Routes put an exception's own text into what they answered -- ``str(exc)``,
``f"...: {exc}"`` -- raw or inside Kazma's sentence. A library's, the OS's
or a database driver's text names absolute paths, SQL statements, hosts and
internal module names: the clone route answered "Could not resolve clone URL:
<httpx's text>", the email and X helpers echoed ``str(exc)[:300]`` whenever
``KAZMA_PRODUCTION`` was unset (every install but a production one), the
backup delete answered the OS's "Access is denied: <the backup's path>", the
memory backend tests the remote embedder's URL and answer, the MCP add the
settings store's error (AGENTS.md section 26 F).

The rule: an unexpected failure is ``safe_error`` / ``described_error`` (a
stable code and a reference, the text logged under it); Kazma's own refusal
(a ``ValueError`` it raises with its sentence, a typed refusal) goes through
``validation_error``, which redacts what looks like a path or a secret.

The gate reads every module under ``kazma-ui/kazma_ui`` and
``kazma-gateway/kazma_gateway/routers`` (the routes and the helpers they
answer through). An exception's text is ``str(e)``, ``repr(e)``, an f-string
``{e}``, ``"%s" % e``, ``"...".format(e)``, ``e.args`` or ``traceback``
formatting, where ``e`` is the name an ``except ... as e`` binds (in that
handler) or a parameter typed as an exception. It is found when it reaches a
response: a ``return``, a ``raise`` (an ``HTTPException`` detail, or an
exception a route turns into one), a ``yield``, a ``*Response(...)`` call --
directly or through a local value it was put in (assigned, appended, set as a
key). A log call is not a response.

A handler catching only a type in ``DECLARED_TYPES`` is Kazma's own words;
a function in ``DECLARED_SITES`` is declared with why. A declaration that
excuses nothing fails, so the lists only shrink.

It grew from audit 2026-10-26 AUD-102's gate, which read six modules' broad
``except`` blocks (documents upload, platform-user create, the chat's
provider switch, Settings, the provider test, the MCP OAuth start); this one
reads every module and every handler, and follows the text through a local
value. AUD-102's behavioural test is kept below.
"""

from __future__ import annotations

import ast
import asyncio
import builtins
import functools
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCANNED = (ROOT / "kazma-ui" / "kazma_ui", ROOT / "kazma-gateway" / "kazma_gateway" / "routers")

#: A marker no answer may carry: a path and a host only the log should name.
SECRET_PATH = "/srv/secret-path/kazma-data/state.db"
SECRET_HOST = "db.internal.example"

#: Kazma's refusal classes whose message may reach a page as it is, and why
#: ``validation_error`` is not used for them.
DECLARED_TYPES: dict[str, str] = {
    "SettingRejected": (
        "settings_validation's refusal: every message is Kazma's sentence about the value; "
        "settings_alerts prefixes the field's name, and validation_error would read "
        "'bot_token: ...' as a secret and cut the sentence"
    ),
    "XApiError": (
        "x_api.client words the X API's answer (the status and X's reason about the owner's "
        "account), which is what the owner fixes, as a mail server's login answer is"
    ),
    "ReferenceUnreadable": (
        "names the reference document the owner configured (their own path, which "
        "validation_error would redact) beside Kazma's sentence"
    ),
}

#: Functions that answer Kazma's own ValueError text with ``str()``, each
#: with why ``validation_error`` does not fit.
DECLARED_SITES: dict[tuple[str, str], str] = {
    ("kazma-ui/kazma_ui/workspace_api.py", "put_extra_roots"): (
        "path_grants.set_durable_roots' ValueError: Kazma's sentence naming the folder "
        "the owner typed, which validation_error would show as <redacted>"
    ),
}

_FUNCS = (ast.FunctionDef, ast.AsyncFunctionDef)
_MUTATORS = {"append", "extend", "insert", "add", "update", "setdefault", "appendleft"}


# ── The walker ─────────────────────────────────────────────────────────────


def _walk_own(node: ast.AST):
    """Every node under *node*; nested functions and classes are not entered
    (each is judged on its own)."""
    stack = list(ast.iter_child_nodes(node))
    while stack:
        current = stack.pop()
        yield current
        if isinstance(current, (*_FUNCS, ast.ClassDef)):
            continue
        stack.extend(ast.iter_child_nodes(current))


def _type_names(node: ast.AST | None) -> set[str]:
    """The class names an ``except`` clause or an annotation names."""
    names: set[str] = set()
    if node is None:
        return names
    for sub in [node, *ast.walk(node)]:
        if isinstance(sub, ast.Name):
            names.add(sub.id)
        elif isinstance(sub, ast.Attribute):
            names.add(sub.attr)
    return names


def _product_files() -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z", "--", "kazma-*"], cwd=ROOT,
                         capture_output=True, check=True).stdout.decode("utf-8")
    return [rel for rel in out.split("\0") if rel.endswith(".py") and "/tests/" not in rel]


def exception_classes_in(trees: list[ast.AST]) -> frozenset[str]:
    """Python's exception classes, and every class *trees* define whose
    bases lead to one (by name, to a fixpoint): ``PeopleRefused(ValueError)``,
    ``_Refused(PeopleRefused)``."""
    found = {name for name in dir(builtins)
             if isinstance(getattr(builtins, name), type) and issubclass(getattr(builtins, name), BaseException)}
    bases: dict[str, set[str]] = {}
    for tree in trees:
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                bases.setdefault(node.name, set()).update(*(_type_names(b) for b in node.bases))
    changed = True
    while changed:
        changed = False
        for name, named in bases.items():
            if name not in found and (named & found or any(b.endswith(("Exception", "Error")) for b in named)):
                found.add(name)
                changed = True
    return frozenset(found)


@functools.lru_cache(maxsize=1)
def _exception_classes() -> frozenset[str]:
    trees = [ast.parse((ROOT / rel).read_text(encoding="utf-8")) for rel in _product_files()]
    assert len(trees) > 500, "the class census must see the packages"
    return exception_classes_in(trees)


def _is_exception_annotation(annotation: ast.AST | None) -> bool:
    """A parameter typed as an exception: a name ending in Exception or
    Error, or any exception class Kazma defines. By the suffix alone,
    ``people_api._refused(exc: people.PeopleRefused)`` answered ``str(exc)``
    unseen (2026-11-26, CodeQL alert 554)."""
    names = _type_names(annotation)
    return any(name.endswith(("Exception", "Error")) for name in names) or bool(names & _exception_classes())


def _is_leak(node: ast.AST, tainted: set[str]) -> bool:
    """*node* is a caught exception's text."""
    if isinstance(node, ast.Call):
        func = node.func
        args = [*node.args, *[k.value for k in node.keywords]]
        if (isinstance(func, ast.Name) and func.id in ("str", "repr", "format") and node.args
                and isinstance(node.args[0], ast.Name) and node.args[0].id in tainted):
            return True
        if (isinstance(func, ast.Attribute) and func.attr == "format"
                and isinstance(func.value, (ast.Constant, ast.JoinedStr))
                and any(isinstance(a, ast.Name) and a.id in tainted for a in args)):
            return True
        if (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)
                and func.value.id == "traceback" and func.attr.startswith("format")):
            return True
    if isinstance(node, ast.FormattedValue) and isinstance(node.value, ast.Name):
        return node.value.id in tainted
    if isinstance(node, ast.Attribute) and node.attr == "args" and isinstance(node.value, ast.Name):
        return node.value.id in tainted
    if isinstance(node, (ast.Dict, ast.List, ast.Tuple, ast.Set)):
        # The exception itself as a value: serialized, it is its text.
        items = node.values if isinstance(node, ast.Dict) else node.elts
        return any(isinstance(v, ast.Name) and v.id in tainted for v in items)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod) and isinstance(
            node.left, (ast.Constant, ast.JoinedStr)):
        right = node.right
        items = right.elts if isinstance(right, ast.Tuple) else [right]
        return any(isinstance(e, ast.Name) and e.id in tainted for e in items)
    return False


def _leaks(func: ast.AST, excused: frozenset[str]) -> set[int]:
    """The ids of *func*'s exception-text expressions."""
    found: set[int] = set()
    params = {a.arg for a in [*func.args.posonlyargs, *func.args.args, *func.args.kwonlyargs]
              if _is_exception_annotation(a.annotation)}
    if params:
        found |= {id(n) for n in _walk_own(func) if _is_leak(n, params)}
    for handler in _walk_own(func):
        if not isinstance(handler, ast.ExceptHandler) or not handler.name:
            continue
        caught = _type_names(handler.type)
        if caught and caught <= excused:
            continue  # Kazma's own refusal class (DECLARED_TYPES)
        for stmt in handler.body:
            found |= {id(n) for n in [stmt, *_walk_own(stmt)] if _is_leak(n, {handler.name})}
    return found


def _carries(expr: ast.AST, leaks: set[int], carriers: set[str]) -> bool:
    for node in [expr, *_walk_own(expr)]:
        if id(node) in leaks or (isinstance(node, ast.Name) and node.id in carriers):
            return True
    return False


def _target_name(target: ast.AST) -> str | None:
    """The local name a value is put into: ``x``, ``x[...]``; an object's
    attribute (``self.errors``) is state, not this answer."""
    while isinstance(target, ast.Subscript):
        target = target.value
    return target.id if isinstance(target, ast.Name) else None


def _function_findings(func: ast.AST, excused: frozenset[str]) -> list[tuple[int, str]]:
    leaks = _leaks(func, excused)
    if not leaks:
        return []
    own = list(_walk_own(func))
    carriers: set[str] = set()
    changed = True
    while changed:
        changed = False
        for node in own:
            targets: list[ast.AST] = []
            value: ast.AST | None = None
            if isinstance(node, ast.Assign):
                targets, value = node.targets, node.value
            elif isinstance(node, (ast.AnnAssign, ast.AugAssign)) and node.value is not None:
                targets, value = [node.target], node.value
            elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                  and node.func.attr in _MUTATORS):
                targets = [node.func.value]
                value = ast.Tuple(elts=[*node.args, *[k.value for k in node.keywords]], ctx=ast.Load())
            if value is None or not _carries(value, leaks, carriers):
                continue
            for target in targets:
                name = _target_name(target)
                if name and name not in carriers:
                    carriers.add(name)
                    changed = True
    findings: dict[int, str] = {}
    for node in own:
        sink: ast.AST | None = None
        if isinstance(node, ast.Return) and node.value is not None:
            sink = node.value
        elif isinstance(node, ast.Raise) and node.exc is not None:
            sink = node.exc
        elif isinstance(node, (ast.Yield, ast.YieldFrom)) and node.value is not None:
            sink = node.value
        elif isinstance(node, ast.Call):
            callee = node.func
            name = callee.id if isinstance(callee, ast.Name) else getattr(callee, "attr", "")
            if name.endswith("Response") or name == "HTTPException":
                sink = node
        if sink is not None and _carries(sink, leaks, carriers):
            findings.setdefault(node.lineno, ast.unparse(node)[:120])
    return sorted(findings.items())


def _tree_findings(tree: ast.AST, rel: str, excused: frozenset[str]) -> list[tuple[str, str, int, str]]:
    out = []
    for node in ast.walk(tree):
        if isinstance(node, _FUNCS):
            for line, code in _function_findings(node, excused):
                out.append((rel, node.name, line, code))
    return out


def findings_in(source: str, rel: str, excused: frozenset[str]) -> list[tuple[str, str, int, str]]:
    """``(file, function, line, code)`` for each response carrying exception text."""
    return _tree_findings(ast.parse(source), rel, excused)


@functools.lru_cache(maxsize=1)
def _modules() -> tuple[tuple[str, ast.AST], ...]:
    files = sorted({p for base in SCANNED for p in base.rglob("*.py")})
    assert len(files) > 100, "the scan must see the UI and the routers"
    return tuple((p.relative_to(ROOT).as_posix(), ast.parse(p.read_text(encoding="utf-8"))) for p in files)


@functools.lru_cache(maxsize=8)
def _all_findings(excused: frozenset[str]) -> tuple[tuple[str, str, int, str], ...]:
    out: list[tuple[str, str, int, str]] = []
    for rel, tree in _modules():
        out.extend(_tree_findings(tree, rel, excused))
    return tuple(out)


# ── The gate ──────────────────────────────────────────────────────────────


def test_no_response_carries_exception_text() -> None:
    found = _all_findings(frozenset(DECLARED_TYPES))
    left = [f for f in found if (f[0], f[1]) not in DECLARED_SITES]
    assert not left, (
        "A response carries an exception's own text. Use safe_error / described_error for an "
        "unexpected failure, validation_error for Kazma's own refusal:\n"
        + "\n".join(f"  {path}:{line} [{func}] {code}" for path, func, line, code in left)
    )


def test_every_declared_site_excuses_something() -> None:
    found = {(f[0], f[1]) for f in _all_findings(frozenset(DECLARED_TYPES))}
    stale = sorted(site for site in DECLARED_SITES if site not in found)
    assert not stale, f"declared sites that carry no exception text (remove them): {stale}"


@pytest.mark.parametrize("name", sorted(DECLARED_TYPES))
def test_every_declared_type_excuses_something(name: str) -> None:
    with_it = len(_all_findings(frozenset(DECLARED_TYPES)))
    without_it = len(_all_findings(frozenset(DECLARED_TYPES) - {name}))
    assert without_it > with_it, f"{name} is declared but excuses no response (remove it)"


#: Where each declared type is defined: a class of Kazma's own, whose
#: messages Kazma writes.
_DECLARED_TYPE_HOMES = {
    "SettingRejected": "kazma-core/kazma_core/settings_validation.py",
    "XApiError": "kazma-core/kazma_core/x_api/client.py",
    "ReferenceUnreadable": "kazma-core/kazma_core/x_api/references.py",
}


@pytest.mark.parametrize("name", sorted(DECLARED_TYPES))
def test_every_declared_type_is_kazmas_own_class(name: str) -> None:
    home = ROOT / _DECLARED_TYPE_HOMES[name]
    tree = ast.parse(home.read_text(encoding="utf-8"))
    assert any(isinstance(n, ast.ClassDef) and n.name == name for n in tree.body), (
        f"{name} must be a class Kazma defines in {home.name} (its message is Kazma's)")


_OLD_SHAPES = '''
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

router = APIRouter()


@router.post("/clone")
async def clone_repo():
    try:
        resolve()
    except Exception as exc:
        return JSONResponse({"error": f"Could not resolve clone URL: {exc}"}, status_code=502)


def _safe_error(exc: Exception, status: int = 500) -> JSONResponse:
    return JSONResponse({"ok": False, "detail": "" if production() else str(exc)[:300]})


@router.delete("/files")
def delete_files():
    try:
        remove()
    except OSError as exc:
        files_error = str(exc)[:300]
        raise HTTPException(status_code=500, detail=f"Failed to delete files: {files_error}")


@router.post("/maintain")
def maintain():
    details = {}
    try:
        vacuum()
    except Exception as exc:
        details["primary"] = "failed: %s" % exc
    return {"details": details}


@router.post("/save")
def save():
    problems = []
    try:
        check()
    except Exception as e:
        problems.append(repr(e))
    return {"problems": problems}


@router.post("/test")
def provider_test():
    try:
        probe()
    except Exception as e:
        return {"success": False, "error": e}
'''

_SAFE_SHAPES = '''
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from kazma_core.errors import described_error, safe_error, validation_error

router = APIRouter()


@router.post("/a")
def a():
    try:
        work()
    except ValueError as exc:
        return JSONResponse({"error": validation_error(exc)}, status_code=400)
    except Exception as exc:
        logger.warning("failed: %s", exc)
        logger.warning(f"failed: {exc}")
        return {"error": safe_error(exc), "detail": described_error("It failed", exc)}


@router.post("/b")
def b():
    try:
        work()
    except SettingRejected as exc:
        return {"error": str(exc)}
    except Exception as exc:
        message = f"saving failed: {exc}"
        logger.error(message)
        return {"error": "saving failed", "kind": type(exc).__name__}
'''


def test_the_walker_finds_the_old_shapes() -> None:
    """Negative control: the shapes this change removed, planted and parsed by
    the same walker, are found -- the gate would have failed on them."""
    found = findings_in(_OLD_SHAPES, "planted.py", frozenset(DECLARED_TYPES))
    assert {f[1] for f in found} == {
        "clone_repo", "_safe_error", "delete_files", "maintain", "save", "provider_test"}, found


def test_the_walker_leaves_the_safe_shapes() -> None:
    found = findings_in(_SAFE_SHAPES, "planted.py", frozenset(DECLARED_TYPES))
    assert not found, found
    undeclared = findings_in(_SAFE_SHAPES, "planted.py", frozenset())
    assert [f[1] for f in undeclared] == ["b"], "an undeclared type is held to the rule"


#: people_api._refused before 2026-11-26: a parameter typed with one of
#: Kazma's refusal classes, whose text went out raw.
_REFUSAL_PARAMETER = '''
from fastapi.responses import JSONResponse
from kazma_core.spaces import people


def _refused(exc: people.PeopleRefused) -> JSONResponse:
    body = {"ok": False, "error": exc.code, "message": str(exc)}
    return JSONResponse(body, status_code=exc.status)


def _answered_by_field(exc: people.PeopleRefused) -> JSONResponse:
    return JSONResponse({"ok": False, "message": refusal_message(exc)}, status_code=exc.status)
'''


def test_a_parameter_typed_with_kazmas_own_exception_is_held_to_the_rule(monkeypatch) -> None:
    """Every exception class the packages define is known, whatever its
    name ends in: the old suffix rule (the negative control) let the People
    page answer ``str(exc)`` of a ``PeopleRefused`` unseen."""
    assert {"PeopleRefused", "LinkRefused", "SpaceMoveRefused", "ImportRefused"} <= _exception_classes()
    assert not {"Path", "JSONResponse", "BaseModel"} & _exception_classes()
    found = findings_in(_REFUSAL_PARAMETER, "planted.py", frozenset(DECLARED_TYPES))
    assert [f[1] for f in found] == ["_refused"], found

    def suffix_only(annotation: ast.AST | None) -> bool:
        return any(n.endswith(("Exception", "Error")) for n in _type_names(annotation))

    monkeypatch.setitem(globals(), "_is_exception_annotation", suffix_only)
    assert not findings_in(_REFUSAL_PARAMETER, "planted.py", frozenset(DECLARED_TYPES)), (
        "the control: the suffix rule must miss the refusal parameter")


def test_the_class_census_follows_bases_to_a_fixpoint() -> None:
    tree = ast.parse("class A(ValueError): ...\nclass B(A): ...\nclass C(B): ...\nclass D(object): ...\n")
    found = exception_classes_in([tree])
    assert {"A", "B", "C"} <= found and "D" not in found


# ── Behaviour: each answer says what happened, never the exception's text ──


def _fault(kind: type[BaseException] = RuntimeError) -> BaseException:
    return kind(f"cannot open {SECRET_PATH} on {SECRET_HOST}")


def _carries_no_secret(text: Any) -> None:
    assert SECRET_PATH not in str(text) and SECRET_HOST not in str(text), text
    assert "/srv/secret-path" not in str(text), text


def test_email_and_x_helpers_answer_without_the_exception_text(monkeypatch) -> None:
    """Item 2: ``detail`` echoed ``str(exc)[:300]`` with KAZMA_PRODUCTION unset."""
    import json

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from kazma_ui import email_api, x_api

    monkeypatch.delenv("KAZMA_PRODUCTION", raising=False)
    for helper in (email_api._safe_error, x_api._safe_error):
        body = json.loads(helper(_fault()).body)
        _carries_no_secret(body)
        assert body["error"] == "internal_error" and body["code"] == "internal_error"
        assert body["detail"].startswith("Something went wrong (internal_error, ref: ")

    # The route the Settings page calls, answered through the helper.
    from kazma_skills.native.email_manager import accounts

    def _remove(alias: str) -> dict[str, Any]:
        raise _fault(sqlite3.OperationalError)

    monkeypatch.setattr(accounts, "remove_account", _remove)
    app = FastAPI()
    app.include_router(email_api.protected_router)
    client = TestClient(app, headers={"Origin": "http://testserver", "X-Requested-With": "XMLHttpRequest"})
    r = client.post("/api/email/accounts/box/remove")
    assert r.status_code == 500
    _carries_no_secret(r.text)
    assert "database_unavailable" in r.json()["detail"]


def _ingest_with(monkeypatch, *, check=None, store=None) -> Any:
    """``ingest_url`` with the store, the address check and the fetch faked."""
    from kazma_core.stores import knowledge_ingest as ki
    from kazma_core.web_acquire import dns_worker

    class _Store:
        def ensure_capacity(self, library_id: str) -> None:
            return None

        def get_library(self, library_id: str) -> dict[str, Any]:
            return {"id": library_id}

    async def _public(url: str, **_kw: Any) -> None:
        if check is not None:
            raise check

    async def _extract(url: str) -> tuple[str, str, str]:
        return "A page about things.", "ok", ""

    def _store_page(*_a: Any, **_kw: Any) -> tuple[int, int, int]:
        if store is not None:
            raise store
        return 1, 0, 1

    monkeypatch.setattr(ki, "_knowledge_store", lambda: _Store())
    monkeypatch.setattr(ki, "_knowledge_index", lambda: None)
    monkeypatch.setattr(dns_worker, "validate_public_url", _public)
    monkeypatch.setattr(ki, "_extract_page", _extract)
    monkeypatch.setattr(ki, "_store_page", _store_page)
    return asyncio.run(ki.ingest_url("lib", "https://example.com/page"))


def test_a_page_not_stored_names_the_failure_not_its_text(monkeypatch) -> None:
    """Item 5: ``ingest_url`` appended "not stored: <url> — <exc>" and
    "SSRF/invalid URL <url>: <exc>", which the Knowledge page shows."""
    from kazma_core.security.ssrf import SSRFError

    result = _ingest_with(monkeypatch, store=_fault(sqlite3.OperationalError))
    assert result.pages_failed == 1 and result.errors
    _carries_no_secret(result.errors)
    assert result.errors[0].startswith("not stored: https://example.com/page")
    assert "OperationalError" in result.errors[0], "the kind of failure is said"

    result = _ingest_with(monkeypatch, check=_fault(OSError))
    _carries_no_secret(result.errors)
    assert "could not be checked (OSError)" in result.errors[0]

    # Kazma's own refusal of the address is the answer, and stays.
    result = _ingest_with(monkeypatch, check=SSRFError("URL resolves to a private address."))
    assert "URL resolves to a private address." in result.errors[0]


def test_backup_delete_and_archive_answer_without_the_os_text(monkeypatch, tmp_path: Path) -> None:
    """Item 6: the delete and archive returned ``str(exc)[:200]`` -- the OS's
    "Access is denied: <the backup's absolute path>"."""
    import zipfile

    from kazma_core.backup import universal

    (tmp_path / "1700000000").mkdir()
    (tmp_path / "1700000000" / "f.txt").write_text("x", encoding="utf-8")
    monkeypatch.setattr(universal, "_universal_dir", lambda: tmp_path)

    def _denied(path: Path) -> None:
        raise _fault(PermissionError)

    monkeypatch.setattr(universal, "_rmtree_force", _denied)
    result = universal.delete_universal_backup("1700000000")
    assert result["ok"] is False
    _carries_no_secret(result)
    assert "could not be removed (permission_denied, ref: " in result["error"]

    def _no_zip(*_a: Any, **_kw: Any) -> Any:
        raise _fault(OSError)

    monkeypatch.setattr(zipfile, "ZipFile", _no_zip)
    result = universal.archive_universal_backup("1700000000")
    assert result["ok"] is False
    _carries_no_secret(result)
    assert "the archive could not be written (io_error, ref: " in result["error"]


def test_memory_backend_tests_answer_without_the_driver_text(monkeypatch) -> None:
    """Item 7: the embedder and vector tests returned ``str(exc)[:300]`` (a
    remote embedder's URL and answer, SQLite's file), the Postgres backfill
    "read sqlite failed: <exc>"."""
    from kazma_core.memory import backends, embedder, state_backend

    class _Embedder:
        def encode(self, text: str) -> list[float]:
            raise _fault(RuntimeError)

    monkeypatch.setattr(embedder, "get_embedder", lambda *a, **k: _Embedder())
    result = backends.test_embedder_backend({"embedder": {"provider": "remote"}})
    assert result["ok"] is False
    _carries_no_secret(result)
    assert result["error"].startswith("the embedder did not encode a test text (internal_error, ref: ")

    def _locked(provider: str, t0: float) -> dict[str, Any]:
        raise _fault(sqlite3.OperationalError)

    monkeypatch.setattr(backends, "_test_local_vector_store", _locked)
    result = backends.test_vector_backend({"vector": {"provider": "sqlite_vec"}})
    assert result["ok"] is False
    _carries_no_secret(result)
    assert "vector store did not answer the test (database_unavailable, ref: " in result["error"]

    class _Mirror:
        available = True

    def _unreadable() -> str:
        raise _fault(sqlite3.OperationalError)

    monkeypatch.setattr(state_backend, "get_state_backend", lambda: _Mirror())
    monkeypatch.setattr("kazma_core.paths.primary_memory_db", _unreadable)
    result = state_backend.backfill_state_mirror(tenant_id="default")
    assert result["ok"] is False
    _carries_no_secret(result)
    assert "local memory database could not be read (database_unavailable, ref: " in result["error"]


def test_adding_an_mcp_server_answers_without_the_store_text(monkeypatch) -> None:
    """Item 9: ``add_mcp_server`` (Settings -> MCP's Add) and the delete
    returned ``str(exc)`` for any failure of the settings store."""
    from kazma_core import mcp_servers_store
    from kazma_core.settings_mcp import MCPSettingsService

    def _refused(*_a: Any, **_kw: Any) -> Any:
        raise _fault(sqlite3.OperationalError)

    monkeypatch.setattr(mcp_servers_store, "upsert_mcp_server", _refused)
    monkeypatch.setattr(mcp_servers_store, "delete_mcp_server", _refused)
    monkeypatch.setattr("kazma_core.settings_mcp._agent_yaml_path", lambda: "kazma.yaml")
    service = MCPSettingsService(config_store=None)
    result = service.add_mcp_server({"name": "files", "command": "npx"})
    _carries_no_secret(result)
    assert result["error"].startswith("The MCP server could not be saved (database_unavailable, ref: ")
    result = service.delete_mcp_server("files")
    _carries_no_secret(result)
    assert result["error"].startswith("The MCP server could not be removed (database_unavailable, ref: ")

    # The store's own refusal of the entry stays the answer.
    def _invalid(*_a: Any, **_kw: Any) -> Any:
        raise ValueError("An MCP server needs a command or a URL.")

    monkeypatch.setattr(mcp_servers_store, "upsert_mcp_server", _invalid)
    assert service.add_mcp_server({"name": "files"}) == {"error": "An MCP server needs a command or a URL."}


def test_described_error_logs_the_text_under_its_reference(caplog) -> None:
    """The text a page no longer sees is in the log, under the reference the
    page shows: the operator joins the two."""
    import logging

    from kazma_core.errors import described_error

    with caplog.at_level(logging.WARNING, logger="kazma_core.errors"):
        info = described_error("The probe failed", _fault(), level=logging.WARNING)
    _carries_no_secret(info)
    assert str(info) == f"The probe failed (internal_error, ref: {info.ref})."
    logged = [r.getMessage() for r in caplog.records]
    assert any(info.ref in line and SECRET_PATH in line for line in logged), logged


def test_a_failed_user_create_answers_a_reference_not_the_store_error(monkeypatch) -> None:
    """AUD-102: the platform-user create answered the store's error."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from kazma_core.security import platform_rbac
    from kazma_ui import auth
    from kazma_ui.saas_api import create_saas_router

    def boom(*_a, **_k):
        raise RuntimeError("could not write /srv/private/users.db: disk full")

    monkeypatch.setattr(auth, "admin_decision", lambda request: "ok")
    monkeypatch.setattr(platform_rbac, "create_local_user", boom)
    app = FastAPI()
    app.include_router(create_saas_router())
    with TestClient(app) as client:
        r = client.post(
            "/api/saas/users", json={"username": "lee", "password": "long-enough"}
        )
    assert r.status_code == 500
    text = r.text
    assert "/srv/private" not in text and "disk full" not in text
    assert "ref:" in r.json()["error"]
