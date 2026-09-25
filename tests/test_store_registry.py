"""Kazma's own stores: declared once, read back by a tool, refused raw, carried whole.

Born 2026-09-25. The model could save drafts but had no tool to read them;
asked for "the remaining posts" it spent 67 tool calls and an approved
``python_exec`` byte-dumping ``agent_artifacts.db``. Behind that sat one
decision — "is this file one of Kazma's stores?" — answered by five
hand-kept lists that had drifted apart, and a migration bundle that
silently left eight stores behind.

Every gate here enumerates from the real source (product code, the tool
registry, the migration modules), and each has a negative control proving it
fails on a planted violation. When one fails, fix the code or the
declaration in ``kazma_core/store_registry.py``; do not loosen the gate.

1. every database file named in product code is declared in ``STORES``
2. every tool that can change anything declares where it writes and how
   the model reads it back (``TOOL_WRITES``)
3. a write to a Kazma store names a reader the model can call without an
   approval — per WRITER, because a per-store rule passes when any other
   writer of the same store has one (the scratchpad did; the drafts didn't)
4. context feeders named as readers exist and are wired into the prompt
5. every store says how it crosses machines; "bundle" stores travel both
   ways, and the bundle list is derived, not kept by hand
6. every door the model can try refuses every store and names its reader;
   the user's own sandbox database passes every door
7. no store path is built from the process working directory
"""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PRODUCT_ROOTS = (
    "kazma-core/kazma_core",
    "kazma-ui/kazma_ui",
    "kazma-gateway/kazma_gateway",
    "kazma-skills/kazma_skills",
    "kazma-cli/kazma_cli",
    "kazma-tui/kazma_tui",
)
DB_NAME = re.compile(r"^[\w.\-]+\.(?:db|sqlite|sqlite3)$", re.IGNORECASE)
DB_TAIL = re.compile(r"\.(?:db|sqlite|sqlite3)$", re.IGNORECASE)


def _product_sources() -> dict[str, str]:
    out: dict[str, str] = {}
    for root in PRODUCT_ROOTS:
        for p in (ROOT / root).rglob("*.py"):
            if "tests" in p.parts or "__pycache__" in p.parts:
                continue
            out[p.relative_to(ROOT).as_posix()] = p.read_text(encoding="utf-8", errors="replace")
    return out


def _registered_tools() -> set[str]:
    from kazma_core.agent.tool_builtins import register_builtin_tools
    from kazma_core.agent.tool_registry import LocalToolRegistry

    registry = LocalToolRegistry()
    register_builtin_tools(registry)
    return set(registry._tools)


# ── 1. every database named in the code is declared ──────────────────────


def _undeclared_store_names(sources: dict[str, str]) -> list[str]:
    from kazma_core.store_registry import DYNAMIC_NAME_SITES, store_name_for

    problems: list[str] = []
    for rel, text in sources.items():
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        # The literal pieces of an f-string are Constants too; they belong to
        # the runtime-built name, judged once as a JoinedStr below.
        fstring_parts = {
            id(part)
            for node in ast.walk(tree)
            if isinstance(node, ast.JoinedStr)
            for part in node.values
        }
        for node in ast.walk(tree):
            if id(node) in fstring_parts:
                continue
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                s = node.value.strip()
                if not s or any(ch.isspace() for ch in s):
                    continue  # prose and docstrings, not a filename
                name = s.replace("\\", "/").rsplit("/", 1)[-1]
                if DB_NAME.match(name) and store_name_for(name) is None:
                    problems.append(f"{rel}:{node.lineno} names {name!r}")
            elif isinstance(node, ast.JoinedStr) and node.values:
                tail = node.values[-1]
                if (
                    isinstance(tail, ast.Constant)
                    and isinstance(tail.value, str)
                    and DB_TAIL.search(tail.value)
                    and rel not in DYNAMIC_NAME_SITES
                ):
                    problems.append(f"{rel}:{node.lineno} builds a database name at runtime")
    return problems


def test_every_database_named_in_the_code_is_declared():
    problems = _undeclared_store_names(_product_sources())
    assert not problems, (
        "A database file Kazma does not know it keeps: the doors cannot say what\n"
        "it holds or which tool reads it, and a migration bundle leaves it behind.\n"
        "Declare it in kazma_core/store_registry.py (STORES, or STORE_FAMILIES +\n"
        "DYNAMIC_NAME_SITES for a name built at runtime):\n  " + "\n  ".join(problems)
    )


def test_undeclared_database_names_are_caught():
    """Negative control: a new literal store and a new runtime-built one."""
    planted = {
        "kazma-core/kazma_core/planted.py": textwrap.dedent(
            """
            from pathlib import Path
            A = Path("somewhere") / "brand_new_store.db"
            def b(x):
                return f"{x}_cache.sqlite3"
            C = "kazma-data/agent_artifacts.db"   # declared: must NOT be reported
            """
        )
    }
    problems = _undeclared_store_names(planted)
    assert len(problems) == 2, problems
    assert any("brand_new_store.db" in p for p in problems)
    assert any("runtime" in p for p in problems)


# ── 2 + 3. every writer declares where it writes and how it is read back ──


def _undeclared_writers(tool_names, tiers, writes) -> list[str]:
    return sorted(n for n in tool_names if tiers.get(n) in ("write", "danger") and n not in writes)


def _readback_problems(writes, tiers, feeders, registered) -> list[str]:
    from kazma_core.store_registry import store_name_for

    problems: list[str] = []
    for tool, write in writes.items():
        for group in write.groups():
            stores = [t for t in group.targets if store_name_for(t)]
            for t in group.targets:
                if store_name_for(t):
                    continue
                if t in ("workspace", "host", "none") or t.startswith(("external:", "user:")):
                    continue
                problems.append(f"{tool}: target {t!r} is neither a declared store nor a known kind")
            if not group.readers:
                if stores:
                    problems.append(
                        f"{tool}: writes {', '.join(stores)} and names no reader — the "
                        "model could save it and never read it back"
                    )
                elif not group.note:
                    problems.append(f"{tool}: no reader and no note saying why none is needed")
            for reader in group.readers:
                if reader.startswith("context:"):
                    if reader.split(":", 1)[1] not in feeders:
                        problems.append(f"{tool}: unknown context feeder {reader!r}")
                    continue
                tier = tiers.get(reader)
                if tier not in ("read", "safe"):
                    problems.append(
                        f"{tool}: reader {reader!r} is tier {tier!r} — reading back what "
                        "Kazma saved must not need an approval"
                    )
                if tool in registered and reader not in registered:
                    problems.append(f"{tool}: reader {reader!r} is not registered in this build")
    return problems


def test_every_state_changing_tool_declares_its_readback():
    from kazma_core.safety.hitl import TOOL_TIERS
    from kazma_core.store_registry import TOOL_WRITES

    missing = _undeclared_writers(_registered_tools(), TOOL_TIERS, TOOL_WRITES)
    assert not missing, (
        "Tools that can change something but do not say where it lands or how the\n"
        "model reads it back. Add each to TOOL_WRITES in kazma_core/store_registry.py\n"
        "(a Kazma store needs a read-tier reader; nothing else is accepted):\n  "
        + ", ".join(missing)
    )
    stale = sorted(n for n in TOOL_WRITES if n not in TOOL_TIERS)
    assert not stale, f"TOOL_WRITES names tools Kazma does not have: {stale}"


def test_every_write_to_a_kazma_store_has_a_no_approval_reader():
    from kazma_core.safety.hitl import TOOL_TIERS
    from kazma_core.store_registry import CONTEXT_FEEDERS, TOOL_WRITES

    problems = _readback_problems(TOOL_WRITES, TOOL_TIERS, CONTEXT_FEEDERS, _registered_tools())
    assert not problems, "\n".join(problems)


def test_readback_gates_catch_a_write_only_store():
    """Negative controls: the 2026-09-25 shape, and its near misses."""
    from kazma_core.store_registry import CONTEXT_FEEDERS, Write

    tiers = {"save_widget": "write", "list_widgets": "read", "python_exec": "danger"}
    assert _undeclared_writers({"save_widget", "list_widgets"}, tiers, {}) == ["save_widget"]

    cases = {
        # save_proposal before list_proposals existed
        "write_only": Write(("agent_artifacts.db",)),
        # a "reader" that needs an approval is how the dump got approved
        "approved_reader": Write(("agent_artifacts.db",), ("python_exec",)),
        "no_note": Write(("workspace",)),
        "undeclared_target": Write(("brand_new_store.db",), ("list_widgets",)),
        "unknown_feeder": Write(("task_ledgers.db",), ("context:nope",)),
    }
    problems = _readback_problems(cases, tiers, CONTEXT_FEEDERS, set())
    for name in cases:
        assert any(p.startswith(name + ":") for p in problems), (name, problems)

    fine = {"ok": Write(("agent_artifacts.db",), ("list_widgets",))}
    assert _readback_problems(fine, tiers, CONTEXT_FEEDERS, set()) == []


def test_context_feeders_are_wired_into_the_prompt():
    import importlib

    from kazma_core.store_registry import CONTEXT_FEEDERS

    assembler = (ROOT / "kazma-core/kazma_core/agent/graph_supervisor.py").read_text(encoding="utf-8")
    for name, (module, attr, _what) in CONTEXT_FEEDERS.items():
        obj = importlib.import_module(module)
        for part in attr.split("."):
            obj = getattr(obj, part)
        assert callable(obj), name
        assert attr.rsplit(".", 1)[-1] in assembler, (
            f"context feeder {name!r} ({attr}) is not used by graph_supervisor.py — "
            "declared as the reader, it would feed nothing"
        )


# ── 5. every store says how it crosses machines ──────────────────────────


def test_every_store_has_a_migration_disposition():
    from kazma_core.store_registry import MIGRATION_DISPOSITIONS, STORE_FAMILIES, STORES

    for name, store in {**STORES, **STORE_FAMILIES}.items():
        assert store.holds, name
        assert store.migration in MIGRATION_DISPOSITIONS, (name, store.migration)
        if store.migration != "bundle":
            assert store.reason, f"{name}: say why it does not travel in a bundle"


def test_every_bundle_store_is_exported_and_restored():
    from kazma_core.migration import exporter, importer
    from kazma_core.store_registry import bundle_store_names

    exported = set(exporter._DATA_DIR_DBS) | {arc for _, arc in exporter._DATA_DBS}
    restored = set(importer._BUNDLE_DB_TO_DEST_RESOLVER)
    for name in bundle_store_names():
        assert name in exported, f"{name} is marked 'bundle' but the exporter leaves it behind"
        assert name in restored, f"{name} is exported but the importer never restores it"


def test_the_bundle_list_is_derived_not_kept_by_hand(monkeypatch):
    """Negative control: a newly declared store joins the bundle by itself."""
    from kazma_core import store_registry
    from kazma_core.migration import exporter

    monkeypatch.setitem(
        store_registry.STORES, "brand_new_store.db", store_registry.Store("x", "bundle")
    )
    assert "brand_new_store.db" in exporter._data_dir_dbs()


def _run(script: str, env: dict[str, str], cwd: Path) -> str:
    proc = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(script)],
        env=env, cwd=str(cwd), capture_output=True, text=True, timeout=240,
    )
    assert proc.returncode == 0, proc.stderr[-3000:]
    return proc.stdout


def test_a_migration_carries_the_stores_it_used_to_drop(tmp_path):
    """Export from one data dir, import into another, read the drafts back.

    Subprocesses: data_dir() comes from the environment, and a vault key is
    pinned to a dummy so neither side reads or writes a real one.
    """
    src, dst = tmp_path / "src" / "kazma-data", tmp_path / "dst" / "kazma-data"
    src.mkdir(parents=True)
    dst.mkdir(parents=True)
    bundle = tmp_path / "bundle.zip"
    base = {k: v for k, v in os.environ.items() if not k.startswith("KAZMA_")}
    base.update({"KAZMA_DB_BACKEND": "sqlite", "KAZMA_VAULT_KEY": "test-only-not-a-secret"})

    out = _run(
        f"""
        import sqlite3, time, zipfile
        from kazma_core.agent.artifacts import ArtifactStore
        from kazma_core.x_api.schedule import XScheduledStore
        from kazma_core.paths import data_dir
        d = data_dir()
        ArtifactStore(d / "agent_artifacts.db").save_proposal("default", "t", "tweets", ["carried draft"])
        XScheduledStore(d / "x_scheduled.db").add(text="booked", fire_at=time.time() + 3600)
        for name in ("hitl_gates.db", "checkpoints_tenantA.db"):
            c = sqlite3.connect(str(d / name)); c.execute("CREATE TABLE t (v)"); c.commit(); c.close()
        from kazma_core.migration.exporter import export_bundle
        out = export_bundle({str(bundle)!r}, include_assets=False)
        print("\\n".join(sorted(zipfile.ZipFile(out).namelist())))
        """,
        {**base, "KAZMA_DATA_DIR": str(src)},
        tmp_path,
    )
    for name in ("agent_artifacts.db", "x_scheduled.db", "hitl_gates.db", "checkpoints_tenantA.db"):
        assert f"data/{name}" in out, f"the bundle left {name} behind:\n{out}"

    out = _run(
        f"""
        import json
        from kazma_core.migration.importer import import_bundle
        r = import_bundle({str(bundle)!r}, target_workspace_root={str(tmp_path / "ws")!r})
        from kazma_core.agent.artifacts import ArtifactStore
        from kazma_core.paths import data_dir
        texts = [i["text"] for i in ArtifactStore(data_dir() / "agent_artifacts.db").list_proposals()]
        print(json.dumps({{"ok": r.ok, "errors": r.errors, "restored": r.files_restored, "texts": texts}}))
        """,
        {**base, "KAZMA_DATA_DIR": str(dst)},
        tmp_path,
    )
    report = json.loads(out.strip().splitlines()[-1])
    assert report["ok"], report["errors"]
    for name in ("agent_artifacts.db", "x_scheduled.db", "hitl_gates.db", "checkpoints_tenantA.db"):
        assert name in report["restored"], (name, report["restored"])
    assert report["texts"] == ["carried draft"]


# ── 6. every door refuses every store, and names the reader ──────────────


@pytest.fixture()
def data(tmp_path, monkeypatch):
    from kazma_core.workspace import path_policy

    d = tmp_path / "kazma-data"
    (d / "workspace").mkdir(parents=True)
    monkeypatch.setenv("KAZMA_DATA_DIR", str(d))
    # Pin the workspace so the verdicts depend on rule 0 alone, not on this
    # machine's active WorkspaceStore row.
    monkeypatch.setattr(path_policy, "resolve_active_root", lambda: tmp_path)
    monkeypatch.chdir(tmp_path)
    return d


def _doors(path: Path) -> dict[str, object]:
    from kazma_core.safety.commitment.authorize import _exec_names_kazma_store
    from kazma_core.workspace.path_policy import (
        check_path_access,
        code_mentions_control_plane,
        control_plane_store_targeted,
        denied_message,
    )
    from kazma_skills.native.database_client.tools import _deny_internal

    read = check_path_access(path, "read")
    return {
        "sql": _deny_internal(str(path)),
        "file_read": None if read.allowed else denied_message(str(path), "read", result=read),
        "file_write": None if check_path_access(path, "write").allowed else "refused",
        "python_exec": code_mentions_control_plane(f"open({str(path)!r}, 'rb')"),
        "shell_exec": control_plane_store_targeted(str(path)),
        "before_the_card_code": _exec_names_kazma_store({"code": f"open({str(path)!r})"}, ""),
        "before_the_card_shell": _exec_names_kazma_store({}, f'sqlite3 "{path}" .dump'),
    }


def test_every_door_refuses_every_store_and_names_its_reader(data):
    from kazma_core.store_registry import STORES, readers_for_store

    for name in STORES:
        path = data / name
        path.write_bytes(b"")
        doors = _doors(path)
        open_doors = [d for d, verdict in doors.items() if not verdict]
        assert not open_doors, f"{name}: open through {open_doors}"
        tools = [r for r in readers_for_store(name) if not r.startswith("context:")]
        for door in ("sql", "file_read"):
            text = str(doors[door])
            for tool in tools:
                assert tool in text, f"{name}: the {door} refusal does not name {tool}: {text}"
        assert "request_path_access" not in str(doors["file_read"]), (
            f"{name}: offered a path grant, which cannot open a store"
        )


def test_the_users_own_sandbox_database_passes_every_door(data):
    """Negative control: the doors must not swallow the user's files."""
    own = data / "workspace" / "project.db"
    own.write_bytes(b"")
    doors = _doors(own)
    assert not any(doors.values()), doors


def test_the_2026_09_25_dump_is_refused(data):
    """The exact code the approved python_exec ran: a relative path, a store
    whose name the old refusal list had never heard of."""
    from kazma_core.safety.commitment.authorize import _exec_names_kazma_store
    from kazma_core.workspace.path_policy import code_mentions_control_plane

    (data / "agent_artifacts.db").write_bytes(b"")
    code = "path = 'kazma-data/agent_artifacts.db'\ndata = open(path, 'rb').read()"
    assert code_mentions_control_plane(code) == "agent_artifacts.db"
    assert _exec_names_kazma_store({"code": code}, "") == "agent_artifacts.db"


# ── 7. no store path is built from the process working directory ─────────


def _cwd_relative_store_paths(sources: dict[str, str]) -> list[str]:
    """``Path("kazma-data") / "x.db"`` and friends, outside an except-fallback.

    The same bug was fixed one file at a time (knowledge, bookmarks,
    checkpoints, documents) and survived in the per-tenant checkpoints: a
    process started elsewhere wrote the store where backup and migration never
    look. A fallback inside ``except`` (data_dir() itself failed) is allowed.
    """
    problems: list[str] = []

    def mentions_data_dir_literal(node: ast.AST) -> bool:
        return any(
            isinstance(n, ast.Constant) and isinstance(n.value, str)
            and n.value.replace("\\", "/").split("/", 1)[0] == "kazma-data"
            for n in ast.walk(node)
        )

    def names_a_database(node: ast.AST) -> bool:
        for n in ast.walk(node):
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and DB_TAIL.search(n.value.strip()):
                return True
            if isinstance(n, ast.JoinedStr) and n.values:
                tail = n.values[-1]
                if isinstance(tail, ast.Constant) and isinstance(tail.value, str) and DB_TAIL.search(tail.value):
                    return True
        return False

    for rel, text in sources.items():
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        fallback: set[int] = set()
        for handler in ast.walk(tree):
            if isinstance(handler, ast.ExceptHandler):
                for n in ast.walk(handler):
                    fallback.add(id(n))
        for node in ast.walk(tree):
            if id(node) in fallback:
                continue
            if isinstance(node, (ast.BinOp, ast.Call)) and not isinstance(getattr(node, "op", None), ast.Mod):
                # the outermost path expression only
                if mentions_data_dir_literal(node) and names_a_database(node):
                    problems.append(f"{rel}:{node.lineno}")
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                s = node.value.strip().replace("\\", "/")
                if s.startswith("kazma-data/") and DB_TAIL.search(s) and not any(c.isspace() for c in s):
                    problems.append(f"{rel}:{node.lineno}")
    return sorted(set(problems))


def test_no_store_path_is_built_from_the_working_directory():
    problems = _cwd_relative_store_paths(_product_sources())
    assert not problems, (
        "A database path built from the literal 'kazma-data' (relative to wherever\n"
        "the process started) instead of kazma_core.paths.data_dir(). Started from\n"
        "another directory, the store lands where backup and migration never look:\n  "
        + "\n  ".join(problems)
    )


def _unclosed_sqlite_with_blocks(sources: dict[str, str]) -> list[str]:
    """``with sqlite3.connect(...) as c:`` — commits on exit, never closes.

    A Connection sits in a reference cycle with its statement cache, so the
    file stays open until a GC pass. On Windows an open file cannot be
    renamed or deleted: every ``kazma migrate import`` died at its first
    swap (WinError 32, 3 of 3 runs) until the copier used closing().
    """
    problems: list[str] = []
    for rel, text in sources.items():
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.With, ast.AsyncWith)):
                continue
            if any(
                isinstance(call := item.context_expr, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr == "connect"
                and isinstance(call.func.value, ast.Name)
                and call.func.value.id == "sqlite3"
                for item in node.items
            ):
                problems.append(f"{rel}:{node.lineno}")
    return problems


def test_no_sqlite_connection_is_left_open_by_a_with_block():
    problems = _unclosed_sqlite_with_blocks(_product_sources())
    assert not problems, (
        "`with sqlite3.connect(...)` does not close the connection; wrap it in\n"
        "contextlib.closing(...) or close it in a finally:\n  " + "\n  ".join(problems)
    )


def test_unclosed_sqlite_with_blocks_are_caught():
    """Negative control: the old copier line is flagged, closing() is not."""
    planted = {
        "x.py": textwrap.dedent(
            """
            import sqlite3
            from contextlib import closing
            def bad(a, b):
                with sqlite3.connect(a) as s, sqlite3.connect(b) as d:
                    s.backup(d)
            def good(a):
                with closing(sqlite3.connect(a)) as c:
                    c.execute("select 1")
            """
        )
    }
    assert _unclosed_sqlite_with_blocks(planted) == ["x.py:5"]


def _returns_raw_connection(fn: ast.AST) -> bool:
    """True when *fn* returns ``sqlite3.connect(...)``'s result unwrapped."""
    raw_names: set[str] = set()
    for node in ast.walk(fn):
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Attribute)
            and node.value.func.attr == "connect"
            and isinstance(node.value.func.value, ast.Name)
            and node.value.func.value.id == "sqlite3"
        ):
            raw_names.update(t.id for t in node.targets if isinstance(t, ast.Name))
    for node in ast.walk(fn):
        if isinstance(node, ast.Return) and node.value is not None:
            v = node.value
            if isinstance(v, ast.Name) and v.id in raw_names:
                return True
            if (
                isinstance(v, ast.Call) and isinstance(v.func, ast.Attribute)
                and v.func.attr == "connect" and isinstance(v.func.value, ast.Name)
                and v.func.value.id == "sqlite3"
            ):
                return True
    return False


def _raw_connections_used_as_context(sources: dict[str, str]) -> list[str]:
    """``with self._connect() as conn:`` where ``_connect`` returns a raw
    connection — the same commit-but-never-close as ``with sqlite3.connect()``,
    one call away. Checked per module (openers are private to their store)."""
    problems: list[str] = []
    for rel, text in sources.items():
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        openers = {
            node.name for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and _returns_raw_connection(node)
        }
        if not openers:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.With, ast.AsyncWith)):
                continue
            for item in node.items:
                call = item.context_expr
                if not isinstance(call, ast.Call):
                    continue
                name = call.func.attr if isinstance(call.func, ast.Attribute) else getattr(call.func, "id", "")
                if name in openers:
                    problems.append(f"{rel}:{node.lineno} with {name}()")
                    break
    return problems


def test_no_raw_connection_opener_is_used_as_a_context():
    problems = _raw_connections_used_as_context(_product_sources())
    assert not problems, (
        "A function returning a raw sqlite3 connection is used in a `with`\n"
        "block, which commits and never closes. Return\n"
        "kazma_core.db.sqlite_session.committed_and_closed(conn) instead:\n  "
        + "\n  ".join(problems)
    )


def test_a_raw_connection_opener_used_as_a_context_is_caught():
    """Negative control: the pre-2026-09-25 store shape, and its fix."""
    planted = {
        "bad.py": textwrap.dedent(
            """
            import sqlite3
            class Store:
                def _connect(self):
                    conn = sqlite3.connect(self.path)
                    return conn
                def read(self):
                    with self._connect() as conn:
                        return conn.execute("select 1").fetchone()
            """
        ),
        "good.py": textwrap.dedent(
            """
            import sqlite3
            from kazma_core.db.sqlite_session import committed_and_closed
            class Store:
                def _connect(self):
                    conn = sqlite3.connect(self.path)
                    return committed_and_closed(conn)
                def read(self):
                    with self._connect() as conn:
                        return conn.execute("select 1").fetchone()
            """
        ),
    }
    assert _raw_connections_used_as_context(planted) == ["bad.py:8 with _connect()"]


def test_cwd_relative_store_paths_are_caught():
    """Negative control: the per-tenant checkpoint line, and its fallback form."""
    planted = {
        "x.py": textwrap.dedent(
            """
            from pathlib import Path
            def saver(t):
                return Path("kazma-data") / f"checkpoints_{t}.db"
            def fallback():
                try:
                    from kazma_core.paths import data_dir
                    return data_dir() / "cron.db"
                except Exception:
                    return Path("kazma-data") / "cron.db"
            DEFAULT = "kazma-data/sessions.db"
            """
        )
    }
    problems = _cwd_relative_store_paths(planted)
    assert "x.py:4" in problems, problems
    assert "x.py:11" in problems, problems
    assert not any(p.endswith(":10") for p in problems), "an except-fallback was flagged"
