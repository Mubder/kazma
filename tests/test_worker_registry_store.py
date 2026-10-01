"""The swarm's worker registry is a data-dir store, written whole (2026-10-01).

``WorkerRegistry`` kept its JSON file at ``Path("swarm_registry.json").resolve()``,
the process's working directory at import: outside the data dir, so no
backup copied it and a test run inside an install wrote the install's. Its
writes were a plain ``write_text`` (a crash mid-write left a truncated file
that loaded as empty), and its changes ran outside its own lock while route
threads and the loop read it.
"""

from __future__ import annotations

import ast
import json
import threading
from pathlib import Path

import pytest

import kazma_core.swarm.registry as reg

REGISTRY_SRC = Path(reg.__file__).read_text(encoding="utf-8")


@pytest.fixture()
def unpinned(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """The registry's own default (the conftest pins a temp file)."""
    data = tmp_path / "data"
    monkeypatch.setenv("KAZMA_DATA_DIR", str(data))
    monkeypatch.setattr(reg, "_DEFAULT_PATH", None)
    monkeypatch.setattr(reg, "_REGISTRY_SINGLETON", None)
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    monkeypatch.setattr(reg, "_legacy_registry_paths", lambda: [cwd / "swarm_registry.json"])
    return data, cwd


def test_the_registry_lives_in_the_data_dir(unpinned) -> None:
    data, cwd = unpinned
    registry = reg.WorkerRegistry()
    registry.register(reg.WorkerEntry(name="coder", expertise=["code"]))
    assert reg.default_registry_path() == data / "swarm_registry.json"
    assert json.loads((data / "swarm_registry.json").read_text(encoding="utf-8"))[0]["name"] == "coder"
    assert not (cwd / "swarm_registry.json").exists()


def test_an_older_builds_registry_is_moved_once(unpinned) -> None:
    data, cwd = unpinned
    legacy = cwd / "swarm_registry.json"
    legacy.write_text(json.dumps([{"name": "kept", "expertise": ["research"]}]), encoding="utf-8")

    registry = reg.WorkerRegistry()

    assert registry.get("kept") is not None
    assert (data / "swarm_registry.json").is_file()
    assert not legacy.exists() and (cwd / "swarm_registry.json.migrated").is_file()
    # A second start reads the data dir's copy and touches nothing else.
    assert reg.WorkerRegistry().get("kept") is not None


def test_an_installs_registry_is_never_moved_into_a_temporary_data_dir(
    unpinned, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A test's (or a scratch copy's) data dir must not take an install's file.

    Here the working directory plays the install: only the data dir is
    temporary. The move test above is the negative control -- both there
    are temporary, and the file moves.
    """
    import kazma_core.paths as paths

    data, cwd = unpinned
    legacy = cwd / "swarm_registry.json"
    legacy.write_text(json.dumps([{"name": "the installs"}]), encoding="utf-8")
    monkeypatch.setattr(paths, "_is_throwaway", lambda p: Path(p).resolve().is_relative_to(data.resolve()))

    registry = reg.WorkerRegistry()

    assert registry.get("the installs") is None
    assert legacy.is_file() and not (cwd / "swarm_registry.json.migrated").exists()
    assert json.loads((data / "swarm_registry.json").read_text(encoding="utf-8")) == []


def test_the_old_default_was_the_working_directory(unpinned) -> None:
    """Negative control: the old default, resolved where the process ran."""
    data, cwd = unpinned
    old_default = Path("swarm_registry.json").resolve()
    assert old_default.parent == cwd.resolve()
    assert not old_default.is_relative_to(data)


def test_a_failed_write_leaves_the_last_good_file(unpinned, monkeypatch: pytest.MonkeyPatch) -> None:
    data, _cwd = unpinned
    registry = reg.WorkerRegistry()
    registry.register(reg.WorkerEntry(name="first"))
    target = data / "swarm_registry.json"
    good = target.read_text(encoding="utf-8")

    real_write = Path.write_text

    def half_then_fail(self: Path, text: str, *args, **kwargs):
        real_write(self, text[: len(text) // 2], *args, **kwargs)
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_text", half_then_fail)
    with pytest.raises(OSError):
        registry.register(reg.WorkerEntry(name="second"))
    monkeypatch.setattr(Path, "write_text", real_write)

    assert target.read_text(encoding="utf-8") == good
    assert not list(data.glob(".swarm_registry.json.*.tmp"))


def test_the_old_write_truncated_the_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Negative control: writing the file in place, as the old save did."""
    target = tmp_path / "swarm_registry.json"
    target.write_text('[{"name": "first"}]\n', encoding="utf-8")
    real_write = Path.write_text

    def half_then_fail(self: Path, text: str, *args, **kwargs):
        real_write(self, text[: len(text) // 2], *args, **kwargs)
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_text", half_then_fail)
    with pytest.raises(OSError):
        target.write_text('[{"name": "first"}, {"name": "second"}]\n', encoding="utf-8")
    monkeypatch.setattr(Path, "write_text", real_write)
    with pytest.raises(json.JSONDecodeError):
        json.loads(target.read_text(encoding="utf-8"))


def _unlocked_entry_access(source: str) -> list[str]:
    """Methods of WorkerRegistry that change or iterate ``self._entries``
    outside ``with self._lock:`` (``_save_unlocked``: its caller holds it)."""
    tree = ast.parse(source)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "WorkerRegistry")
    problems: list[str] = []

    def is_entries(node: ast.AST) -> bool:
        return (
            isinstance(node, ast.Attribute) and node.attr == "_entries"
            and isinstance(node.value, ast.Name) and node.value.id == "self"
        )

    def touches(node: ast.AST) -> bool:
        for n in ast.walk(node):
            if isinstance(n, (ast.Assign, ast.Delete)):
                targets = n.targets
                if any(isinstance(t, ast.Subscript) and is_entries(t.value) for t in targets):
                    return True
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
                if is_entries(n.func.value) and n.func.attr in ("values", "items", "keys", "clear", "pop", "update"):
                    return True
        return False

    def locked(stmt: ast.stmt) -> bool:
        return isinstance(stmt, ast.With) and any(
            isinstance(i.context_expr, ast.Attribute) and i.context_expr.attr == "_lock"
            for i in stmt.items
        )

    for fn in cls.body:
        if not isinstance(fn, ast.FunctionDef) or fn.name == "_save_unlocked":
            continue
        for stmt in fn.body:
            if not locked(stmt) and touches(stmt):
                problems.append(fn.name)
                break
    return problems


def test_every_change_and_iteration_holds_the_lock() -> None:
    assert _unlocked_entry_access(REGISTRY_SRC) == []


def test_an_unlocked_change_is_found() -> None:
    """Negative control: the old register()."""
    old = '''
class WorkerRegistry:
    def register(self, entry):
        self._entries[entry.name] = entry
        self._save()

    def list_all(self):
        return list(self._entries.values())
'''
    assert _unlocked_entry_access(old) == ["register", "list_all"]


def test_route_threads_and_readers_do_not_collide(unpinned) -> None:
    registry = reg.WorkerRegistry()
    errors: list[BaseException] = []

    def write(prefix: str) -> None:
        try:
            for i in range(60):
                registry.register(reg.WorkerEntry(name=f"{prefix}{i}", expertise=["code"]))
        except BaseException as exc:  # noqa: BLE001 -- collected for the assertion
            errors.append(exc)

    def read() -> None:
        try:
            for _ in range(300):
                registry.find_by_expertise("code")
                registry.expertise_map()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=write, args=(p,)) for p in "abc"]
    threads += [threading.Thread(target=read) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert registry.count() == 180
