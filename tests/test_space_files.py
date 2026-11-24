"""The space folder (Spaces S1-19).

A sandboxed space's files live in ``<data dir>/spaces/<id>/`` and its tools
cannot leave it; the owner's space keeps the install's folders and the
machine's workspace; nobody's tool opens another space's folder. Each rule
is shown failing with the rule taken out (the install's workspace, the
install's folders, one browser page for all, the journal for all), and an
install that does not enforce Spaces behaves exactly as before.

The matrix cases (``tests/_files_space_cases.py``) drive every tool that
takes a path or writes a file; this file holds the rules and the controls.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest
from kazma_core.spaces import files as space_files
from kazma_core.spaces import identity, scope
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID, OWNER_SPACE_ID, NoSpaceBound, bind_scope
from kazma_core.workspace import path_policy
from kazma_core.workspace.binding import resolve_active_root
from kazma_core.workspace.path_policy import check_path_access

from tests import _files_space_cases as cases
from tests._space_matrix import Matrix


@pytest.fixture
def matrix():
    with Matrix() as made:
        yield made
    for space in (Matrix.SPACE_A, Matrix.SPACE_B):
        shutil.rmtree(space_files.space_dir(space), ignore_errors=True)


@pytest.fixture
def enforced(monkeypatch):
    monkeypatch.setattr(scope, "_enforced", True)


def as_owner():
    return bind_scope(OWNER_SPACE_ID, OWNER_ACCOUNT_ID)


# ── where a space's files are ────────────────────────────────────────────


def test_a_sandboxed_space_works_in_its_own_folder(matrix, enforced) -> None:
    with matrix.as_a():
        root_a = resolve_active_root()
    with matrix.as_b():
        root_b = resolve_active_root()
    assert root_a == (space_files.space_dir(Matrix.SPACE_A) / "files").resolve()
    assert root_b == (space_files.space_dir(Matrix.SPACE_B) / "files").resolve()
    assert root_a != root_b and root_a.is_dir()
    # No per-task scope moves it (a swarm task's workspace, the IDE server's root).
    from kazma_core.ide.workspace_scope import pin_workspace_path, reset_workspace_path

    with as_owner():
        machine = resolve_active_root()
    token = pin_workspace_path(machine)
    try:
        with matrix.as_a():
            assert resolve_active_root() == root_a
    finally:
        reset_workspace_path(token)


def test_the_owners_space_keeps_the_machines_workspace_and_folders(matrix, enforced, monkeypatch) -> None:
    from kazma_core.paths import data_dir

    monkeypatch.setattr(scope, "_enforced", False)
    before = resolve_active_root()
    monkeypatch.setattr(scope, "_enforced", True)
    with as_owner():
        assert resolve_active_root() == before
        for kind in space_files.FOLDER_KINDS:
            assert space_files.space_folder(kind, create=False) == data_dir() / kind
        assert space_files.sandboxed_space() is None
    # The install's own work (no space bound) keeps the machine's workspace too.
    assert resolve_active_root() == before


def test_each_space_has_its_own_folders(matrix, enforced) -> None:
    for kind in space_files.FOLDER_KINDS:
        with matrix.as_a():
            folder_a = space_files.space_folder(kind)
        with matrix.as_b():
            folder_b = space_files.space_folder(kind)
        assert folder_a == space_files.space_dir(Matrix.SPACE_A) / kind
        assert folder_b == space_files.space_dir(Matrix.SPACE_B) / kind
    with pytest.raises(NoSpaceBound):
        space_files.space_folder("attachments")  # a file is never kept for no one
    with pytest.raises(ValueError):
        space_files.space_dir("../escape")
    with matrix.as_a(), pytest.raises(ValueError):
        space_files.space_folder("vault")


def test_without_spaces_every_folder_is_the_installs(matrix) -> None:
    from kazma_core.paths import data_dir

    assert not scope.spaces_enforced()
    before = sorted(space_files._spaces_root().iterdir()) if space_files._spaces_root().exists() else []
    root = resolve_active_root()
    with matrix.as_a():  # a bound scope changes nothing while not enforced
        assert resolve_active_root() == root
        assert space_files.sandboxed_space() is None
        for kind in space_files.FOLDER_KINDS:
            assert space_files.space_folder(kind, create=False) == data_dir() / kind
        assert check_path_access(root / "x.txt", "write").via == "workspace"
    after = sorted(space_files._spaces_root().iterdir()) if space_files._spaces_root().exists() else []
    assert after == before  # no space folder is made while Spaces is not enforced


# ── the path policy ──────────────────────────────────────────────────────


def test_a_sandboxed_space_cannot_leave_its_folder(matrix, enforced, tmp_path, monkeypatch) -> None:
    from kazma_core.workspace import path_grants

    with as_owner():
        machine = resolve_active_root()
    outside = tmp_path / "granted"
    outside.mkdir()
    # Nothing of the machine reaches it: not an extra folder, not a path
    # grant, not the absolute-path switch.
    monkeypatch.setattr(path_policy, "list_durable_roots",
                        lambda: [path_grants.PathGrant(path=str(outside), mode="write")])
    monkeypatch.setattr(path_policy, "allow_absolute_paths", lambda: True)
    with matrix.as_a():
        home = space_files.space_dir(Matrix.SPACE_A).resolve()
        assert check_path_access("note.md", "write").via == "space"
        assert check_path_access(home / "images" / "x.png", "read").allowed
        climbing = ["../../../settings.db", "../../s0b0b0b0b0b0b/files/note.md"]
        if os.name == "nt":
            # A backslash separates folders on Windows only; elsewhere it is
            # a letter of one file's name in A's own folder (below).
            climbing.append("..\\..\\s0b0b0b0b0b0b\\files\\note.md")
        for target in (machine / "README.md", outside / "x.txt", tmp_path / "y.txt", *climbing,
                       space_files.space_dir(Matrix.SPACE_B) / "files" / "note.md"):
            result = check_path_access(target, "read")
            assert not result.allowed, target
        if os.name != "nt":
            named = check_path_access("..\\..\\s0b0b0b0b0b0b\\files\\note.md", "read")
            assert named.allowed and Path(named.resolved).parent == (home / "files").resolve(), named
    # The same paths for the owner: the extra folder and the switch still work.
    with as_owner():
        assert check_path_access(outside / "x.txt", "write").via == "durable"


def test_control_the_installs_workspace_reaches_the_owners_files(matrix, monkeypatch) -> None:
    """The plan's control: with the install's workspace as a member's root
    (the resolver and the policy as they were), A reads the owner's file."""
    case = cases._in_world(cases._PATH_TOOLS and _path_run("file_read"))
    case(matrix)  # passes as built
    monkeypatch.setattr(space_files, "sandboxed_files_root", lambda: None)
    monkeypatch.setattr(space_files, "sandboxed_space", lambda: None)
    with pytest.raises(AssertionError):
        case(matrix)


def _path_run(tool_name: str):
    planted, kwargs = cases._PATH_TOOLS[tool_name]

    def run(w) -> None:
        for target in w.foreign(planted or ""):
            w.nothing_of_the_others(w.tool("a", tool_name, **kwargs(target)))
            w.others_unchanged()

    return run


def test_nobody_opens_another_spaces_folder_the_owner_included(matrix, enforced, monkeypatch) -> None:
    """The machine's workspace can hold the data folder (on the live install
    it does): the owner's tools still open no member's files (O1)."""
    from kazma_core import store_registry
    from kazma_core.paths import data_dir

    theirs = space_files.space_dir(Matrix.SPACE_B) / "files" / "note.md"
    theirs.parent.mkdir(parents=True, exist_ok=True)
    theirs.write_text("b's own", encoding="utf-8")
    # The owner's workspace is the folder that holds the data folder.
    monkeypatch.setattr(path_policy, "resolve_active_root", lambda: data_dir().parent.resolve())
    with as_owner():
        for mode in ("read", "write"):
            result = check_path_access(theirs, mode)
            assert not result.allowed and "Another space's folder" in result.reason, result
        assert store_registry.is_kazma_store(theirs)
        assert "another space's folder" in store_registry.store_refusal(theirs, door="shell_exec")
    with matrix.as_a():
        assert store_registry.is_kazma_store(theirs)
    with matrix.as_b():
        assert not store_registry.is_kazma_store(theirs)
        assert check_path_access(theirs, "write").allowed
    assert store_registry.is_kazma_store(theirs)  # no space bound: nobody's to open
    # Control: without the rule the owner's file tools read B's file.
    monkeypatch.setattr(store_registry, "in_another_spaces_folder", lambda path: False)
    with as_owner():
        assert check_path_access(theirs, "read").allowed


def test_a_persons_database_in_their_space_is_theirs(matrix, enforced) -> None:
    from kazma_core import store_registry

    with matrix.as_a():
        mine = resolve_active_root() / "notes.db"
        mine.write_bytes(b"SQLite format 3\x00" + b"\x00" * 100)
        env = resolve_active_root() / ".env"
        env.write_text("MY_OWN=1", encoding="utf-8")
        assert not store_registry.is_kazma_store(mine)
        assert not store_registry.is_kazma_secret_file(env)
        assert check_path_access(mine, "write").allowed
        # Outside the working folder a database in the space folder is not a
        # person's file (uploads and images are no place for one).
        elsewhere = space_files.space_dir(Matrix.SPACE_A) / "attachments" / "x.db"
        assert store_registry.is_kazma_store(elsewhere)


def test_work_with_no_space_opens_no_file(matrix, enforced) -> None:
    result = check_path_access("note.md", "read")
    assert not result.allowed and "no space is bound" in result.reason
    assert path_policy.denied_message("note.md", "read", result=result).startswith("Safety: no space")


def test_a_space_whose_kind_cannot_be_read_gets_no_path(matrix, enforced, monkeypatch) -> None:
    def away() -> dict:
        raise identity.IdentityUnavailable("the settings store is away")

    monkeypatch.setattr(identity, "read_registry", away)
    with matrix.as_a():
        with pytest.raises(space_files.SpaceKindUnknown):
            resolve_active_root()
        result = check_path_access("note.md", "read")
        assert not result.allowed and "could not be read" in result.reason
        from kazma_core.tools import code_exec

        assert code_exec.local_exec_forbidden() is True
    # A space that is not a space of this install: the same.
    with bind_scope("no-such-space", OWNER_ACCOUNT_ID), pytest.raises(space_files.SpaceKindUnknown):
        space_files.sandboxed_space()


def test_a_host_space_other_than_the_owners_keeps_its_own_folders(matrix, enforced) -> None:
    identity.create_space("Dev", created_by=OWNER_ACCOUNT_ID, space_id="matrix-dev", kind="host")
    try:
        with as_owner():
            machine = resolve_active_root()
        with bind_scope("matrix-dev", OWNER_ACCOUNT_ID):
            assert space_files.sandboxed_space() is None
            # Its own root (S1-34): its own folder until it activates a
            # workspace of its own, never the machine's.
            assert resolve_active_root() == (space_files.space_dir("matrix-dev") / "files").resolve()
            assert resolve_active_root() != machine
            assert space_files.space_folder("images", create=False) == (
                space_files.space_dir("matrix-dev") / "images")
    finally:
        shutil.rmtree(space_files.space_dir("matrix-dev"), ignore_errors=True)


# ── controls for the matrix cases ────────────────────────────────────────


def _case(target: str):
    from tests._space_matrix import load_cases

    return load_cases()[target].run


def test_control_uploads_in_one_folder_are_everyones(matrix, monkeypatch) -> None:
    _case("POST /api/chat/upload")(matrix)
    from kazma_core.paths import data_dir

    monkeypatch.setattr(space_files, "space_folder",
                        lambda kind, *, install=None, create=True: Path(install or data_dir() / kind))
    with pytest.raises(AssertionError):
        _case("POST /api/chat/upload")(matrix)
    with pytest.raises(AssertionError):
        _case("GET /api/chat/files/{file_id}")(matrix)


def test_control_one_browser_page_for_everyone_shows_the_others_page(matrix, monkeypatch) -> None:
    _case("tool:browser_extract_text")(matrix)
    from kazma_skills.native.browser_automation import tools as browser

    monkeypatch.setattr(browser, "_page_space", lambda: None)
    with pytest.raises(AssertionError):
        _case("tool:browser_extract_text")(matrix)


def test_control_the_journal_for_everyone_shows_another_spaces_job(matrix, monkeypatch) -> None:
    _case("GET /api/research/acquisition")(matrix)
    from kazma_ui.research_panel import routes

    monkeypatch.setattr(routes, "_jobs_of_this_space", lambda: None)
    with pytest.raises(AssertionError):
        _case("GET /api/research/acquisition")(matrix)


def test_control_the_machines_report_folders_show_another_spaces_report(matrix, monkeypatch) -> None:
    _case("GET /api/research/papers/file")(matrix)
    monkeypatch.setattr(space_files, "sandboxed_files_root", lambda: None)
    monkeypatch.setattr(space_files, "sandboxed_space", lambda: None)
    monkeypatch.setattr(space_files, "in_sandboxed_space", lambda: False)
    with pytest.raises(AssertionError):
        _case("GET /api/research/papers/file")(matrix)


def test_control_the_local_runner_is_what_a_lab_switch_allows(matrix, enforced, monkeypatch) -> None:
    from kazma_core.tools import code_exec

    monkeypatch.setenv("KAZMA_CODE_EXEC_ALLOW_LOCAL", "1")
    monkeypatch.delenv("KAZMA_CODE_EXEC_DOCKER", raising=False)
    monkeypatch.setattr(code_exec, "container_required", lambda: False, raising=False)
    from kazma_core.safety import deployment_policy

    monkeypatch.setattr(deployment_policy, "container_required", lambda: False)
    with matrix.as_a():
        assert code_exec.local_exec_forbidden() is True
    with as_owner():
        assert code_exec.local_exec_forbidden() is False  # the owner's lab switch, as before
    monkeypatch.setattr(space_files, "in_sandboxed_space", lambda: False)
    monkeypatch.setattr(code_exec, "_outside_the_owners_space", lambda: False)
    from kazma_core.spaces import member_code

    # And without S4-05's rule (a person's code only in a container).
    monkeypatch.setattr(member_code, "code_rule", lambda: member_code._AS_BEFORE)
    with matrix.as_a():
        assert code_exec.local_exec_forbidden() is False  # the control


# ── a real container, where there is an engine ───────────────────────────


@pytest.mark.real_docker
def test_python_exec_in_a_container_sees_its_own_folder_only(matrix, enforced) -> None:
    import asyncio

    from kazma_core.tools import code_exec

    if not code_exec.docker_available():
        pytest.skip("no container engine on this machine")
    theirs = space_files.space_dir(Matrix.SPACE_B) / "files"
    theirs.mkdir(parents=True, exist_ok=True)
    (theirs / "note.md").write_text("b-only-word", encoding="utf-8")

    async def run() -> str:
        with matrix.as_a():
            root = await asyncio.to_thread(resolve_active_root)
            (root / "note.md").write_text("a-only-word", encoding="utf-8")
            try:
                await asyncio.to_thread(code_exec._workspace_mounts, root)
            except RuntimeError as exc:
                pytest.skip(f"the engine cannot mount the test's folder: {exc}")
            # The sandbox blocks the snippet's own ``os``/``pathlib`` imports;
            # ``glob`` and ``open`` are what a person's code has.
            return await code_exec.python_exec(
                "import glob\n"
                "for path in sorted(glob.glob('/workspace/**', recursive=True)):\n"
                "    try:\n"
                "        print(path, open(path).read())\n"
                "    except OSError:\n"
                "        pass\n"
            )

    shown = asyncio.run(run())
    # Every answer's header names the image, so only the engine's own words
    # for no engine or a missing image mean "skip": any other failure fails
    # the test. (The docker CLI being found says nothing of an engine: the
    # developer's current context may name one that is not there.)
    missing = ("unable to find image", "no such image", "pull access denied",
               "failed to connect to the docker api", "cannot connect to the docker daemon")
    if any(word in shown.lower() for word in missing) and "a-only-word" not in shown:
        pytest.skip(f"no engine with the sandbox image answers here: {shown[:200]}")
    assert "a-only-word" in shown, shown
    assert "b-only-word" not in shown, shown


def test_the_runner_never_falls_back_to_the_machine_python(matrix, enforced, monkeypatch) -> None:
    from kazma_core.tools import code_exec

    ran: list[str] = []

    async def local(*_a, **_k) -> str:
        ran.append("local")
        return "ran on the machine"

    async def no_engine(*_a, **_k) -> str:
        raise RuntimeError("docker CLI not found")

    monkeypatch.setattr(code_exec, "_run_local_subprocess", local)
    monkeypatch.setattr(code_exec, "_run_docker_jail", no_engine)
    monkeypatch.setenv("KAZMA_CODE_EXEC_ALLOW_LOCAL", "1")
    import asyncio

    async def as_a() -> str:
        with matrix.as_a():
            return await code_exec.python_exec("print(1)")

    shown = asyncio.run(as_a())
    assert not ran and "Docker jail" in shown, shown
    assert sys.platform  # (kept: the runner is the same on every platform)
