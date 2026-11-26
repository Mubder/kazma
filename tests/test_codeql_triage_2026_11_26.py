"""The CodeQL alerts open on 2026-11-26 (135): the ones the code changed for.

Every alert is judged in ``docs/audits/CODEQL_TRIAGE_2026-11-26.md``; this
file holds what changed, each with the old code or shape as its control
(AGENTS.md sections 26 F and 66).

- **Every key's read path names nothing a secret.** CodeQL calls whatever a
  variable named like a secret holds a secret. ``ConfigStore._resolve_vault_value``
  -- the read path of every setting -- bound the vault's fallback answer to
  ``secret``, so every setting any module read was one, and 88 log lines of
  account ids, space ids and counts were reported as secrets. A secret is
  named where it is read, by its key.
- **A refusal's sentence is read from its field.** Routes answer Kazma's own
  refusals (``PeopleRefused``, ``LinkRefused``...) with
  ``errors.refusal_message``: the ``message`` the class keeps, redacted --
  never the exception's text. ``people_api`` answered ``str(exc)`` raw; the
  section 26 F gate did not see it (``tests/test_route_errors_carry_no_exception_text.py``
  now knows every exception class the packages define).
- **A memory backup's path is made from its timestamp**
  (``memory.backup.memory_backup_path``): the restore route joined the text
  the request sent, and the name check took Unicode digits.
- **X's sign-in refusal comes back as Kazma's word** (``x_api._sign_in_error_word``):
  the letters of any text X's redirect carried went to the page. X's outage
  codes are ``unreachable``, and every word is in the catalog in both
  languages.

The review of this triage widened three gates: the name walk follows a
setting's value into the vault; ``refusal_message`` is handed only a caught
refusal of Kazma's own (no built-in beside it, no expression); and no route
answers a typed refusal (``...Refused``) with the exception's text, so
``DocumentsRefused``, ``MoveRefused`` and ``KnowledgeUrlRefused`` keep their
sentence too. A refusal with no sentence answers the catalog's, in the
reader's language.
"""

from __future__ import annotations

import ast
import logging
import re
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
CONFIG_STORE = ROOT / "kazma-core" / "kazma_core" / "config_store.py"


# ── Every key's read path names nothing a secret ──────────────────────────

#: CodeQL's own name heuristics (codeql/concepts SensitiveDataHeuristics,
#: CLI 2.27.6): the "secret" and "password" classes, and the names it never
#: counts. Searched rather than matched whole: their ``.*X.*`` is a search.
_CODEQL_SECRET = re.compile(
    r"(?<!is)(?<!is_)secret|(?<!un)(?<!un_)(?<!is)(?<!is_)trusted(?!_iter|_?schema)|confidential",
    re.IGNORECASE | re.DOTALL)
_CODEQL_PASSWORD = re.compile(
    r"pass(wd|word|code|.?phrase)(?!.*question)|(auth(entication|ori[sz]ation)?).?key|oauth|api.?(key|tok)"
    r"|([_-]|\b)mfa([_-]|\b)", re.IGNORECASE | re.DOTALL)
_CODEQL_NOT_SENSITIVE = re.compile(
    r"[^\w$.-]|redact|censor|obfuscate|hash|md5|sha|random|(?<!unen)crypt|(?<!un)encode|certain|concert"
    r"|secretar|wildcard|coauthor|account(ant|ab|ing|ed)|(?<!pro)file|path|([_-]|\b)url", re.IGNORECASE | re.DOTALL)


def codeql_reads_as_secret(name: str) -> bool:
    """Whether CodeQL takes a value bound to *name* for a secret or a password."""
    return bool(_CODEQL_SECRET.search(name) or _CODEQL_PASSWORD.search(name)) and not _CODEQL_NOT_SENSITIVE.search(name)


#: The modules on a setting's value path, and the receivers whose class the
#: walk knows (``vault.retrieve(...)`` is ``SecretVault.retrieve``).
_VALUE_MODULES = {
    "config_store": "kazma-core/kazma_core/config_store.py",
    "vault": "kazma-core/kazma_core/security/vault.py",
}
_RECEIVERS = {"vault": "vault:SecretVault"}
#: Where a setting's value leaves the store.
_VALUE_ENTRIES = ("config_store:ConfigStore.get", "config_store:ConfigStore.get_all",
                  "config_store:ConfigStore.get_category", "config_store:ConfigStore.snapshot")


def _module_table(module: str, tree: ast.Module) -> tuple[dict[str, ast.AST], dict[str, str]]:
    """``module:Qual`` -> function (module functions and every class's
    methods), and each name the module imports from another walked module."""
    funcs: dict[str, ast.AST] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            funcs[f"{module}:{node.name}"] = node
        elif isinstance(node, ast.ClassDef):
            for m in node.body:
                if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    funcs[f"{module}:{node.name}.{m.name}"] = m
    by_path = {rel.replace("/", ".").removesuffix(".py").split(".", 1)[1]: name
               for name, rel in _VALUE_MODULES.items()}
    imported: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in by_path:
            for alias in node.names:
                imported[alias.asname or alias.name] = f"{by_path[node.module]}:{alias.name}"
    return funcs, imported


def _flowing_calls(func: ast.AST) -> list[ast.Call]:
    """The calls whose result can reach *func*'s return: in a return, or
    assigned to a name a return (or such an assignment) reads."""
    returns = [r.value for r in ast.walk(func) if isinstance(r, ast.Return) and r.value is not None]
    reached: set[str] = {n.id for e in returns for n in ast.walk(e) if isinstance(n, ast.Name)}
    exprs: list[ast.AST] = list(returns)
    assigns = [n for n in ast.walk(func) if isinstance(n, (ast.Assign, ast.AnnAssign, ast.AugAssign))
               and n.value is not None]
    grew = True
    while grew:
        grew = False
        for node in assigns:
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            bound = {n.id for t in targets for n in ast.walk(t) if isinstance(n, ast.Name)}
            if bound & reached and node.value not in exprs:
                exprs.append(node.value)
                reached |= {n.id for n in ast.walk(node.value) if isinstance(n, ast.Name)}
                grew = True
    return [c for e in exprs for c in ast.walk(e) if isinstance(c, ast.Call)]


def _value_path(sources: dict[str, str] | None = None) -> dict[str, ast.AST]:
    """Every function a setting's value can come back through, from
    ``ConfigStore.get`` (and ``get_all``, ``get_category``, ``snapshot``),
    followed through the calls whose result reaches a return, into the
    vault: ``module:Qual`` -> function."""
    sources = sources or {m: (ROOT / rel).read_text(encoding="utf-8") for m, rel in _VALUE_MODULES.items()}
    funcs: dict[str, ast.AST] = {}
    imports: dict[str, dict[str, str]] = {}
    for module, text in sources.items():
        table, imported = _module_table(module, ast.parse(text))
        funcs.update(table)
        imports[module] = imported
    seen: dict[str, ast.AST] = {}
    todo = [e for e in _VALUE_ENTRIES if e in funcs]
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen[name] = funcs[name]
        module, qual = name.split(":", 1)
        owner = qual.rsplit(".", 1)[0] if "." in qual else ""
        for call in _flowing_calls(funcs[name]):
            fn = call.func
            target = None
            if isinstance(fn, ast.Name):
                target = (f"{module}:{fn.id}" if f"{module}:{fn.id}" in funcs
                          else imports[module].get(fn.id))
            elif isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name):
                if fn.value.id == "self" and owner:
                    target = f"{module}:{owner}.{fn.attr}"
                elif fn.value.id in _RECEIVERS:
                    target = f"{_RECEIVERS[fn.value.id]}.{fn.attr}"
            if target in funcs:
                todo.append(target)
    return seen


def _names_read_as_secrets(sources: dict[str, str] | None = None) -> list[tuple[str, str]]:
    found = []
    for qual, func in _value_path(sources).items():
        for node in ast.walk(func):
            bound = None
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                bound = node.id
            elif isinstance(node, ast.arg):
                bound = node.arg
            if bound and codeql_reads_as_secret(bound):
                found.append((qual, bound))
    return found


def test_the_heuristic_reads_names_as_codeql_does() -> None:
    for name in ("secret", "client_secret", "api_key", "password", "oauth_token", "db_passphrase"):
        assert codeql_reads_as_secret(name), name
    for name in ("account_id", "space_id", "by", "key_id", "is_secret", "secret_path", "password_hash", "vname"):
        assert not codeql_reads_as_secret(name), name


def _value_sources() -> dict[str, str]:
    return {m: (ROOT / rel).read_text(encoding="utf-8").replace("\r\n", "\n")
            for m, rel in _VALUE_MODULES.items()}


def test_every_keys_read_path_names_nothing_a_secret() -> None:
    path = set(_value_path())
    assert {
        "config_store:ConfigStore.get", "config_store:ConfigStore.get_all",
        "config_store:ConfigStore._resolve_vault_value", "config_store:ConfigStore._sqlite_get_raw",
        "config_store:ConfigStore._pg_get_raw", "vault:retrieve_scoped", "vault:SecretVault.retrieve",
        "vault:SecretVault._open_row", "vault:SecretVault._decrypt",
    } <= path, sorted(path)
    assert not _names_read_as_secrets(), (
        "A function a setting's value comes back through binds a value to a name CodeQL reads as "
        "a secret: every setting becomes one to it, and each log line of an id is an alert. Return "
        "it unnamed, or name it for what it is.")


def test_control_the_old_fallback_is_found() -> None:
    sources = _value_sources()
    assert sources["config_store"].count("                return vault.retrieve(name)\n") == 1
    sources["config_store"] = sources["config_store"].replace(
        "                return vault.retrieve(name)\n",
        "                secret = vault.retrieve(name)\n                return secret if secret is not None else None\n")
    assert ("config_store:ConfigStore._resolve_vault_value", "secret") in _names_read_as_secrets(sources)


def test_control_a_secret_name_in_the_vault_is_found() -> None:
    """The walk reaches into the vault: a value bound to ``secret`` in the
    row decryption the fallback returns through is found."""
    sources = _value_sources()
    tree = ast.parse(sources["vault"])
    vault_cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "SecretVault")
    open_row = next(m for m in vault_cls.body if isinstance(m, ast.FunctionDef) and m.name == "_open_row")
    ret = next(n for n in open_row.body if isinstance(n, ast.Return))
    lines = sources["vault"].split("\n")
    indent = " " * ret.col_offset
    expr = ast.get_source_segment(sources["vault"], ret.value)
    lines[ret.lineno - 1] = f"{indent}secret = {expr}\n{indent}return secret"
    sources["vault"] = "\n".join(lines)
    assert ("vault:SecretVault._open_row", "secret") in _names_read_as_secrets(sources)


def test_the_fallback_still_reads_the_vault(tmp_path, monkeypatch) -> None:
    """The branch changed: when the scoped ladder cannot answer, the vault's
    own read is returned -- the value, or None."""
    from kazma_core.config_store import ConfigStore
    from kazma_core.security import vault as vault_mod

    def _no_ladder(name: str, vault: object) -> str:
        raise RuntimeError("the ladder is away")

    monkeypatch.setattr(vault_mod, "retrieve_scoped", _no_ladder)
    held = MagicMock()
    held.retrieve.return_value = "demo-settings-value"
    store = ConfigStore(db_path=str(tmp_path / "settings.db"), yaml_path=str(tmp_path / "none.yaml"))
    try:
        monkeypatch.setattr("kazma_core.config_store._try_get_vault", lambda: held)
        assert store._resolve_vault_value("x.key", "vault://x-name") == "demo-settings-value"
        held.retrieve.assert_called_with("x-name")
        held.retrieve.return_value = None
        assert store._resolve_vault_value("x.key", "vault://x-name") is None
    finally:
        store.close()


# ── A refusal's sentence is read from its field ───────────────────────────


class _SaysInternalsError(ValueError):
    """A refusal whose text is not its sentence: what a wrapper or a cause
    can make of ``str(exc)``."""

    def __init__(self, message: str) -> None:
        super().__init__("sqlite3.OperationalError at /home/kazma/kazma-data/identity.db")
        self.message = message


def test_the_sentence_is_the_field_never_the_text() -> None:
    from kazma_core.errors import refusal_message, validation_error

    refused = _SaysInternalsError("That person is not in this space.")
    assert refusal_message(refused) == "That person is not in this space."
    # The control: the exception's text is what validation_error answers.
    assert validation_error(refused).startswith("sqlite3.OperationalError")


def test_the_sentence_is_redacted_and_cut() -> None:
    from kazma_core.errors import refusal_message

    said = refusal_message(_SaysInternalsError(r"That folder (C:\Users\bob\kazma-data\x) cannot be used."))
    assert "Users" not in said and "<redacted>" in said
    assert len(refusal_message(_SaysInternalsError("x" * 5000))) == 300


def test_a_refusal_without_the_field_is_said_and_answers_no_text(caplog) -> None:
    from kazma_core.errors import REFUSED_KEY, refusal_message
    from kazma_ui.i18n import TRANSLATIONS

    with caplog.at_level(logging.WARNING, logger="kazma_core.errors"):
        said = refusal_message(ValueError("/home/kazma/kazma-data/state.db is locked"))
    assert said == TRANSLATIONS[REFUSED_KEY]["en"]
    assert any("ValueError carries no message field" in r.getMessage() for r in caplog.records)


def test_the_sentence_with_no_field_is_the_catalogs_in_the_readers_language() -> None:
    """English and Arabic are equals (section 57): the fallback is a catalog
    entry worded in both, answered in the request's language."""
    from kazma_core.errors import REFUSED_KEY, refusal_message
    from kazma_ui import i18n

    entry = i18n.TRANSLATIONS[REFUSED_KEY]
    assert entry["en"] and entry["ar"] and entry["ar"] != entry["en"]
    token = i18n.set_request_language("ar")
    try:
        assert refusal_message(ValueError("x")) == entry["ar"]
        assert refusal_message(_SaysInternalsError("")) == entry["ar"]
    finally:
        i18n._REQUEST_LANG.reset(token)


def test_the_people_page_answers_the_sentence_redacted() -> None:
    """``people_api._refused`` answered ``str(exc)`` as it was: a path in the
    sentence went to the page (this fails on the old code)."""
    import json

    from kazma_core.spaces.people import PeopleRefused
    from kazma_ui import people_api

    answer = people_api._refused(PeopleRefused(
        "folder_bad", 400, "That folder (/home/bob/kazma-data/spaces) cannot be this space's project folder."))
    body = json.loads(answer.body)
    assert answer.status_code == 400 and body["error"] == "folder_bad"
    assert "/home/bob" not in body["message"] and body["message"].startswith("That folder (<redacted>")


@pytest.mark.parametrize("make", [
    lambda: __import__("kazma_core.spaces.people", fromlist=["x"]).PeopleRefused("c", 400, "The sentence."),
    lambda: __import__("kazma_core.spaces.chat_links", fromlist=["x"]).LinkRefused("c", 400, "The sentence."),
    lambda: __import__("kazma_core.spaces.own_keys", fromlist=["x"]).OwnKeyRefused("c", "The sentence."),
    lambda: __import__("kazma_core.spaces.space_data", fromlist=["x"]).SpaceDataRefused("c", 400, "The sentence."),
    lambda: __import__("kazma_core.spaces.space_move", fromlist=["x"]).SpaceMoveRefused("c", 400, "The sentence."),
    lambda: __import__("kazma_core.memory.history_import", fromlist=["x"]).ImportRefused("The sentence."),
    lambda: __import__("kazma_core.documents.principal", fromlist=["x"]).DocumentsRefused("The sentence."),
    lambda: __import__("kazma_core.spaces.moves", fromlist=["x"]).MoveRefused("c", 403, "The sentence."),
    lambda: __import__("kazma_core.stores.knowledge", fromlist=["x"]).KnowledgeUrlRefused("The sentence."),
])
def test_each_refusal_keeps_its_sentence(make) -> None:
    from kazma_core.errors import refusal_message

    refused = make()
    assert refused.message == "The sentence." == str(refused)
    assert refusal_message(refused) == "The sentence."


def _product_files() -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z", "--", "kazma-*"], cwd=ROOT,
                         capture_output=True, check=True).stdout.decode("utf-8")
    return [rel for rel in out.split("\0") if rel.endswith(".py") and "/tests/" not in rel]


def _names(node: ast.AST | None) -> set[str]:
    out: set[str] = set()
    for sub in ast.walk(node) if node is not None else ():
        if isinstance(sub, ast.Name):
            out.add(sub.id)
        elif isinstance(sub, ast.Attribute):
            out.add(sub.attr)
    return out


def _keeps_a_message(cls: ast.ClassDef) -> bool:
    return any(
        isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Attribute) and t.attr == "message" and isinstance(t.value, ast.Name)
            and t.value.id == "self" for t in node.targets)
        for node in ast.walk(cls))


def _type_names(node: ast.AST | None) -> set[str]:
    """The class names an ``except`` clause or an annotation names: each
    element of a tuple or a union, a dotted name by its last part."""
    if node is None:
        return set()
    if isinstance(node, ast.Tuple):
        return set().union(*(_type_names(e) for e in node.elts))
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return _type_names(node.left) | _type_names(node.right)
    if isinstance(node, ast.Subscript):
        return _type_names(node.slice)
    if isinstance(node, ast.Attribute):
        return {node.attr}
    if isinstance(node, ast.Name):
        return {node.id}
    return set()


def refusal_callers_without_a_message(trees: dict[str, ast.AST]) -> list[str]:
    """``file: Class`` for each class a ``refusal_message(<name>)`` call is
    handed (the type its ``except ... as <name>`` catches, or its parameter's
    annotation) that keeps no ``self.message``, itself or through a base;
    and ``file:line`` for a call handed something else: a type Kazma does not
    define (a built-in caught beside a refusal), or an argument that is not
    a plain name."""
    classes: dict[str, list[ast.ClassDef]] = {}
    for tree in trees.values():
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                classes.setdefault(node.name, []).append(node)

    def carries(name: str, seen: frozenset[str] = frozenset()) -> bool:
        if name in seen or name not in classes:
            return False
        return any(_keeps_a_message(c) or any(carries(b, seen | {name}) for n in c.bases for b in _names(n))
                   for c in classes[name])

    missing: list[str] = []
    for rel, tree in trees.items():
        for func in ast.walk(tree):
            if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            typed = {a.arg: _type_names(a.annotation) - {"None", "Optional"}
                     for a in [*func.args.args, *func.args.kwonlyargs]}
            handlers = [h for h in ast.walk(func) if isinstance(h, ast.ExceptHandler) and h.name]
            for call in ast.walk(func):
                if not (isinstance(call, ast.Call) and _names(call.func) & {"refusal_message"}):
                    continue
                if not (call.args and isinstance(call.args[0], ast.Name)):
                    missing.append(f"{rel}:{call.lineno}: refusal_message of something that is not a "
                                   "caught refusal's name")
                    continue
                handed = call.args[0].id
                kinds = set(typed.get(handed, set()))
                for handler in handlers:
                    if handler.name == handed and any(call is n for n in ast.walk(handler)):
                        kinds |= _type_names(handler.type)
                foreign = sorted(kinds - set(classes))
                if foreign:
                    missing.append(f"{rel}:{call.lineno}: refusal_message({handed}) may be handed "
                                   f"{', '.join(foreign)}, which Kazma does not define")
                kinds &= set(classes)
                if not kinds and not foreign:
                    missing.append(f"{rel}:{call.lineno}: refusal_message({handed}) of no class Kazma defines")
                missing += [f"{rel}: {kind}" for kind in sorted(kinds) if not carries(kind)]
    return sorted(set(missing))


def test_every_refusal_a_route_answers_keeps_its_sentence() -> None:
    trees = {rel: ast.parse((ROOT / rel).read_text(encoding="utf-8")) for rel in _product_files()}
    callers = [rel for rel, tree in trees.items()
               if any(isinstance(n, ast.Call) and _names(n.func) & {"refusal_message"} for n in ast.walk(tree))]
    assert len(callers) >= 5, callers
    assert not refusal_callers_without_a_message(trees)


def test_control_a_refusal_with_no_field_is_found() -> None:
    planted = ast.parse(
        "class Quiet(ValueError):\n    pass\n\n"
        "class Kept(ValueError):\n    def __init__(self, m):\n        self.message = m\n\n"
        "class KeptBelow(Kept):\n    pass\n\n"
        "def route():\n    try:\n        work()\n    except Quiet as exc:\n        return refusal_message(exc)\n"
        "    except KeptBelow as exc:\n        return refusal_message(exc)\n\n"
        "def helper(exc: Kept):\n    return refusal_message(exc)\n")
    assert refusal_callers_without_a_message({"planted.py": planted}) == ["planted.py: Quiet"]


def test_control_a_builtin_caught_beside_a_refusal_and_an_expression_are_found() -> None:
    planted = ast.parse(
        "class Kept(ValueError):\n    def __init__(self, m):\n        self.message = m\n\n"
        "def route():\n    try:\n        work()\n"
        "    except (Kept, ValueError) as exc:\n        return refusal_message(exc)\n"
        "    except mod.Kept as exc:\n        return refusal_message(exc.__cause__)\n\n"
        "def helper(exc: Kept | None):\n    return refusal_message(exc)\n")
    assert refusal_callers_without_a_message({"planted.py": planted}) == [
        "planted.py:11: refusal_message of something that is not a caught refusal's name",
        "planted.py:9: refusal_message(exc) may be handed ValueError, which Kazma does not define",
    ]


#: Where a route lives: its answers reach a page.
_ROUTE_PLACES = ("kazma-ui/kazma_ui/", "kazma-gateway/kazma_gateway/routers/")
#: What answers an exception's own text (section 26 F).
_TEXT_OF = frozenset({"validation_error", "str", "repr"})


def refusals_answered_as_text(trees: dict[str, ast.AST]) -> list[str]:
    """``file:line`` of each route handler that catches one of Kazma's typed
    refusals (a class the packages define whose name ends ``Refused``) and
    hands the exception to ``validation_error``, ``str`` or an f-string:
    the exception's text, where its sentence is ``refusal_message(exc)``."""
    refused = {n.name for tree in trees.values() for n in ast.walk(tree)
               if isinstance(n, ast.ClassDef) and n.name.endswith("Refused")}
    found: list[str] = []
    for rel, tree in trees.items():
        if not rel.startswith(_ROUTE_PLACES):
            continue
        for handler in ast.walk(tree):
            if not (isinstance(handler, ast.ExceptHandler) and handler.name
                    and _type_names(handler.type) & refused):
                continue
            for node in ast.walk(ast.Module(body=handler.body, type_ignores=[])):
                as_text = (
                    isinstance(node, ast.Call) and _names(node.func) & _TEXT_OF
                    and any(isinstance(a, ast.Name) and a.id == handler.name for a in node.args)
                ) or (
                    isinstance(node, ast.FormattedValue) and isinstance(node.value, ast.Name)
                    and node.value.id == handler.name
                )
                if as_text:
                    found.append(f"{rel}:{node.lineno}")
    return sorted(set(found))


def test_every_route_answers_a_typed_refusal_with_its_sentence() -> None:
    trees = {rel: ast.parse((ROOT / rel).read_text(encoding="utf-8")) for rel in _product_files()}
    assert not refusals_answered_as_text(trees), (
        "A route answers one of Kazma's refusals with the exception's text: answer "
        "refusal_message(exc), and keep the sentence in the class's message field (section 26 F).")


def test_control_a_refusal_answered_as_text_is_found() -> None:
    planted = ast.parse(
        "class DocsRefused(Exception):\n    pass\n\n"
        "def route():\n    try:\n        work()\n"
        "    except DocsRefused as exc:\n        return {'error': validation_error(exc)}\n"
        "    except (DocsRefused, OSError) as exc:\n        return {'error': f'no: {exc}'}\n"
        "    except ValueError as exc:\n        return {'error': validation_error(exc)}\n")
    assert refusals_answered_as_text({"kazma-ui/kazma_ui/planted.py": planted}) == [
        "kazma-ui/kazma_ui/planted.py:10", "kazma-ui/kazma_ui/planted.py:8"]
    assert refusals_answered_as_text({"kazma-core/kazma_core/planted.py": planted}) == []


# ── A memory backup's path is made from its timestamp ─────────────────────


def _old_is_memory_backup_name(name: str, prefix: str) -> bool:
    stem, dot, ext = str(name or "").rpartition(".")
    head, _, stamp = stem.rpartition("_")
    return bool(dot) and ext == "db" and head == prefix and stamp.isdigit()


def test_a_memory_backup_is_named_by_its_number(tmp_path, monkeypatch) -> None:
    from kazma_core import paths
    from kazma_core.memory import backup

    monkeypatch.setattr(paths, "backups_dir", lambda: tmp_path)
    prefix = backup._memory_backup_prefix()
    named = backup.memory_backup_path(f"{prefix}_1794700000.db")
    assert named == tmp_path / f"{prefix}_1794700000.db"
    arabic_indic = "".join(chr(0x0661 + i) for i in range(3))
    for bad in (f"{prefix}_{arabic_indic}.db", f"{prefix}_{chr(0x00B2)}.db", f"{prefix}_0123.db",
                f"{prefix}_1.db\n", f"{prefix}_1", "memory_ops_1794700000.db", f"../{prefix}_1.db",
                f"{prefix}_{'9' * 21}.db", f"{prefix}_.db", ""):
        assert backup.memory_backup_path(bad) is None, repr(bad)
        assert not backup.is_memory_backup_name(bad), repr(bad)
    # The control: the old name check took Unicode digits as a timestamp.
    assert _old_is_memory_backup_name(f"{prefix}_{arabic_indic}.db", prefix)


# ── X's sign-in refusal comes back as Kazma's word ────────────────────────


def test_xs_refusal_is_one_of_kazmas_words() -> None:
    from kazma_ui import x_api

    words = x_api._X_SIGN_IN_WORDS
    cases = {None: "missing_code", "": "missing_code", "access_denied": "access_denied",
             " Invalid_Scope ": "invalid_scope", "server_error": "unreachable",
             "temporarily_unavailable": "unreachable", "unauthorized_client": "refused",
             "<script>alert(1)</script>": "refused", "evil.example/%2F%2F": "refused"}
    for given, expected in cases.items():
        said = x_api._sign_in_error_word(given)
        assert said == expected, given
        assert any(said is word for word in words), "the word is Kazma's constant, not the request's text"
    # The control: the old filter passed the letters of any text.
    assert re.sub(r"[^a-z_]", "", "<script>alert(1)</script>".lower())[:40] == "scriptalertscript"


def x_words_without_a_catalog_entry(words: tuple[str, ...]) -> list[str]:
    """The words the X callback can send that Settings -> X cannot word in
    both languages (``settings.xapi.err.<word>``, English and Arabic)."""
    from kazma_ui.i18n import TRANSLATIONS

    return [w for w in words
            if not all((TRANSLATIONS.get(f"settings.xapi.err.{w}") or {}).get(lang) for lang in ("en", "ar"))]


def test_every_word_the_x_callback_sends_is_worded_in_both_languages() -> None:
    from kazma_ui import x_api

    assert {"unreachable", "refused", "missing_code", "access_denied"} <= set(x_api._X_SIGN_IN_WORDS)
    assert not x_words_without_a_catalog_entry(x_api._X_SIGN_IN_WORDS)
    # The control: X's own code passed through, as the old map did for an
    # outage, has no entry, and the page would show the code itself.
    assert x_words_without_a_catalog_entry(("server_error",)) == ["server_error"]


def test_the_callback_goes_back_to_the_installs_own_address(monkeypatch) -> None:
    """``_own_base`` reads KAZMA_PUBLIC_URL (else KAZMA_HOST and the port),
    never the Host or X-Forwarded-Host a request sends."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from kazma_ui import x_api

    monkeypatch.setenv("KAZMA_PUBLIC_URL", "https://kazma.example")
    app = FastAPI()
    app.include_router(x_api.router)
    client = TestClient(app, follow_redirects=False)
    r = client.get("/api/x/oauth2/callback?error=access_denied",
                   headers={"Host": "evil.example", "X-Forwarded-Host": "evil.example"})
    assert r.status_code == 302
    assert r.headers["location"] == "https://kazma.example/settings?tab=x&x_oauth=error&msg=access_denied"


def test_a_token_x_would_not_issue_is_refused_with_kazmas_sentence() -> None:
    from kazma_ui import x_api

    assert x_api._token_problem("  demo-bearer-value-123  ", "Bearer token") is None
    assert x_api._token_problem("has spaces in it", "Bearer token") == (
        "That does not look like an X Bearer token: paste it as X shows it.")
