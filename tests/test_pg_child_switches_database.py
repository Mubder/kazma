"""A test process switches its Kazma database through ``tests._pg_child.use_database`` (S4-01 step 5 review).

The restore-order drill failed ("Document is unavailable") whenever pg_dump
ran through ``docker exec``: finding the tool read the stored container-engine
choice, which opened the child's settings store and shared pool on the base
database, and the child's later switch by the environment alone left both
there, so it read the base database instead of the restored one. Four test
files kept their own copy of that switch. Now one helper lets go of the pool
and the settings store before it switches, and this gate refuses a switch by
hand anywhere else in the tests.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
HELPER = "tests/_pg_child.py"


def _switches_by_hand(source: str) -> list[int]:
    """Lines where *source* sets ``os.environ["KAZMA_DATABASE_URL"]`` (or
    ``DATABASE_URL``) itself: an assignment, ``update`` or ``setdefault``."""
    keys = {"KAZMA_DATABASE_URL", "DATABASE_URL"}
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.Assign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if (isinstance(target, ast.Subscript) and isinstance(target.slice, ast.Constant)
                        and target.slice.value in keys and ast.unparse(target.value).endswith("environ")):
                    found.append(node.lineno)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            owner = ast.unparse(node.func.value)
            if owner.endswith("environ") and node.func.attr in ("update", "setdefault", "__setitem__"):
                said = {a.value for a in node.args if isinstance(a, ast.Constant)}
                said |= {k.value for a in node.args if isinstance(a, ast.Dict) for k in a.keys
                         if isinstance(k, ast.Constant)}
                said |= {kw.arg for kw in node.keywords}
                if said & keys:
                    found.append(node.lineno)
    return sorted(found)


def _test_files() -> list[str]:
    out = subprocess.run(["git", "ls-files", "--", "tests/*.py", "tests/**/*.py", "conftest.py"],
                         cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return sorted({line.strip() for line in out.splitlines() if line.strip()})


def test_no_test_switches_its_database_by_hand() -> None:
    files = _test_files()
    assert HELPER in files, "stage tests/_pg_child.py: the gate reads git-listed files"
    by_hand = []
    for rel in files:
        if rel == HELPER or not (ROOT / rel).exists():
            continue
        by_hand += [f"{rel}:{n}" for n in _switches_by_hand((ROOT / rel).read_text(encoding="utf-8"))]
    assert not by_hand, (
        "a test process switched its Kazma database by the environment alone: the pool and settings "
        f"store it opened before stay on the old one -- use tests._pg_child.use_database: {by_hand}"
    )
    assert _switches_by_hand((ROOT / HELPER).read_text(encoding="utf-8")), "the helper is the one switch"


@pytest.mark.parametrize("planted", [
    'os.environ["KAZMA_DATABASE_URL"] = dsn',
    "environ['DATABASE_URL'] = dsn",
    'os.environ.update({"KAZMA_DATABASE_URL": dsn})',
    "os.environ.update(KAZMA_DATABASE_URL=dsn)",
    'os.environ.setdefault("KAZMA_DATABASE_URL", dsn)',
])
def test_the_gate_finds_a_planted_switch(planted: str) -> None:
    assert _switches_by_hand(f"import os\n{planted}\n") == [2]


def test_the_gate_leaves_reads_and_monkeypatch_alone() -> None:
    source = ('url = os.environ["KAZMA_DATABASE_URL"]\n'
              'monkeypatch.setenv("KAZMA_DATABASE_URL", dsn)\n'
              'os.environ["KAZMA_DB_BACKEND"] = "postgres"\n')
    assert _switches_by_hand(source) == []


class _Pool:
    closed = False

    def close(self) -> None:
        self.closed = True


def _switch_world(monkeypatch: pytest.MonkeyPatch) -> tuple[Any, list[Any], dict[str, str]]:
    """The base database's pool and settings store open, and a private
    environment for the switch to write (the suite's own shield rewrites the
    real one's database variables in a test with no real database)."""
    from kazma_core import config_store
    from kazma_core.db import postgres_pool

    from tests import _pg_child

    env = {"KAZMA_DB_BACKEND": "postgres", "KAZMA_DATABASE_URL": "postgresql://kazma@127.0.0.1:1/base"}
    monkeypatch.setattr(_pg_child, "os", SimpleNamespace(environ=env))
    old = _Pool()
    monkeypatch.setattr(postgres_pool, "_pool", old)
    written: list[Any] = []

    class _Store:
        def set(self, key: str, value: Any, **_: Any) -> None:
            written.append((key, value))

    monkeypatch.setattr(config_store, "_config_store", object())  # the base database's, opened before
    monkeypatch.setattr(config_store, "get_config_store", lambda: _Store())
    return old, written, env


def test_switching_lets_go_of_the_pool_and_settings_opened_before(monkeypatch: pytest.MonkeyPatch) -> None:
    from kazma_core import config_store
    from kazma_core.db import postgres_pool

    from tests._pg_child import use_database

    old, written, env = _switch_world(monkeypatch)
    use_database("postgresql://kazma@127.0.0.1:1/restored", "sqlite")
    assert old.closed and postgres_pool._pool is None, "the pool on the old database is let go"
    assert config_store._config_store is None, "so is the settings store opened on it"
    assert env == {"KAZMA_DB_BACKEND": "postgres", "KAZMA_DATABASE_URL": "postgresql://kazma@127.0.0.1:1/restored",
                   "KAZMA_DOCUMENTS_METADATA_BACKEND": "sqlite", "KAZMA_DOCUMENTS_JOBS_BACKEND": "postgres"}
    assert written == [("documents.enabled", True)]


def test_control_a_switch_by_the_environment_alone_keeps_them(monkeypatch: pytest.MonkeyPatch) -> None:
    """The four copies before: the environment set, the old pool and store kept."""
    from kazma_core import config_store
    from kazma_core.db import postgres_pool

    old, _, env = _switch_world(monkeypatch)
    env["KAZMA_DATABASE_URL"] = "postgresql://kazma@127.0.0.1:1/restored"  # the old copies': environment only
    assert postgres_pool._pool is old and not old.closed
    assert config_store._config_store is not None
