"""shell_exec never reads Kazma's secret files and stores, never writes its
configuration, and never leaves the workspace through a link.

2026-11 review, on an install whose workspace IS its install folder (as on
live: ``.env`` holds ``KAZMA_VAULT_KEY``, ``restic.pass`` opens the offsite
backups). The guard asked only the words that LOOKED like one of Kazma's
files (``_looks_like_store_token``) and held only the words that LOOKED like
a path to the workspace. Each of these printed a secret or read outside:

* ``grep -r -a KAZMA_VAULT_KEY .`` (a folder holding the secrets, read whole);
* ``cat ENV~1`` and ``cat RESTIC~1.PAS`` (Windows short names);
* ``cat link/secret.txt`` (a junction inside the workspace leading out).

And git and tar started other programs through options the denylist lacked
(``git web--browse --browser=``, ``git send-email --to-cmd=``, ``git
instaweb --httpd=``, ``tar --info-script=``), and ``tar -f host:x`` opens an
archive on another machine.

Every case runs the real tool on a temporary install with a planted ``.env``;
the runner is replaced by a recorder, so "refused" means nothing ran. The old
guard, rebuilt here, lets each attack through (negative controls).
"""

from __future__ import annotations

import ast
import os
import subprocess
from pathlib import Path

import pytest

from kazma_core.agent.tool_refusal import RefusedBeforeEffect

SECRET = "demo-vault-key-value"

WINDOWS = os.name == "nt"


def _junction(link: Path, target: Path) -> None:
    if WINDOWS:
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                       check=True, capture_output=True)
    else:
        os.symlink(target, link, target_is_directory=True)


@pytest.fixture()
def install(tmp_path, monkeypatch):
    """An install whose workspace is the install folder, as on live."""
    from kazma_core import paths

    root = tmp_path / "install"
    data = root / "kazma-data"
    (data / "workspace").mkdir(parents=True)
    (root / ".kazma").mkdir()
    monkeypatch.setenv("KAZMA_DATA_DIR", str(data))
    monkeypatch.setenv("KAZMA_USER_HOME", str(root / ".kazma"))
    monkeypatch.setenv("KAZMA_SHELL_ALLOW_MUTATE", "1")
    monkeypatch.setenv("KAZMA_SHELL_ALLOW_ARCHIVE", "1")
    monkeypatch.delenv("KAZMA_ENV_FILE", raising=False)
    monkeypatch.setattr(paths, "installed_project_root", lambda: root)
    monkeypatch.setattr(paths, "get_project_root", lambda: root)
    monkeypatch.chdir(root)
    (root / ".env").write_text(f"KAZMA_VAULT_KEY={SECRET}\n", encoding="utf-8")
    (root / "restic.pass").write_text(SECRET + "\n", encoding="utf-8")
    (data / "memory_state.db").write_bytes(b"SQLite format 3\x00" + SECRET.encode())
    (root / "kazma.yaml").write_text("safety:\n  hitl:\n    enabled: true\n", encoding="utf-8")
    (root / "notes.txt").write_text("hello\n", encoding="utf-8")
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("print('hello')\n", encoding="utf-8")
    (root / "payload").mkdir()
    (root / "payload" / "kazma.local.yaml").write_text("safety: {hitl: {enabled: false}}\n",
                                                     encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("outside-secret\n", encoding="utf-8")
    _junction(root / "link", outside)
    return root


@pytest.fixture()
def shell(monkeypatch, install):
    """shell_exec in the install, with a runner that records instead of running."""
    from kazma_core.agent.tool_builtins import register_builtin_tools
    from kazma_core.agent.tool_registry import LocalToolRegistry
    from kazma_core.ide.workspace_scope import workspace_path_scope
    from kazma_core.safety import post_hitl
    from kazma_core.security import process_budget

    real_resolve = post_hitl.resolve_shell_binary
    monkeypatch.setattr(post_hitl, "resolve_shell_binary",
                        lambda argv0, **kw: real_resolve(argv0, **kw) or argv0)
    ran: list[list[str]] = []

    async def record(args, **_kwargs):
        ran.append(list(args))
        return subprocess.CompletedProcess(args, 0, b"ok", b"")

    monkeypatch.setattr(process_budget, "run_bounded_async", record)
    registry = LocalToolRegistry()
    register_builtin_tools(registry)

    async def run(command: str) -> str:
        async with workspace_path_scope(install):
            return await registry._tools["shell_exec"].func(command, timeout=10)

    async def before_the_card(command: str) -> str | None:
        from kazma_core.safety.call_preflight import call_refusal

        async with workspace_path_scope(install):
            return await call_refusal("shell_exec", {"command": command})

    run.ran = ran  # type: ignore[attr-defined]
    run.before_the_card = before_the_card  # type: ignore[attr-defined]
    return run


async def _refused(shell, command: str) -> str:
    with pytest.raises(RefusedBeforeEffect) as refused:
        await shell(command)
    assert shell.ran == [], f"{command!r} ran: {shell.ran}"
    text = str(refused.value)
    assert SECRET not in text
    return text


# ── item 1: Kazma's secret files and stores, by any name ─────────────────

READS_A_FOLDER = [
    "grep -r -a KAZMA_VAULT_KEY .",
    "grep -r KAZMA_VAULT_KEY",            # no folder: grep -r reads the working folder
    "grep -R KAZMA_VAULT_KEY .",
    "grep --recursive KAZMA_VAULT_KEY",
    "grep -d recurse KAZMA_VAULT_KEY .",
    "find .",
    "find",
    "find . -name .env",
    "ls -R",
    "ls -laR .",
    "du",
    "du -sh .",
    "tar -c -f out.tar .",
    "tar -cf out.tar .",
    "tar -c -C kazma-data -f out.tar .",
    "cp -r . copy",
    "cp -a . copy",
    "mv kazma-data moved",
    "zip -r out.zip .",
    "git add -A",
    "git add .",
    "git stash -u",
    "git stash push --all",
    "git grep --no-index KAZMA_VAULT_KEY",
    "grep -r KAZMA kazma-data",
]


@pytest.mark.parametrize("command", READS_A_FOLDER)
async def test_a_folder_holding_kazma_files_is_not_read(shell, command):
    text = await _refused(shell, command)
    assert "Kazma's own" in text, text


NAMES_A_SECRET = [
    "cat .env",
    "cat .ENV",
    "cat ./.env",
    "cat .env.",
    "cat .env/.",
    "cat restic.pass",
    "grep KAZMA kazma-data/memory_state.db",
    "cat .e*",
    "cat *",
    "cat rest?c.pass",
    "cat {notes.txt,.env}",
    "head -c100 .env",
    "wc --files=.env",
]


@pytest.mark.parametrize("command", NAMES_A_SECRET)
async def test_a_secret_named_any_way_is_refused(shell, command):
    text = await _refused(shell, command)
    assert "Kazma" in text, text


@pytest.mark.skipif(not WINDOWS, reason="short names and NTFS streams are Windows'")
@pytest.mark.parametrize("command", [
    "cat ENV~1", "cat RESTIC~1.PAS", "cat .env::$DATA", "cat RESTIC*.PAS", "cat ENV*",
])
async def test_a_windows_name_for_a_secret_is_refused(shell, install, command):
    if not (install / "ENV~1").exists() or not (install / "RESTIC~1.PAS").exists():
        pytest.skip("this volume makes no short (8.3) names")
    text = await _refused(shell, command)
    assert "Kazma" in text, text


ORDINARY = [
    "ls",
    "ls -la",
    "cat notes.txt",
    "grep -n hello notes.txt",
    "grep -rn hello src",
    "find src -name app.py",
    "du -sh src",
    "cat *.txt",
    "cat kazma.yaml",
    "grep -n hitl kazma.yaml",
    "tar -c -f out.tar notes.txt src",
    "cp -r src copy",
    "git status --short",
    "git log -3 --oneline",
    "git diff --stat",
    "git add notes.txt src",
    "git commit -m tidy",
    "jq .items[0] notes.txt",
]


@pytest.mark.parametrize("command", ORDINARY)
async def test_ordinary_commands_still_run(shell, command):
    out = await shell(command)
    assert not out.startswith("Error:"), out
    assert len(shell.ran) == 1, command


async def test_refused_before_the_approval_card(shell, install):
    """The chat graph and the registry ask call_refusal before a card: the
    owner is never asked to approve a call that cannot run."""
    for command in ("grep -r -a KAZMA_VAULT_KEY .", "cat .e*", "cat link/secret.txt",
                    "git web--browse --browser=start x", "tar -c -f host:x.tar notes.txt",
                    "cp payload/kazma.local.yaml ."):
        said = await shell.before_the_card(command)
        assert said, command
        assert SECRET not in said
    if WINDOWS and (install / "ENV~1").exists():
        assert await shell.before_the_card("cat ENV~1")
    assert await shell.before_the_card("cat notes.txt") is None
    assert await shell.before_the_card("grep -rn hello src") is None


def _old_guard(command: str, cwd: Path) -> str | None:
    """The guard before this change: a word is refused only when it looks
    like a store's name, and held to the workspace only when it looks like
    a path."""
    from kazma_core.safety.shell_arguments import split_command_line
    from kazma_core.workspace.path_policy import control_plane_store_targeted

    for word in split_command_line(command)[1:]:
        if not word or word.startswith("-"):
            continue
        if control_plane_store_targeted(word, cwd=str(cwd)):
            return "store"
        looks_path = (
            word.startswith(("/", "\\"))
            or (len(word) > 2 and word[1] == ":" and word[0].isalpha())
            or ".." in word.replace("\\", "/")
        )
        if looks_path:
            real = os.path.realpath(os.path.join(str(cwd), word))
            if not real.startswith(os.path.realpath(str(cwd))):
                return "outside"
    return None


async def test_control_the_old_guard_let_each_attack_through(install):
    from kazma_core.ide.workspace_scope import workspace_path_scope

    attacks = ["grep -r -a KAZMA_VAULT_KEY .", "cat link/secret.txt", "cat .e*", "find .",
               "tar -c -f out.tar ."]
    if WINDOWS and (install / "ENV~1").exists():
        attacks += ["cat ENV~1", "cat RESTIC~1.PAS"]
    async with workspace_path_scope(install):
        for command in attacks:
            assert _old_guard(command, install) is None, command
        # The old guard did refuse the plain name: the attacks went around it.
        assert _old_guard("cat .env", install) == "store"


# ── item 5: a link or junction does not lead out ──────────────────────────


async def test_a_junction_does_not_lead_shell_out(shell):
    for command in ("cat link/secret.txt", "grep outside link/secret.txt", "cat link/*",
                    "grep -r outside link"):
        text = await _refused(shell, command)
        assert "outside" in text, text


async def test_a_junction_does_not_lead_the_file_tools_out(install):
    from kazma_core.agent.tool_builtins import filesystem
    from kazma_core.ide.workspace_scope import workspace_path_scope
    from kazma_core.tools.file_read import clear_read_cache, file_read
    from kazma_core.tools.file_write import configure_workspace

    from tests._tool_answer import answer

    # The suite's conftest lets file tools reach temp files anywhere; the
    # product does not (the autouse fixture restores it after the test).
    configure_workspace(workspace=None, allow_absolute=False)
    tools: dict[str, object] = {}

    class _Capture:
        def register(self, **_kw):
            def deco(fn):
                tools[fn.__name__] = fn
                return fn

            return deco

    filesystem.register_filesystem_tools(_Capture())
    async with workspace_path_scope(install):
        clear_read_cache()
        read = await answer(file_read("link/secret.txt"))
        assert "outside-secret" not in read and "outside" in read, read
        found = await answer(tools["file_search"]("outside-secret", path=".", glob="*"))
        assert "outside-secret" not in found.replace("'outside-secret'", ""), found


@pytest.mark.skipif(subprocess.run(["git", "--version"], capture_output=True).returncode != 0,
                    reason="git is not installed")
async def test_git_listing_through_a_junction_is_not_searched(tmp_path, monkeypatch):
    """git lists the files of a junction as the repository's own; file_search
    read them (the reviewer's file read through a junction)."""
    from kazma_core.agent.tool_builtins import filesystem
    from kazma_core.ide.workspace_scope import workspace_path_scope
    from kazma_core.workspace import project_files

    from tests._tool_answer import answer

    ws = tmp_path / "ws"
    ws.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("outside-secret\n", encoding="utf-8")
    (ws / "mine.txt").write_text("hello\n", encoding="utf-8")
    _junction(ws / "link", outside)
    subprocess.run(["git", "init", "-q"], cwd=ws, check=True)
    listed = project_files.git_project_files(ws)
    assert listed is not None and ws / "mine.txt" in listed
    assert not any("secret" in str(p) for p in listed), listed

    tools: dict[str, object] = {}

    class _Capture:
        def register(self, **_kw):
            def deco(fn):
                tools[fn.__name__] = fn
                return fn

            return deco

    filesystem.register_filesystem_tools(_Capture())
    async with workspace_path_scope(ws):
        found = await answer(tools["file_search"]("outside-secret", path=".", glob="*"))
    assert "secret.txt" not in found, found

    # Negative control: git itself lists the junction's file; the listing
    # without the real-path filter is what file_search read.
    raw = subprocess.run(["git", "ls-files", "--others", "--exclude-standard"], cwd=ws,
                         capture_output=True, text=True).stdout
    if "link/secret.txt" in raw:
        monkeypatch.setattr(project_files, "_inside_the_root", lambda root, listed: listed)
        assert any("secret" in str(p) for p in project_files.git_project_files(ws))


def test_a_junction_is_a_linked_folder_on_every_python(install, monkeypatch):
    from kazma_core.agent.tool_builtins.filesystem import _is_linked_dir
    from kazma_core.workspace.real_paths import is_linked

    assert is_linked(install / "link") and _is_linked_dir(str(install / "link"))
    assert not is_linked(install / "src")
    if WINDOWS:
        # Negative control: the old check on Python 3.11 (no isjunction).
        monkeypatch.delattr(os.path, "isjunction", raising=False)
        old = os.path.islink(str(install / "link")) or bool(
            getattr(os.path, "isjunction", None) and os.path.isjunction(str(install / "link"))
        )
        assert old is False
        assert is_linked(install / "link")


# ── items 3 and 4: options that start a program; tar's remote archive ────

STARTS_A_PROGRAM = [
    ("git web--browse --browser=start payload.bat", "git web--browse"),
    ("git send-email --to-cmd=whoami --dry-run notes.txt", "git send-email"),
    ("git instaweb --httpd=whoami", "git instaweb"),
    ("git help -w log", "git help"),
    ("git maintenance start", "git maintenance"),
    ("git pull", "git pull"),
    ("git ls-remote origin", "git ls-remote"),
    ("git am notes.txt", "git am"),
    ("git apply notes.txt", "git apply"),
    ("git imap-send", "git imap-send"),
    ("git bisect visualize", "git bisect"),
    ("git my-alias", "git my-alias"),
    ("tar -c --multi-volume --tape-length=10 --info-script=whoami -f x.tar notes.txt", "--multi-volume"),
    ("tar -c --info-script=whoami -f x.tar notes.txt", "--info-script"),
    ("tar -c --info=whoami -f x.tar notes.txt", "--info"),
    ("tar -c --new-volume-script=whoami -f x.tar notes.txt", "--new-volume-script"),
    ("tar -c -F whoami -f x.tar notes.txt", "-F"),
    ("tar -c --checkpoint-action=exec=whoami -f x.tar notes.txt", "--checkpoint-action"),
    ("tar -c --to-command=whoami -f x.tar notes.txt", "--to-command"),
    ("tar -c --rsh-command=whoami -f x.tar notes.txt", "--rsh-command"),
    ("tar -c --rmt-command=whoami -f x.tar notes.txt", "--rmt-command"),
    ("tar -c -I whoami -f x.tar notes.txt", "-I"),
    ("tar -c --use-compress=whoami -f x.tar notes.txt", "--use-compress"),
    ("tar -c --listed-incremental=snap -f x.tar notes.txt", "--listed-incremental"),
]


@pytest.mark.parametrize("command,said", STARTS_A_PROGRAM)
async def test_an_option_that_starts_a_program_is_refused(shell, command, said):
    text = await _refused(shell, command)
    assert said in text, text


REMOTE_ARCHIVES = [
    "tar -c -f host:x.tar notes.txt",
    "tar -cf user@host:/srv/x.tar notes.txt",
    "tar -c --file=host:/x.tar notes.txt",
    "tar -c --file host:x.tar notes.txt",
    "tar -cvfhost:x.tar notes.txt",
]


@pytest.mark.parametrize("command", REMOTE_ARCHIVES)
async def test_a_remote_archive_is_refused(shell, command):
    text = await _refused(shell, command)
    # On Windows a one-letter tail of the word (``t:x.tar``) is a drive path
    # outside the workspace, refused first; ``--file`` is refused as an
    # abbreviation of ``--files-from`` before the archive is read.
    assert "host:path" in text or "outside" in text or "--file" in text, text


def test_the_archive_names_are_read_in_every_form():
    from kazma_core.safety.shell_arguments import _remote_archive_reason, _tar_archive_names

    for words, name in (
        (["-c", "-f", "host:x.tar", "a"], "host:x.tar"),
        (["-cf", "user@host:/x.tar", "a"], "user@host:/x.tar"),
        (["-c", "--file=host:/x.tar", "a"], "host:/x.tar"),
        (["-c", "--file", "host:x.tar", "a"], "host:x.tar"),
        (["-cvfhost:x.tar", "a"], "host:x.tar"),
        (["-c", "-C", "dir", "-f", "out.tar", "a"], "out.tar"),
    ):
        names = _tar_archive_names(words)
        assert names == [name], (words, names)
        assert bool(_remote_archive_reason(name)) is (":" in name), name


async def test_a_local_archive_still_runs(shell, install):
    await shell("tar -c -f out.tar notes.txt")
    if WINDOWS:
        await shell(f"tar -c -f {install / 'out2.tar'} notes.txt")
    assert len(shell.ran) == (2 if WINDOWS else 1)


def test_control_the_old_tables_missed_each_option():
    """The tables before this change: a denylist of git subcommands and of tar options."""
    old_git_blocked = {
        "push", "credential", "credential-manager", "credential-store", "credential-cache",
        "reset", "rebase", "filter-branch", "filter-repo", "remote", "submodule",
        "clone", "fetch", "archive", "daemon", "http-backend", "mergetool", "difftool",
    }
    old_tar = ("--use-compress-program", "--to-command", "-I", "--checkpoint-action",
               "--rmt-command", "--rsh-command", "-T", "--files-from", "--extract", "--get",
               "--absolute-names", "--dereference")
    from kazma_core.safety.shell_arguments import matches_blocked

    for sub in ("web--browse", "send-email", "instaweb", "help", "maintenance", "pull",
                "ls-remote", "am", "apply", "imap-send"):
        assert sub not in old_git_blocked
    for flag in ("--info-script", "--new-volume-script", "--multi-volume", "--tape-length",
                 "--listed-incremental"):
        assert not matches_blocked(flag, old_tar)


def test_no_allowed_git_subcommand_starts_a_program():
    """Gate: git's subcommands that start a program they are told to, reach
    the network or rewrite history stay off the allowlist."""
    from kazma_core.safety.shell_arguments import GIT_SUBCOMMANDS

    starts_or_reaches = {
        "web--browse", "send-email", "instaweb", "help", "difftool", "mergetool",
        "bisect", "maintenance", "submodule", "filter-branch", "rebase", "imap-send",
        "daemon", "http-backend", "credential", "pull", "fetch", "push", "clone",
        "ls-remote", "remote", "archive", "am", "apply", "gui", "citool", "p4", "svn",
        "cvsimport", "cvsexportcommit", "cvsserver", "quiltimport", "request-pull",
        "upload-pack", "receive-pack", "upload-archive", "shell", "fast-import",
        "update-index", "checkout-index", "hash-object", "read-tree", "commit-tree",
        "update-ref", "replace", "notes", "clean", "reset", "gc", "repack", "prune",
        "bundle", "sparse-checkout", "submodule--helper",
    }
    assert not (GIT_SUBCOMMANDS & starts_or_reaches), GIT_SUBCOMMANDS & starts_or_reaches


@pytest.mark.parametrize("name,expected", [
    ("host:x.tar", True), ("user@host:/x", True), ("h:x", True), ("out.tar", False),
    ("dir/a:b.tar", False), ("./a:b", False),
])
def test_remote_archive_reason(name, expected):
    from kazma_core.safety.shell_arguments import _remote_archive_reason

    assert bool(_remote_archive_reason(name)) is expected


@pytest.mark.skipif(not WINDOWS, reason="a drive path is Windows'")
def test_a_drive_path_is_a_local_archive():
    from kazma_core.safety.shell_arguments import _remote_archive_reason

    assert _remote_archive_reason("C:\\x.tar") is None
    assert _remote_archive_reason("C:/x.tar") is None


# ── the gate: every allowlisted program is classified ─────────────────────


def _repo() -> Path:
    return Path(__file__).resolve().parents[1]


def _allowlisted_programs() -> set[str]:
    """Every name shell_exec's allowlist holds, read from its source."""
    source = (_repo() / "kazma-core/kazma_core/agent/tool_builtins/system.py").read_text(
        encoding="utf-8")
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        targets: list[ast.AST] = []
        value: ast.AST | None = None
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AugAssign):
            targets, value = [node.target], node.value
        if not any(isinstance(t, ast.Name) and t.id == "_SAFE_BINARIES" for t in targets):
            continue
        for sub in ast.walk(value):
            if isinstance(sub, ast.Set):
                names |= {e.value for e in sub.elts if isinstance(e, ast.Constant)}
    return names


def test_every_allowlisted_program_says_how_it_walks_folders():
    """Gate: a program added to shell_exec's allowlist must be classified:
    in WALKS (how it walks the folders it is given) or NO_WALK (why it never
    reads a folder's files)."""
    from kazma_core.safety.shell_paths import NO_WALK, WALKS

    allowed = _allowlisted_programs()
    assert {"grep", "find", "tar", "git", "cat"} <= allowed, allowed
    missing = allowed - set(WALKS) - set(NO_WALK)
    assert not missing, f"classify these in safety/shell_paths WALKS or NO_WALK: {missing}"
    assert not (set(WALKS) & set(NO_WALK))


def test_the_allowlist_never_holds_a_program_that_reaches_kazmas_secrets():
    """Gate: no mode of shell_exec's allowlist holds a program of
    shell_paths.NEVER_ALLOWED (2026-11-16: the development allowlist, the
    default after setup, held `kazma`, whose export writes the vault and its
    key into a bundle, and `ps`, whose `eww` prints the server's environment)."""
    from kazma_core.safety.shell_paths import NEVER_ALLOWED

    assert not (_allowlisted_programs() & set(NEVER_ALLOWED))


def test_the_never_allowed_gate_sees_the_old_allowlist():
    """Negative control: the development extras as they were."""
    from kazma_core.safety.shell_paths import NEVER_ALLOWED

    old = _allowlisted_programs() | {"ps", "pgrep", "kazma"}
    assert old & set(NEVER_ALLOWED) == {"ps", "kazma"}


@pytest.mark.parametrize("command", [
    "kazma migrate export --out bundle.zip",
    "kazma --version",
    "kazma.exe doctor",
    "ps eww",
    "ps -E",
])
async def test_a_development_install_refuses_kazma_and_ps(shell, monkeypatch, command):
    """Behaviour: with KAZMA_PRODUCTION unset (the default after setup) the
    tool refuses Kazma's command line and ps before anything runs."""
    monkeypatch.delenv("KAZMA_PRODUCTION", raising=False)
    with pytest.raises(RefusedBeforeEffect):
        await shell(command)
    assert shell.ran == []


async def test_a_development_install_still_runs_pgrep(shell, monkeypatch):
    """Positive control: the development extra that stays still runs."""
    monkeypatch.delenv("KAZMA_PRODUCTION", raising=False)
    await shell("pgrep python")
    assert shell.ran and Path(shell.ran[0][0]).name.split(".")[0] == "pgrep"


def test_the_gate_sees_a_new_program():
    """Negative control: a planted allowlisted program is unclassified."""
    from kazma_core.safety.shell_paths import NO_WALK, WALKS

    planted = _allowlisted_programs() | {"rsync"}
    assert planted - set(WALKS) - set(NO_WALK) == {"rsync"}


def test_no_product_code_asks_isjunction():
    """Gate: ``os.path.isjunction`` does not exist on Python 3.11 (CI's and
    the live install's), so a walk that asked it entered junctions there.
    workspace.real_paths.is_linked is the one check."""
    out = subprocess.run(["git", "ls-files", "-z", "--", "kazma-*"], cwd=_repo(),
                         capture_output=True, check=True).stdout.decode("utf-8")
    users = [
        rel for rel in out.split("\0")
        if rel.endswith(".py") and "/tests/" not in rel
        and "isjunction(" in (_repo() / rel).read_text(encoding="utf-8")
    ]
    assert not users, users
