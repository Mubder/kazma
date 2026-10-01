"""A test run never touches an install's data (root ``conftest.py``, 2026-09-27).

``paths.data_dir()`` defaults to ``<checkout>/kazma-data`` and
``paths.user_home()`` to ``<checkout>/.kazma``, and a live install is a
checkout of this repository. Kazma's agent runs the tests of its workspace,
and the live workspace is the install folder: a run there wrote two pipeline
test gates into the live hitl_gates.db (2026-09-01), a ``chat_sessions_test.db``
beside the live chat store and fourteen "test task" rows into the live swarm
store. The root conftest pins both directories to fresh temp folders before
any product module is imported, and clears the per-store overrides that would
point around them.

Held here: every location ``kazma_core/paths.py`` resolves from the
environment -- enumerated from its source, so a new override is covered or
fails -- lands in the temp folders; a module-level path computed at import
does too (the pin came first); and, as the negative control, without the pin
the same code resolves into the checkout.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PATHS = REPO / "kazma-core" / "kazma_core" / "paths.py"

#: Functions that read the environment but are not a store location.
_NOT_A_STORE = {
    "get_project_root": "the checkout itself -- what the pin keeps tests out of",
    "installed_project_root": "the install folder, read-only",
    "migrate_legacy_user_home": "a boot-time move, guarded on its own (test_user_home_migration.py)",
    "log_file": "pinned separately, by the log shield above it in the conftest",
}


def _env_reading_functions() -> dict[str, list[str]]:
    """Every top-level function of paths.py that reads a KAZMA_* variable."""
    tree = ast.parse(PATHS.read_text(encoding="utf-8"))
    found: dict[str, list[str]] = {}
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        names = [
            sub.args[0].value
            for sub in ast.walk(node)
            if isinstance(sub, ast.Call) and sub.args
            and isinstance(sub.args[0], ast.Constant) and isinstance(sub.args[0].value, str)
            and sub.args[0].value.startswith("KAZMA_")
            and isinstance(sub.func, ast.Attribute) and sub.func.attr == "get"
        ]
        if names:
            found[node.name] = names
    return found


def _under_temp(path: str | Path) -> bool:
    target = Path(path).resolve()
    roots = {Path(tempfile.gettempdir()).resolve()}
    for var in ("TEMP", "TMP", "TMPDIR"):
        if os.environ.get(var):
            roots.add(Path(os.environ[var]).resolve())
    return any(target.is_relative_to(root) for root in roots)


def test_the_enumeration_finds_the_store_locations():
    found = _env_reading_functions()
    for name in ("data_dir", "user_home", "primary_memory_db", "memory_ops_db", "hub_registry_db"):
        assert name in found, f"{name} no longer reads its override -- update this gate"


def test_every_store_location_is_under_the_test_temp_folders():
    from kazma_core import paths

    outside = []
    for name in sorted(set(_env_reading_functions()) - set(_NOT_A_STORE)):
        location = getattr(paths, name)()
        if not _under_temp(location):
            outside.append(f"{name}() -> {location}")
    assert not outside, (
        "these resolve outside the suite's temp folders, so a test run inside an "
        f"install writes the install's data: {outside}"
    )


def test_the_checkout_data_folder_is_not_where_tests_write():
    from kazma_core import paths

    assert not Path(paths.data_dir()).resolve().is_relative_to(REPO)
    assert not Path(paths.user_home()).resolve().is_relative_to(REPO)


def test_a_path_computed_at_import_follows_the_pin():
    """The pin ran before the conftest imported every product module."""
    from kazma_core.swarm import task_store

    assert _under_temp(task_store._DEFAULT_DB)


def test_the_conftest_clears_every_store_override():
    """The per-store variables paths.py reads, from its source: each is either
    derived from the pinned folders or cleared by the conftest."""
    tree = ast.parse((REPO / "conftest.py").read_text(encoding="utf-8"))
    cleared: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            target = node.targets[0] if isinstance(node, ast.Assign) else node.target
            if isinstance(target, ast.Name) and target.id == "_STORE_PATH_OVERRIDES":
                cleared = {elt.value for elt in node.value.elts}
    pinned = {"KAZMA_DATA_DIR", "KAZMA_USER_HOME", "KAZMA_LOG_FILE", "KAZMA_PROJECT_ROOT"}
    read = {n for names in _env_reading_functions().values() for n in names}
    assert read - pinned - cleared == set(), "a store override the conftest neither pins nor clears"


def test_the_skill_folders_are_under_the_test_temp_folders():
    """The user-level skill folders live in the user's home, outside the data
    dir: the live install runs as the same user and reads them, an install
    writes there and an uninstall removes from there (2026-10-01)."""
    from kazma_core.agent_skills.discovery import skill_base_dirs, skills_home, user_skill_folders

    user_scope = [path for scope, path in skill_base_dirs() if scope == "user"]
    outside = [str(p) for p in [skills_home(), *user_skill_folders(), *user_scope] if not _under_temp(p)]
    assert not outside, f"skill folders outside the suite's temp folders: {outside}"


def test_only_skills_home_reads_the_home_folder():
    """Every skill folder in the home comes from discovery.skills_home(), so
    the one pin covers them all."""
    folder = REPO / "kazma-core" / "kazma_core" / "agent_skills"
    reads = {
        f"{path.name}:{n}"
        for path in sorted(folder.glob("*.py"))
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if "Path.home()" in line or "expanduser(\"~\")" in line or "legacy_user_home" in line
    }
    assert reads == {f"discovery.py:{_line_of('discovery.py', 'Path.home()')}"}, reads


def _line_of(name: str, needle: str) -> int:
    path = REPO / "kazma-core" / "kazma_core" / "agent_skills" / name
    return next(n for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1) if needle in line)


def test_without_the_pin_the_skill_folders_are_the_real_home():
    """Negative control: with KAZMA_SKILLS_HOME removed, a fresh interpreter
    resolves the skill folders into the user's real home."""
    env = {k: v for k, v in os.environ.items() if k != "KAZMA_SKILLS_HOME"}
    out = subprocess.run(
        [sys.executable, "-c", "from kazma_core.agent_skills.discovery import skills_home; print(skills_home())"],
        cwd=REPO, env=env, capture_output=True, text=True, timeout=60, check=True,
    ).stdout.strip()
    assert Path(out).resolve() == Path.home().resolve()


def test_without_the_pin_the_same_code_resolves_into_the_checkout():
    """Negative control: in a fresh interpreter started in the checkout with
    the pins removed, paths resolve to <checkout>/kazma-data -- the live data
    when the checkout is an install. (Resolved without creating anything.)"""
    env = {k: v for k, v in os.environ.items() if k not in ("KAZMA_DATA_DIR", "KAZMA_USER_HOME")}
    out = subprocess.run(
        [sys.executable, "-c", "from kazma_core.paths import get_project_root; print(get_project_root())"],
        cwd=REPO, env=env, capture_output=True, text=True, timeout=60, check=True,
    ).stdout.strip()
    assert Path(out).resolve() == REPO
