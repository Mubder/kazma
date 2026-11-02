"""Every key a Settings control writes has a reader outside the Settings page.

The 2026-11 functional audit found Settings controls that saved a key
nothing read (FA-250: Settings -> Tools' switch wrote ``tools.<name>.enabled``
and only its own listing read it -- a tool switched off was still offered to
the model and still ran; FA-125: Non-Stop's "Per-Tool Timeout"). A saved
switch that changes nothing tells the owner something false.

The keys are enumerated from the Settings writers (``settings_manager``, the
Settings routes) and the keys the page saves by name (``single(...)``). A key
is read when a product module other than those holds:

* the key itself as a literal (an f-string counts by its template);
* an ancestor of at least two segments, and every remaining segment;
* an f-string ancestor template (``f"agent.nonstop.{key}"``) and the rest of
  the key as one literal (``"watchdog.tool_timeout_seconds"``);
* a whole-section read of its first segment (``get_category("proxy")``,
  ``snapshot("proxy")``, ``get("proxy")``) and every remaining segment.

What none of these sees is declared in ``DECLARED`` with its reader
(``module:function``); the gate checks the function exists and names the
key's last segment, so a declaration is checked too.
"""

from __future__ import annotations

import ast
import importlib
import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
WRITERS = (
    "kazma-core/kazma_core/settings_manager.py",
    "kazma-ui/kazma_ui/settings.py",
    "kazma-ui/kazma_ui/routes_direct/settings.py",
)
PAGE_SCRIPTS = re.compile(r"kazma-ui/kazma_ui/static/js/settings[^/]*\.js$")
PRODUCT = re.compile(r"kazma-(core|ui|gateway|tui|cli|skills)/kazma_[a-z]+/.+\.py$")
KEYISH = re.compile(r"^[a-z][a-z0-9_]*(\.([a-z0-9_]+|\{\}))+$")
#: Key-shaped literals in the writers that are not settings: file names and
#: catalog keys.
NOT_SETTINGS = re.compile(r"\.(html|js|css|json|yaml|md)$|^tool\.desc\.")

#: Keys a reader reaches in a way the rules above do not see: key -> the
#: function that reads it, and why the rules miss it.
DECLARED: dict[str, tuple[str, str, str]] = {
    # key: (the file that reads it, what it reads it by, why the rules miss it)
    "appearance.theme": (
        "kazma-ui/kazma_ui/static/js/modules/components.js", "d.theme",
        "every page reads it through /api/settings/appearance (kazmaApp); the "
        "server renders it too (app.py _read_theme)",
    ),
    "appearance.font_size": (
        "kazma-ui/kazma_ui/static/js/modules/components.js", "d.font_size",
        "every page reads it through /api/settings/appearance (kazmaApp)",
    ),
    "appearance.accent_color": (
        "kazma-ui/kazma_ui/static/js/modules/components.js", "applyAccentColor(d.accent_color)",
        "every page applies it through /api/settings/appearance when the owner chose one",
    ),
}


def _fields(target: str) -> list[str]:
    module, _, name = target.partition(":")
    obj = getattr(importlib.import_module(module), name)
    if isinstance(obj, dict):
        return list(obj)
    if isinstance(obj, (list, tuple)):
        return list(obj)
    return list(obj.model_fields)


#: A writer that writes ``<section>.<field>`` from the request: the fields it
#: takes, so each is checked (the template alone says nothing).
EXPANSIONS: dict[str, tuple[str, tuple[str, ...]]] = {
    # personality goes through set_personality, not agent.personality.
    "agent.{}": ("kazma_ui.models:AgentConfigUpdate", ("personality",)),
    "appearance.{}": ("kazma_core.settings_manager:DEFAULT_APPEARANCE", ()),
    "logging.{}": ("kazma_core.settings_manager:_LOGGING_FIELDS", ()),
    "safety.{}": ("kazma_core.settings_manager:_SAFETY_FIELDS", ()),
    "proxy.{}": ("kazma_core.settings_manager:_PROXY_FIELDS", ()),
}


def expand(keys: dict[str, set[str]]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for key, src in keys.items():
        if key in EXPANSIONS:
            target, skip = EXPANSIONS[key]
            section = key[: -len(".{}")]
            for field in _fields(target):
                if field not in skip:
                    out.setdefault(f"{section}.{field}", set()).update(src)
        else:
            out.setdefault(key, set()).update(src)
    return out


def _files() -> list[str]:
    return subprocess.run(["git", "ls-files"], cwd=REPO, capture_output=True, text=True,
                          check=True).stdout.split()


def _template(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value if isinstance(v, ast.Constant) else "{}" for v in node.values)
    return None


def _literals(source: str) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        t = _template(node)
        if t:
            out.add(t)
    return out


#: The calls that write a settings key (their first argument).
WRITE_CALLS = {"set", "set_if_absent", "atomic_update", "batch_set", "set_tool_enabled",
               "set_skill_enabled"}
#: Writers whose first argument is a name, not the key: the key they write.
KEY_OF_CALL = {"set_tool_enabled": "tools.{}.enabled", "set_skill_enabled": "skills.enabled.{}"}


def _keys_written_in(func: ast.AST) -> set[str]:
    """The keys *func* writes: a write call's key, a batch's payload keys
    (a dict literal, or the ``(key, value, category)`` tuples a batch is
    built from) -- in a function that writes at all."""
    calls = [n for n in ast.walk(func) if isinstance(n, ast.Call)
             and isinstance(n.func, (ast.Attribute, ast.Name))
             and (getattr(n.func, "attr", None) or getattr(n.func, "id", None)) in WRITE_CALLS]
    if not calls:
        return set()
    out: set[str] = set()
    for call in calls:
        name = getattr(call.func, "attr", None) or getattr(call.func, "id", None)
        if name in KEY_OF_CALL:
            out.add(KEY_OF_CALL[name])
        elif call.args:
            t = _template(call.args[0])
            if t:
                out.add(t)
    for node in ast.walk(func):
        if isinstance(node, ast.Dict):
            out.update(t for k in node.keys if k is not None and (t := _template(k)))
        elif isinstance(node, ast.Tuple) and node.elts:
            t = _template(node.elts[0])
            if t:
                out.add(t)
    return {k for k in out if KEYISH.match(k) and not NOT_SETTINGS.search(k)}


def written_keys(files: list[str]) -> dict[str, set[str]]:
    """Every settings key the Settings writers write and the page saves by name."""
    keys: dict[str, set[str]] = {}
    for f in WRITERS:
        tree = ast.parse((REPO / f).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for k in _keys_written_in(node):
                    keys.setdefault(k, set()).add(f)
    for f in files:
        if PAGE_SCRIPTS.match(f):
            text = (REPO / f).read_text(encoding="utf-8")
            for m in re.finditer(r"single\('([a-z0-9_.]+)'", text):
                keys.setdefault(m.group(1), set()).add(f)
    return keys


#: The TUI writes settings too (roadmap theme 4): its Settings panel from
#: tables of controls, its chat's ``/config`` through ``/api/settings/single``.
TUI_WRITERS = ("kazma-tui/kazma_tui/settings_panel.py", "kazma-tui/kazma_tui/chat.py")
#: Files whose reads of a key do not make its control effective: they only
#: show it back (the Settings writers, the TUI's Settings panel).
NOT_READERS = (*WRITERS, "kazma-tui/kazma_tui/settings_panel.py")


def tui_written_keys() -> dict[str, set[str]]:
    """The keys the TUI writes: a control table's key (a class-level list of
    tuples), the panel's ``_persist_setting(<key>)`` (a literal or a module
    constant), and a request payload naming its ``"key"``."""
    keys: dict[str, set[str]] = {}

    def add(key: str | None, f: str) -> None:
        if key and KEYISH.match(key) and not NOT_SETTINGS.search(key):
            keys.setdefault(key, set()).add(f)

    for f in TUI_WRITERS:
        tree = ast.parse((REPO / f).read_text(encoding="utf-8"))
        consts = {
            target.id: node.value.value
            for node in tree.body if isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)
            for target in node.targets if isinstance(target, ast.Name)
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                for item in node.body:
                    if isinstance(item, ast.Assign) and isinstance(item.value, (ast.List, ast.Tuple)):
                        for row in item.value.elts:
                            if isinstance(row, ast.Tuple):
                                for cell in row.elts:
                                    add(_template(cell), f)
            elif (isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "_persist_setting"
                  and node.args):
                arg = node.args[0]
                add(_template(arg) or (consts.get(arg.id) if isinstance(arg, ast.Name) else None), f)
            elif isinstance(node, ast.Dict):
                for k, v in zip(node.keys, node.values):
                    if k is not None and _template(k) == "key":
                        add(_template(v), f)
    return keys


def reader_index(files: list[str]) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """``(literals per file, categories read per file)`` of product modules
    other than the Settings writers and the TUI's Settings panel."""
    lits: dict[str, set[str]] = {}
    cats: dict[str, set[str]] = {}
    for f in files:
        if not PRODUCT.match(f) or f in NOT_READERS:
            continue
        source = (REPO / f).read_text(encoding="utf-8")
        try:
            lits[f] = _literals(source)
        except SyntaxError:
            continue
        # A whole section read ON A SETTINGS STORE: get_category / snapshot,
        # or get() of a root key (get merges a missing key's children,
        # AGENTS.md section 8). A dict's .get("tools") is not one.
        cats[f] = set(re.findall(
            r"(?:\bstore|\bcs|\b_cs|\bconfig_store|get_config_store\(\))\s*\.\s*"
            r"(?:get_category|snapshot|get)\(\s*['\"]([a-z0-9_]+)['\"]", source))
    return lits, cats


def reader_of(key: str, lits: dict[str, set[str]], cats: dict[str, set[str]]) -> str | None:
    segs = key.split(".")
    for f, held in lits.items():
        if key in held:
            return f
        for i in range(1, len(segs)):
            ancestor, rest = ".".join(segs[:i]), segs[i:]
            if i >= 2 and ancestor in held and all(r == "{}" or r in held for r in rest):
                return f
            if f"{ancestor}.{{}}" in held and ".".join(rest) in held:
                return f
            if i == 1 and ancestor in cats.get(f, set()) and all(r == "{}" or r in held for r in rest):
                return f
    return None


def unread(keys: dict[str, set[str]], lits, cats) -> list[str]:
    return sorted(k for k in keys if k not in DECLARED and reader_of(k, lits, cats) is None)


def test_every_key_a_settings_control_writes_is_read():
    files = _files()
    keys = expand(written_keys(files))
    assert len(keys) > 60  # the enumeration found the writers
    for key, src in tui_written_keys().items():
        keys.setdefault(key, set()).update(src)
    assert not [k for k in keys if k.endswith(".{}") and k.count(".") == 1], (
        "a section written field by field from the request needs EXPANSIONS")
    lits, cats = reader_index(files)
    missing = unread(keys, lits, cats)
    assert not missing, (
        "Settings writes keys nothing outside the Settings page reads (a control that "
        "changes nothing). Wire a reader, remove the control, or declare the reader in "
        "DECLARED:\n  " + "\n  ".join(f"{k}  <- {', '.join(sorted(keys[k]))}" for k in missing)
    )


@pytest.mark.parametrize("key", sorted(DECLARED))
def test_a_declared_reader_reads_its_key(key):
    path, snippet, why = DECLARED[key]
    assert why.strip() and path in _files()
    assert snippet in (REPO / path).read_text(encoding="utf-8"), f"{path} no longer holds {snippet!r}"


def test_the_tools_switch_was_a_dead_control():
    """Negative control: with the switch's reader taken out (the code before
    FA-250), the gate finds ``tools.{}.enabled`` unread."""
    files = _files()
    keys = written_keys(files)
    lits, cats = reader_index(files)
    assert "tools.{}.enabled" in keys and reader_of("tools.{}.enabled", lits, cats)
    without = {f: held - {"tools.{}.enabled"} for f, held in lits.items()
               if f != "kazma-core/kazma_core/skills/switches.py"}
    without["kazma-core/kazma_core/skills/switches.py"] = set()
    assert "tools.{}.enabled" in unread({"tools.{}.enabled": {"x"}}, without, cats)


def test_the_tui_writes_are_read_by_the_gate():
    """The TUI's tables and payloads are enumerated (roadmap theme 4): a key
    the panel writes from a table, one through a module constant, and one the
    chat's /config sends by name."""
    keys = tui_written_keys()
    assert len(keys) >= 15
    assert {"memory.enabled", "memory.v2.recall_ttl_days", "memory.tenant_mode"} <= set(keys)


def test_negative_control_a_dead_tui_toggle_is_found():
    """A TUI table key no product module reads is reported."""
    lits, cats = reader_index(_files())
    assert unread({"memory.v2.no_such_switch": {"kazma-tui/kazma_tui/settings_panel.py"}},
                  lits, cats) == ["memory.v2.no_such_switch"]
