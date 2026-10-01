"""Every settings key Kazma's code writes is classified for backup and restore.

``kazma_core.settings_restore`` decides by ``KEY_RULES`` what a settings
backup carries and what a restore may write: Kazma's own state (approval
grants, a chat's long-task record, boot stamps) and credentials (sessions,
the password) are never written back. A key no rule names counts as a
setting -- so a new piece of runtime state written under a new key would ride
in every backup and be restored over the live value. This gate finds every
write to the settings store in product code and fails on a key that no rule
names explicitly.

A write's key is read from the source: a literal, the literal start of an
f-string, a constant (module, class or imported), a local assignment, a
``.format`` template, ``tenant_key(...)``, or a helper in the same module
that returns one of those. A write whose key the scan cannot read is listed
in ``DYNAMIC_WRITES`` with the keys it writes, or why it needs none.
Negative controls: a planted write of an unnamed key, and a planted write
the scan cannot read, are both reported.
"""

from __future__ import annotations

import ast
import functools
import subprocess
from pathlib import Path

import pytest
import yaml

from kazma_core.settings_restore import KEY_RULES, _rule_for
from kazma_core.settings_validation import _rules as validated_rules

REPO = Path(__file__).resolve().parents[1]

_WRITE_METHODS = frozenset({"set", "set_if_absent", "atomic_update", "delete", "batch_set"})
#: How product code names the settings store when it writes to it.
_STORE_RECEIVERS = frozenset({
    "store", "cs", "config_store", "self._cs", "self._config_store", "self.config_store",
    "get_config_store()", "_get_config_store()", "_cs()", "_gcs()", "_store()", "_get_sm()._cs",
})
#: The store's own machinery: it writes whatever key it is handed.
_SKIP_FILES = frozenset({
    "kazma-core/kazma_core/config_store.py",
    "kazma-core/kazma_core/settings_restore.py",
})

#: ``path::function`` of a write whose key the scan cannot read -> the key
#: patterns it writes (each must be named by a rule), or why there are none.
DYNAMIC_WRITES: dict[str, tuple[str, ...] | str] = {
    "kazma-core/kazma_core/agent/tool_builtins/system.py::config_save": (
        "the agent's config_save writes the key it is handed; "
        "safety.protected_config refuses every key that is not a setting"
    ),
    "kazma-core/kazma_core/lifecycle_notifier.py::_stamp": ("system.lifecycle.",),
    "kazma-core/kazma_core/memory/backends.py::save_backends_cfg": ("memory.backends.",),
    "kazma-core/kazma_core/memory/config.py::set_memory_flag": ("memory.",),
    "kazma-core/kazma_core/observability/cadence.py::stamp_run": ("observability.daily_digest.last_sent",),
    "kazma-core/kazma_core/safety/hitl_grants.py::clear_grants": ("hitl_grant.",),
    "kazma-core/kazma_core/safety/yolo.py::purge_expired_yolo": ("yolo.",),
    "kazma-core/kazma_core/security/web_sessions.py::purge_expired_sessions": ("web_session.",),
    "kazma-core/kazma_core/swarm/reliability.py::_release_probe_lease": ("swarm.breaker.",),
    "kazma-core/kazma_core/swarm/reliability.py::_try_acquire_probe_lease": ("swarm.breaker.",),
    "kazma-core/kazma_core/system/installer.py::_record_status": ("system.",),
    "kazma-tui/kazma_tui/settings_panel.py::_persist_setting": (
        "the TUI's Settings panel saves the settings it shows, through the server's "
        "single-setting route, or locally through the same settings_validation check"
    ),
    "kazma-ui/kazma_ui/app.py::_bootstrap_services": ("llm.",),
    "kazma-ui/kazma_ui/providers.py::_purge": ("connectors.",),
    "kazma-ui/kazma_ui/routes_direct/settings.py::_save": ("memory.v2.", "knowledge."),
    "kazma-ui/kazma_ui/settings.py::api_save_document_settings": ("documents.",),
    "kazma-ui/kazma_ui/settings.py::api_save_voice_settings": ("voice.",),
    "kazma-ui/kazma_ui/settings.py::api_update_settings": (
        "the page's batch save writes the keys the page sends (settings), each "
        "through settings_validation"
    ),
    "kazma-ui/kazma_ui/settings.py::api_update_single": (
        "the page's single save writes the key the page sends (a setting), "
        "through settings_validation"
    ),
    "kazma-ui/kazma_ui/x_api.py::x_disconnect": ("connectors.x.",),
    "kazma-ui/kazma_ui/x_api.py::x_save_credentials": ("connectors.x.",),
}


@functools.cache
def _product_sources() -> dict[str, str]:
    files = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard",
         "kazma-*/*.py", "kazma-*/**/*.py"],
        cwd=REPO, capture_output=True, text=True, check=True,
    ).stdout.split()
    return {
        f: (REPO / f).read_text(encoding="utf-8")
        for f in sorted(set(files))
        if "/tests/" not in f and "_tests" not in f and (REPO / f).is_file()
    }


# ── reading a key from the source ──────────────────────────────────────────

_UNREAD = None


def _module_constants(tree: ast.Module) -> dict[str, ast.expr]:
    out: dict[str, ast.expr] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    out[target.id] = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value:
            out[node.target.id] = node.value
    return out


def _class_constants(tree: ast.Module) -> dict[str, ast.expr]:
    out: dict[str, ast.expr] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, ast.Assign):
                    for target in item.targets:
                        if isinstance(target, ast.Name):
                            out[target.id] = item.value
    return out


def _imports(tree: ast.Module) -> dict[str, tuple[str, str]]:
    """name -> (module file, name there) for ``from kazma_x.y import name``."""
    out: dict[str, tuple[str, str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("kazma_"):
            pkg = node.module.split(".")[0]
            path = f"{pkg.replace('_', '-')}/{node.module.replace('.', '/')}.py"
            for alias in node.names:
                out[alias.asname or alias.name] = (path, alias.name)
    return out


class _Reader:
    """Reads the key a write names, as a pattern: a whole key, or the literal
    start of one ending where the source stops being literal."""

    def __init__(self, sources: dict[str, str]) -> None:
        self.sources = sources
        self.trees: dict[str, ast.Module] = {}

    def tree(self, path: str) -> ast.Module | None:
        if path not in self.trees:
            text = self.sources.get(path)
            if text is None:
                return None
            try:
                self.trees[path] = ast.parse(text)
            except SyntaxError:
                return None
        return self.trees[path]

    def functions(self, path: str) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
        tree = self.tree(path)
        out: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
        for node in ast.walk(tree) if tree else ():
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out.setdefault(node.name, node)
        return out

    def read(self, node: ast.expr, path: str, func: ast.AST | None, depth: int = 0) -> list[str] | None:
        """The patterns *node* can be, or None when the source does not say."""
        if depth > 8:
            return None
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return [node.value]
        if isinstance(node, ast.JoinedStr):
            head = ""
            for part in node.values:
                if isinstance(part, ast.Constant):
                    head += str(part.value)
                    continue
                # f"{_PREFIX}{thread_id}": a constant reads on; anything else ends the literal start.
                fixed = self.literal(part.value, path) if isinstance(part, ast.FormattedValue) else None
                if fixed is None:
                    return [head] if head else None
                head += fixed
            return [head]
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left = self.read(node.left, path, func, depth + 1)
            if left is None:
                return None
            right = self.read(node.right, path, func, depth + 1)
            if right is None:
                return left  # the rest is not literal: the left is the start
            return [a + b for a in left for b in right]
        if isinstance(node, ast.Name):
            return self.name(node.id, path, func, depth)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in ("cls", "self"):
            tree = self.tree(path)
            value = _class_constants(tree).get(node.attr) if tree else None
            return self.read(value, path, None, depth + 1) if value is not None else None
        if isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Attribute) and fn.attr == "format":
                base = self.read(fn.value, path, func, depth + 1)
                return [b.split("{", 1)[0] for b in base] if base else None
            name = fn.id if isinstance(fn, ast.Name) else None
            if name == "tenant_key" and node.args:
                return self.read(node.args[0], path, func, depth + 1)
            target = self.functions(path).get(name) if name else None
            if target is not None:
                found: list[str] = []
                for ret in ast.walk(target):
                    if isinstance(ret, ast.Return) and ret.value is not None:
                        got = self.read(ret.value, path, target, depth + 1)
                        if got is None:
                            return None
                        found.extend(got)
                return found or None
        return None

    def literal(self, node: ast.expr, path: str, depth: int = 0) -> str | None:
        """*node* as a fixed string: a literal, or a constant bound to one
        (module-level or imported); anything else None."""
        if depth > 6:
            return None
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left, right = self.literal(node.left, path, depth + 1), self.literal(node.right, path, depth + 1)
            return left + right if left is not None and right is not None else None
        if isinstance(node, ast.Name):
            tree = self.tree(path)
            if tree is None:
                return None
            const = _module_constants(tree).get(node.id)
            if const is not None:
                return self.literal(const, path, depth + 1)
            imported = _imports(tree).get(node.id)
            if imported is not None and imported[0] in self.sources:
                return self.literal(ast.Name(id=imported[1]), imported[0], depth + 1)
        return None

    def name(self, ident: str, path: str, func: ast.AST | None, depth: int) -> list[str] | None:
        if func is not None:
            values = [
                n.value for n in ast.walk(func)
                if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == ident for t in n.targets)
            ]
            if values:
                found: list[str] = []
                for value in values:
                    got = self.read(value, path, func, depth + 1)
                    if got is None:
                        return None
                    found.extend(got)
                return found
        tree = self.tree(path)
        if tree is None:
            return None
        const = _module_constants(tree).get(ident)
        if const is not None:
            return self.read(const, path, None, depth + 1)
        imported = _imports(tree).get(ident)
        if imported is not None and imported[0] in self.sources:
            return self.name(imported[1], imported[0], None, depth + 1)
        return None

    def batch_keys(self, call: ast.Call, path: str, func: ast.AST | None) -> list[str] | None:
        """The keys of ``batch_set([(key, value, category), ...])``."""
        arg = call.args[0]
        tuples: list[ast.expr] = []
        if isinstance(arg, (ast.List, ast.Tuple)):
            tuples = list(arg.elts)
        elif isinstance(arg, ast.Name) and func is not None:
            for n in ast.walk(func):
                if isinstance(n, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id == arg.id for t in n.targets
                ) and isinstance(n.value, (ast.List, ast.Tuple)):
                    tuples.extend(n.value.elts)
                elif (
                    isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and n.func.attr == "append" and isinstance(n.func.value, ast.Name)
                    and n.func.value.id == arg.id and n.args
                ):
                    tuples.append(n.args[0])
        if not tuples:
            return None
        found: list[str] = []
        for item in tuples:
            if not (isinstance(item, ast.Tuple) and item.elts):
                return None
            got = self.read(item.elts[0], path, func)
            if got is None:
                return None
            found.extend(got)
        return found


def settings_writes(sources: dict[str, str]) -> tuple[dict[str, set[str]], set[str]]:
    """(``path::function`` -> key patterns it writes, sites the scan cannot read)."""
    reader = _Reader(sources)
    found: dict[str, set[str]] = {}
    unread: set[str] = set()

    def visit(node: ast.AST, path: str, func: ast.AST | None, qual: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                visit(child, path, child, child.name)
                continue
            if (
                isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute)
                and child.func.attr in _WRITE_METHODS and child.args
                and ast.unparse(child.func.value) in _STORE_RECEIVERS
            ):
                site = f"{path}::{qual}"
                if child.func.attr == "batch_set":
                    keys = reader.batch_keys(child, path, func)
                else:
                    keys = reader.read(child.args[0], path, func)
                if keys is None or not all(keys):
                    unread.add(site)
                else:
                    found.setdefault(site, set()).update(keys)
            visit(child, path, func, qual)

    for path, text in sources.items():
        if path in _SKIP_FILES:
            continue
        tree = reader.tree(path)
        if tree is not None:
            visit(tree, path, None, "<module>")
    return found, unread


@functools.cache
def _scan() -> tuple[dict[str, set[str]], set[str]]:
    """The product scan, once per test run."""
    return settings_writes(dict(_product_sources()))


def _unnamed(patterns: set[str]) -> set[str]:
    return {p for p in patterns if _rule_for(p) is None}


# ── the gate ────────────────────────────────────────────────────────────────


def test_every_key_the_code_writes_is_named_by_a_rule() -> None:
    found, unread = _scan()
    assert len(found) >= 60, f"the scan found too few writes to be working: {len(found)}"
    unnamed = {site: sorted(_unnamed(keys)) for site, keys in found.items() if _unnamed(keys)}
    assert not unnamed, (
        "Settings keys written by product code that no KEY_RULES entry names "
        "(kazma_core/settings_restore.py). Say whether each is a setting, Kazma's "
        "own state, a credential or learned data -- a key left out rides in every "
        "backup and is restored over the live value:\n"
        + "\n".join(f"  {site}: {keys}" for site, keys in sorted(unnamed.items()))
    )
    undeclared = sorted(unread - DYNAMIC_WRITES.keys())
    assert not undeclared, (
        "Settings writes whose key the scan cannot read; add each to DYNAMIC_WRITES "
        "with the keys it writes (or why it needs none):\n  " + "\n  ".join(undeclared)
    )


def test_every_declared_dynamic_write_is_real_and_named() -> None:
    found, unread = _scan()
    stale = sorted(site for site in DYNAMIC_WRITES if site not in unread)
    assert not stale, (
        f"declared, but the scan reads these now or they are gone (remove them): {stale}"
    )
    for site, keys in DYNAMIC_WRITES.items():
        if isinstance(keys, tuple):
            assert keys and not _unnamed(set(keys)), (site, sorted(_unnamed(set(keys))))
        else:
            assert len(keys) > 20, f"{site}: say why there is no key to name"


def _strings_in_product() -> set[str]:
    out: set[str] = set()
    for text in _product_sources().values():
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and len(node.value) < 120:
                out.add(node.value)
    return out


def _shipped_keys() -> set[str]:
    from kazma_core.config_store import shipped_settings

    data = yaml.safe_load((REPO / "kazma.yaml").read_text(encoding="utf-8")) or {}
    return {key for key, _value, _cat in shipped_settings(data)}


def test_every_rule_names_a_key_kazma_has() -> None:
    """A rule naming a key nothing writes or reads is a typo: the real key
    would then fall to the default."""
    strings = _strings_in_product() | _shipped_keys() | set(validated_rules())
    found, _unread = _scan()
    written = set().union(*found.values())
    declared = {k for keys in DYNAMIC_WRITES.values() if isinstance(keys, tuple) for k in keys}
    known = strings | written | declared
    missing = sorted(
        rule.pattern for rule in KEY_RULES
        if not any(s == rule.pattern or s.startswith(rule.pattern) or rule.pattern.startswith(s) and s.endswith(".")
                   for s in known if s)
    )
    assert not missing, f"rules naming no key Kazma has (typo?): {missing}"


def test_the_gate_sees_an_unnamed_key_and_an_unreadable_one() -> None:
    """Negative controls (§28)."""
    planted = {
        "kazma-x/kazma_x/m.py": (
            "_KEY = 'brand_new.cursor'\n"
            "def remember(cs, thread):\n"
            "    cs.set(f'another_new.{thread}', 1)\n"
            "    cs.set(_KEY, 2)\n"
            "def forget(cs, key):\n"
            "    cs.delete(key)\n"
            "def fine(cs):\n"
            "    cs.set('agent.language', 'ar')\n"
        )
    }
    found, unread = settings_writes(planted)
    assert found["kazma-x/kazma_x/m.py::remember"] == {"another_new.", "brand_new.cursor"}
    assert _unnamed(found["kazma-x/kazma_x/m.py::remember"]) == {"another_new.", "brand_new.cursor"}
    assert not _unnamed(found["kazma-x/kazma_x/m.py::fine"])
    assert unread == {"kazma-x/kazma_x/m.py::forget"}


@pytest.mark.parametrize("key,kind", [
    ("web_session.abc", "credential"),
    ("account.password_hash", "credential"),
    ("security.secret", "credential"),
    ("notifications.push.subscriptions", "credential"),
    ("task_grant.t1", "runtime"),
    ("long_task.continue.t1", "runtime"),
    ("memory.v2.rehydrate", "runtime"),
    ("tenant.acme.yolo.t1", "runtime"),
    ("self_improvement.agent_evolution", "learned"),
    ("memory.v2.summaries_min_turns", "setting"),
    ("notifications.lifecycle.events", "setting"),
    ("providers.list", "setting"),
    ("system_prompt", "setting"),
])
def test_known_keys_classify_as_they_should(key: str, kind: str) -> None:
    from kazma_core.settings_restore import classify

    assert classify(key) == kind
