"""Git with no approval card runs an allowlist of forms; everything else asks
(AGENTS.md sections 10 and 37; ``ide/git_policy.py``,
``safety/git_kazma_files.py``).

Two doors run git with no card: the IDE's git (``/api/ide/git``, the chat
command ``/ide git``, the TUI: ``IdeService.git``) and the agent's git read
tools (``git_status``, ``git_log``, ``git_diff``). A command runs there only
when it is one of ``git_policy.CARD_LESS``'s forms, and a form that prints a
file's contents only as ``git_kazma_files`` judges it in the repository:
every revision a commit of the repository's own history, Kazma's paths
excluded at the source and their sections dropped after, a line saying how
many items were left out, the section shape fixed. Anything else goes to the
approval card, which shows the exact command.

What this file holds:

- the allowlist, enumerated from ``CARD_LESS``: every form runs card-less in
  an ordinary repository and shows the user's work;
- everything outside it asks: every verb git lists, every option its help
  names and the output-shaping ones it does not, the values past a bound,
  the operands that name an object -- with a planted extra form as control;
- each way a Kazma file reaches what git prints (committed, a tree, a blob,
  a note, a stash, a commit no ref holds, a replace ref, a submodule, the
  repository's configuration, a linked worktree, a junction, a short name),
  read through both doors, the secret never back -- with the old rule, each
  piece taken out and each force flag dropped as controls;
- the exclusions agree with git; the "not shown" line, in both languages.
"""

from __future__ import annotations

import asyncio
import base64
import os
import re
import shutil
import string
import subprocess
import zlib
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

SECRET = "demo_vault_key_Rk42"
OTHER = "text_of_another_repository_Qm7"
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
NOT_SHOWN_AR = "من ملفات Kazma الخاصة"

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="these reads need git")


# ── An install whose workspace is the install folder, as on live ────────


@pytest.fixture(autouse=True)
def carded(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every command the IDE sends to its approval card, recorded; none runs."""
    from kazma_core.ide.service import IdeService

    calls: list[str] = []

    async def _card(self: Any, command: str, timeout: int = 60) -> dict[str, Any]:
        calls.append(command)
        return {"ok": False, "error": "waits for its approval card", "output": ""}

    monkeypatch.setattr(IdeService, "run", _card)
    return calls


@pytest.fixture()
def ws(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from kazma_core import paths
    from kazma_core.ide.service import reset_ide_service
    from kazma_core.workspace.binding import configure_workspace

    root = tmp_path / "install"
    data = root / "kazma-data"
    (data / "backups" / "1").mkdir(parents=True)
    (root / ".kazma").mkdir()
    (root / "conf").mkdir()
    (root / "src").mkdir()
    monkeypatch.setenv("KAZMA_DATA_DIR", str(data))
    monkeypatch.setenv("KAZMA_USER_HOME", str(root / ".kazma"))
    monkeypatch.setenv("KAZMA_WORKSPACE", str(root))
    monkeypatch.setenv("KAZMA_ENV_FILE", str(root / "conf" / "kazma_env.py"))
    monkeypatch.setattr(paths, "installed_project_root", lambda: root)
    monkeypatch.setattr(paths, "get_project_root", lambda: root)
    monkeypatch.setattr(
        "kazma_core.stores.get_workspace_store",
        lambda: type("S", (), {"get_active_workspace": staticmethod(lambda: None)})(),
    )
    configure_workspace(workspace=str(root))
    for rel in (".env", "kazma-data/backups/1/.env"):
        (root / rel).write_text(f"KAZMA_VAULT_KEY={SECRET}\n", encoding="utf-8")
    (root / "conf" / "kazma_env.py").write_text(f'VAULT_KEY = "{SECRET}"\n', encoding="utf-8")
    (data / "vault.db").write_bytes(b"SQLite format 3\x00" + SECRET.encode())
    (data / "memo.bin").write_bytes(b"SQLite format 3\x00" + SECRET.encode() + bytes(range(256)))
    (root / "src" / "app.py").write_text("def greet_all(items):\n    return items\n", encoding="utf-8")
    (root / ".env.example").write_text("KAZMA_VAULT_KEY=put_yours_here\n", encoding="utf-8")
    reset_ide_service()
    yield root
    reset_ide_service()


@pytest.fixture()
def user_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A user's own project, Kazma's data dir elsewhere: history, a branch, a
    tag, a stash entry, and a change in the work tree."""
    from kazma_core.ide.service import reset_ide_service
    from kazma_core.workspace.binding import configure_workspace

    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path / "elsewhere" / "kazma-data"))
    monkeypatch.delenv("KAZMA_ENV_FILE", raising=False)
    monkeypatch.setattr(
        "kazma_core.stores.get_workspace_store",
        lambda: type("S", (), {"get_active_workspace": staticmethod(lambda: None)})(),
    )
    root = tmp_path / "project"
    (root / "src").mkdir(parents=True)
    monkeypatch.setenv("KAZMA_WORKSPACE", str(root))
    configure_workspace(workspace=str(root))
    _init(root)
    (root / "src" / "app.py").write_text("def greet_all(items):\n    return items\n", encoding="utf-8")
    _commit(root, message="first")
    _git(root, "tag", "v1")
    _git(root, "branch", "side")
    (root / "src" / "app.py").write_text("def greet_all(items):\n    return sorted(items)\n", encoding="utf-8")
    _commit(root, message="sorted greeting")
    (root / "src" / "app.py").write_text("def greet_all(items):\n    return list(items)\n", encoding="utf-8")
    _git(root, "stash", "-q")
    (root / "src" / "app.py").write_text("def greet_all(items):\n    return tuple(items)\n", encoding="utf-8")
    reset_ide_service()
    yield root
    reset_ide_service()


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        [shutil.which("git") or "git", "-c", "core.fsmonitor=false", "-c", "core.autocrlf=false",
         "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false", *args],
        cwd=root, capture_output=True, text=True, check=True,
    ).stdout


def _init(root: Path) -> None:
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.name", "Kazma Test")
    _git(root, "config", "user.email", "test@example.invalid")


def _commit(root: Path, *paths: str, message: str = "a change") -> str:
    _git(root, "add", "-f", "--", *(paths or (".",)))
    _git(root, "commit", "-q", "--allow-empty", "-m", message)
    return _git(root, "rev-parse", "HEAD").strip()


def _ide(command: str, *, admitted_as_read: bool = False) -> dict[str, Any]:
    from kazma_core.ide.service import get_ide_service

    return asyncio.run(get_ide_service().git(command, admitted_as_read=admitted_as_read))


def _agent(**kwargs: Any) -> str:
    from kazma_skills.native.git_github_manager.tools import git_diff

    return asyncio.run(git_diff(**kwargs))


def _text(answer: Any) -> str:
    if isinstance(answer, str):
        return answer
    return f"{answer.get('output') or ''}\n{answer.get('error') or ''}"


_B85_LENGTH = {c: i + 1 for i, c in enumerate(string.ascii_uppercase)}
_B85_LENGTH.update({c: i + 27 for i, c in enumerate(string.ascii_lowercase)})


def _binary_patches(text: str) -> bytes:
    """Every ``GIT binary patch`` block of *text*, decoded from base85 and
    inflated: a store's bytes do not show in a patch's text."""
    out, chunk, reading = bytearray(), bytearray(), False

    def flush() -> None:
        nonlocal chunk
        if chunk:
            try:
                out.extend(zlib.decompress(bytes(chunk)))
            except zlib.error:
                out.extend(chunk)
            chunk = bytearray()

    for line in text.splitlines():
        if line.startswith(("literal ", "delta ")):
            flush()
            reading = True
        elif reading and line and line[0] in _B85_LENGTH:
            try:
                chunk.extend(base64.b85decode(line[1:])[: _B85_LENGTH[line[0]]])
            except ValueError:
                reading = False
        else:
            flush()
            reading = False
    flush()
    return bytes(out)


def _leaked(answer: Any) -> bool:
    text = _text(answer)
    return SECRET in text or SECRET.encode() in _binary_patches(text)


def test_the_binary_decoder_finds_a_store_in_a_patch(ws: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The instrument first: a binary patch of the store holds the secret
    only once decoded."""
    _init(ws)
    _commit(ws, "kazma-data/memo.bin")
    patch = _git(ws, "show", "--binary", "--format=", "HEAD")
    assert "GIT binary patch" in patch and SECRET not in patch
    assert _leaked(patch)


# ── The allowlist, from the source: each form runs, with no card ────────


_VALUE_EXAMPLES = {
    "--unified": "1", "-U": "1", "--diff-filter": "M", "--pretty": "fuller", "--format": "%h|%an|%s",
    "--date": "iso", "-n": "5", "--max-count": "5", "--since": "2000-01-01", "--until": "2100-01-01",
    "--author": "Kazma", "--grep": "greeting", "-L": "1,1", "--abbrev": "7", "--match": "v*",
    "--exclude": "x", "--group": "author", "--sort": "refname", "--untracked-files": "all",
    "--porcelain": "v1", "--ignored": "matching", "--short": "7",
}
_REF_FORMAT = "%(refname:short)|%(objectname:short)|%(subject)"
#: Operands a form is read with in the user's repository.
_OPERANDS = {"blame": "src/app.py", "describe": "--always", "rev-parse": "HEAD", "show": "HEAD"}


def card_less_commands() -> list[str]:
    """Every flag and value of every card-less form, from ``CARD_LESS``."""
    from kazma_core.ide.git_policy import CARD_LESS

    commands: list[str] = []
    for key, form in sorted(CARD_LESS.items()):
        operand = _OPERANDS.get(key, "")
        for flag in sorted(form.flags):
            commands.append(f"{key} {flag} {operand}".strip())
        for name in sorted(form.values):
            value = _REF_FORMAT if key in ("branch", "tag") and name == "--format" else _VALUE_EXAMPLES[name]
            spelled = f"{name}={value}" if name.startswith("--") else f"{name} {value}"
            commands.append(f"{key} {spelled} {operand}".strip())
    return commands


def test_every_card_less_form_runs_with_no_card(user_repo: Path, carded: list[str]) -> None:
    commands = card_less_commands()
    assert len(commands) > 150
    asked = []
    for command in commands:
        _ide(command)
        if carded:
            asked.append(command)
            carded.clear()
    assert not asked, f"card-less forms that went to the card: {asked}"


def test_the_users_own_work_shows_in_every_content_read(user_repo: Path, carded: list[str]) -> None:
    shown = {
        "diff": "tuple(items)", "diff HEAD": "tuple(items)", "diff HEAD~1 HEAD": "sorted(items)",
        "diff v1..main": "sorted(items)", "show": "sorted(items)", "show HEAD~1": "return items",
        "log -p": "sorted(items)", "log -p -1 -- src": "sorted(items)", "log -p side..main": "sorted(items)",
        "blame src/app.py": "tuple(items)", "show --stat HEAD": "src/app.py", "log --oneline": "sorted greeting",
        "stash list": "stash@{0}", "status -s": "src/app.py", "branch --list": "side", "tag -l": "v1",
    }
    for command, words in shown.items():
        answer = _ide(command)
        assert answer["ok"] is True and words in answer["output"], (command, answer)
        assert "Kazma's own files" not in answer["output"], (command, answer)
    assert carded == []
    assert "tuple(items)" in _agent()
    assert "sorted(items)" in _agent(target="HEAD")
    assert "sorted(items)" in _agent(target="v1..main")


# ── Everything outside the list asks, with the exact command ────────────


def _git_verbs() -> list[str]:
    """Every command git lists (its own and the ones on the PATH)."""
    listed = subprocess.run([shutil.which("git") or "git", "--list-cmds=main,others"],
                            capture_output=True, text=True, check=True).stdout.split()
    return sorted(set(listed))


def _help_options(verb: str) -> set[str]:
    """The options ``git <verb> -h`` names (``--[no-]x`` as both)."""
    shown = subprocess.run([shutil.which("git") or "git", verb, "-h"], capture_output=True,
                           text=True, check=False)
    found: set[str] = set()
    for line in (shown.stdout + shown.stderr).splitlines():
        if not line.lstrip().startswith("-"):
            continue
        for token in re.findall(r"(?<![\w\[-])(--?(?:\[no-\])?[A-Za-z0-9][A-Za-z0-9-]*)", line):
            if "[no-]" in token:
                found.update({token.replace("[no-]", ""), token.replace("[no-]", "no-")})
            else:
                found.add(token)
    return found


#: Output-shaping options of diff, show and log their ``-h`` leaves out.
_SHAPING = (
    "--word-diff", "--color-words", "--line-prefix=x", "--no-prefix", "--src-prefix=x/", "--dst-prefix=y/",
    "--default-prefix", "--text", "-a", "-M", "-C", "-B", "-D", "--find-copies-harder", "--relative",
    "--relative=src", "--no-relative", "--output=x.txt", "--ext-diff", "--textconv", "--remerge-diff", "--cc",
    "-c", "-m", "--diff-merges=on", "--full-index", "--abbrev", "-W", "--function-context",
    "--inter-hunk-context=9", "--output-indicator-new=x", "--color", "--color=always", "--submodule",
    "--submodule=diff", "--submodule=short", "--ignore-submodules", "--no-notes", "--notes", "--show-notes",
    "--show-signature", "-G", "-S", "--pickaxe-all", "--follow", "-R", "-O", "--rotate-to=x", "--skip-to=x",
    "--no-index", "--check", "--ws-error-highlight=all", "--irreversible-delete", "--stat-width=1",
    "--anchored=x", "--patience", "--histogram", "--minimal", "--dirstat", "--compact-summary", "-z",
    "-g", "--reflog", "--walk-reflogs", "--branches", "--tags", "--remotes", "--glob=x", "--exclude=x",
    "--not", "--stdin", "--cherry-pick", "--left-only", "--ancestry-path", "--source", "--full-history",
    "--sparse", "-L1,2:src/app.py", "--objects", "--no-walk", "--children", "--parents", "--encoding=x",
    "--no-replace-objects",
)

#: Values past their bound, and operands that name an object, a search, a
#: reflog entry or a place outside the workspace; the other words of the
#: verbs that only list.
_PAST_A_BOUND = (
    "log --format=%N", "log --pretty=%GG", "log --format=%(describe)", "log --pretty=myalias",
    "log --format=%gs", "show --format=%G?", "show --pretty=tformat:%N", "stash list --format=%N",
    "branch --format=%(raw)", "branch --format=%(contents)", "tag --format=%(raw)",
    "tag --format=%(signature)", "branch --sort=signature", "shortlog --group=format:%N",
    "status --porcelain=v3", "log -n x", "log -p --all", "log -p --graph", "log --patch --all",
    "show HEAD:src/app.py", "show :src/app.py", "show :0:src/app.py", "show :/first", "log -p HEAD:src",
    "diff HEAD:src", "diff HEAD~1:src HEAD:src", "blame HEAD -- src/app.py", "blame src/app.py src/app.py",
    "show HEAD@{1}", "log -p main@{1}", "diff -- :(exclude)src", "log -p -- :/src",
    "diff -- //host/share/x", "show HEAD -- ../outside.txt", "stash show", "stash show -p",
    "stash show --stat", "stash", "stash push", "branch made", "tag made", "branch -d side",
    "tag -d v1", "tag -n", "remote -v", "remote get-url origin",
)

_GLOBAL = ("-c core.pager=cat log -p", "--git-dir=.git log -p", "--work-tree=. diff", "-C . log -p",
           "--exec-path=. log", "--no-replace-objects log", "--literal-pathspecs log", "-p log",
           "--namespace=x log", "--bare log")


def _outside_commands() -> list[str]:
    from kazma_core.ide.git_policy import CARD_LESS

    verbs = {key.split(" ")[0] for key in CARD_LESS}
    commands = [verb for verb in _git_verbs() if verb not in verbs]
    for key, form in sorted(CARD_LESS.items()):
        verb = key.split(" ")[0]
        listed = _help_options(verb) | (set(_SHAPING) if verb in ("diff", "show", "log") else set())
        allowed = form.flags | form.values
        operand = _OPERANDS.get(key, "")
        commands += [f"{key} {option} {operand}".strip() for option in sorted(listed)
                     if option.split("=", 1)[0] not in allowed and option not in allowed]
    return [*commands, *_PAST_A_BOUND, *_GLOBAL]


def ran_without_a_card(commands: list[str], carded: list[str]) -> list[str]:
    """The commands of *commands* the IDE ran with no card (one refused as
    naming a Kazma file ran nothing either)."""
    ran = []
    for command in commands:
        carded.clear()
        answer = _ide(command)
        if carded != [f"git {command}"] and "Kazma's own" not in _text(answer):
            ran.append(command)
    return ran


def test_everything_outside_the_list_goes_to_the_card(user_repo: Path, carded: list[str]) -> None:
    commands = _outside_commands()
    assert len(commands) > 300
    assert {"cat-file", "archive", "grep", "format-patch", "bundle", "notes", "reflog", "range-diff"} <= set(commands)
    assert ran_without_a_card(commands, carded) == []


def test_control_a_planted_extra_form_runs_with_no_card(
    ws: Path, carded: list[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kazma_core.ide import git_policy

    _init(ws)
    _commit(ws)
    planted = git_policy._Form(frozenset({"-p"}), frozenset(), "names")
    monkeypatch.setitem(git_policy.CARD_LESS, "cat-file", planted)
    assert ran_without_a_card(["cat-file -p HEAD:src/app.py"], carded) == ["cat-file -p HEAD:src/app.py"]
    blob = _git(ws, "rev-parse", "HEAD:.env").strip()
    assert _leaked(_ide(f"cat-file -p {blob}")), "the planted form prints the secret"


def test_the_web_route_answers_a_read_it_admitted_with_needs_approval(ws: Path, carded: list[str]) -> None:
    from kazma_ui.i18n import TRANSLATIONS

    _init(ws)
    _commit(ws)
    tree = _git(ws, "rev-parse", "HEAD:conf").strip()
    answer = _ide(f"show {tree}", admitted_as_read=True)
    assert answer["needs_approval"] is True and answer["command"] == f"git show {tree}", answer
    assert answer["error"] == TRANSLATIONS["ide.git_needs_approval"]["en"]
    assert carded == [] and not _leaked(answer)
    # Not admitted as a read (chat, TUI): the same read goes to the card.
    _ide(f"show {tree}")
    assert carded == [f"git show {tree}"]


def test_the_page_sends_a_needs_approval_read_to_the_card() -> None:
    source = (Path(__file__).resolve().parents[1] / "kazma-ui/kazma_ui/static/js/ide.js").read_text(encoding="utf-8")
    body = source[source.index("async gitCmd(sub)"):]
    body = body[: body.index("finally")]
    assert "needs_approval" in body and "'/api/ide/run'" in body and "data.command" in body


def test_the_agent_answers_a_read_outside_the_list_with_its_command(ws: Path) -> None:
    _init(ws)
    _commit(ws)
    tree = _git(ws, "rev-parse", "HEAD:conf").strip()
    said = _agent(target=tree)
    assert said.startswith("Error:") and "shell_exec" in said and "git show" in said and tree in said
    assert SECRET not in said


# ── The ways a Kazma file gets into what git can print ──────────────────
#
# Each setup returns the IDE reads and the agent's git_diff arguments that
# reach the file.


def _committed(root: Path) -> dict[str, Any]:
    _init(root)
    _commit(root)
    (root / ".env").write_text(f"KAZMA_VAULT_KEY={SECRET}_fresh\n", encoding="utf-8")
    return {"ide": ["show", "show HEAD", "diff", "diff HEAD", "log -p", "log --patch", "log -p --all",
                    "show --binary HEAD", "log -p -- .", "log -p -- kazma-data", "log -p -- [.]env",
                    "log -p -- :!src", "log -p -- :^src", "diff -- [.]env", "diff . src", "log -p --binary"],
            "agent": [{}, {"path": ".env"}, {"target": "HEAD"}, {"path": "kazma-data"}]}


def _trees(root: Path) -> dict[str, Any]:
    """A tree, a tree's path and a tree's file: git prints paths from that
    tree, not from the top (D4-M1)."""
    _init(root)
    _commit(root)
    conf = _git(root, "rev-parse", "HEAD:conf").strip()
    data = _git(root, "rev-parse", "HEAD:kazma-data").strip()
    return {"ide": [f"diff {EMPTY_TREE} HEAD:conf", "diff HEAD:conf", f"diff HEAD:src {conf}",
                    f"show {conf}:kazma_env.py", f"diff --binary {EMPTY_TREE} HEAD:kazma-data",
                    "diff --binary HEAD:kazma-data", f"show {data}:memo.bin", f"show {conf}",
                    f"diff {EMPTY_TREE} {conf}", f"diff --binary {EMPTY_TREE} {data}", f"log -p {conf}",
                    "show HEAD^{tree}", f"diff {EMPTY_TREE} HEAD^{{tree}}", f"diff {conf} {data}"],
            "agent": [{"target": f"{EMPTY_TREE}..{conf}"}, {"target": conf}, {"target": data},
                      {"target": f"{EMPTY_TREE}..{data}"}]}


def _removed_from_the_index(root: Path) -> dict[str, Any]:
    _init(root)
    _commit(root)
    _git(root, "rm", "-q", "--cached", "--", ".env", "kazma-data/backups/1/.env",
         "conf/kazma_env.py", "kazma-data/vault.db", "kazma-data/memo.bin")
    _commit(root, "src", message="took them out")
    return {"ide": ["log -p", "show HEAD~1", "diff HEAD~1", "log -p --all", "diff HEAD~1 HEAD",
                    "log -p --binary"],
            "agent": [{"target": "HEAD~1"}, {"target": "HEAD~1..HEAD"}]}


def _gone_from_the_disk(root: Path) -> dict[str, Any]:
    """A store only its header makes Kazma's, committed, then gone (D4-L1)."""
    _init(root)
    _commit(root)
    (root / "kazma-data" / "memo.bin").unlink()
    return {"ide": ["show HEAD:kazma-data/memo.bin", "show --binary HEAD:kazma-data/memo.bin",
                    "blame HEAD -- kazma-data/memo.bin", "show --binary HEAD", "log -p --binary",
                    "diff --binary"],
            "agent": [{"target": "HEAD"}, {}]}


def _undone(root: Path) -> dict[str, Any]:
    _init(root)
    _commit(root, "src", ".env.example")
    undone = _commit(root, ".env", message="oops")
    _git(root, "reset", "-q", "--hard", "HEAD~1")
    return {"ide": ["show ORIG_HEAD", "diff ORIG_HEAD", "log -p ORIG_HEAD", f"show {undone}",
                    f"diff HEAD {undone}", f"log -p {undone}", f"show {undone[:7]}"],
            "agent": [{"target": "ORIG_HEAD"}, {"target": undone}, {"target": undone[:7]}]}


def _amended(root: Path) -> dict[str, Any]:
    _init(root)
    _commit(root, "src")
    before = _commit(root, ".env", message="with the key")
    _git(root, "rm", "-q", "--cached", "--", ".env")
    _git(root, "-c", "commit.gpgsign=false", "commit", "-q", "--amend", "--allow-empty", "-m", "without")
    return {"ide": [f"show {before}", f"diff HEAD {before}", f"log -p {before}"],
            "agent": [{"target": before}]}


def _stashed_untracked(root: Path) -> dict[str, Any]:
    _init(root)
    _commit(root, "src")
    (root / ".env.local").write_text(f"KAZMA_VAULT_KEY={SECRET}_local\n", encoding="utf-8")
    _git(root, "stash", "-q", "-u")
    return {"ide": ["show stash^3", "diff stash^3", "log -p stash^3", "log -p --all", "stash show -p",
                    "show stash", "stash show --include-untracked -p"],
            "agent": [{"target": "stash^3"}, {"target": "stash"}]}


def _stashed_staged(root: Path) -> dict[str, Any]:
    _init(root)
    _commit(root, "src")
    _git(root, "add", "-f", "--", ".env", "kazma-data/memo.bin")
    _git(root, "stash", "-q")
    (root / "src" / "app.py").write_text("changed\n", encoding="utf-8")
    _git(root, "stash", "-q")
    return {"ide": ["log -p --all", "show stash", "show stash^2", "stash show -p", "stash list",
                    "stash list -p", "stash show --binary", "stash show -p --binary", "show --binary stash@{1}",
                    "diff --binary stash^1 stash", "show stash@{1}^2"],
            "agent": [{"target": "stash^2"}, {"target": "stash^1..stash"}]}


def _loose_blob(root: Path) -> dict[str, Any]:
    _init(root)
    _commit(root, "src")
    _git(root, "add", "-f", "--", ".env")
    blob = _git(root, "rev-parse", ":.env").strip()
    _git(root, "reset", "-q")
    return {"ide": [f"show {blob}", f"show {blob[:4]}", f"show {blob[:7]}", f"diff {blob} {blob}"],
            "agent": [{"target": blob}, {"target": blob[:7]}]}


def _blob_tag(root: Path) -> dict[str, Any]:
    _init(root)
    _commit(root, "src")
    blob = _git(root, "hash-object", "-w", "--", ".env").strip()
    _git(root, "tag", "blobtag", blob)
    _git(root, "tag", "-a", "-m", "annotated", "annotatedblob", blob)
    return {"ide": ["show blobtag", "show annotatedblob", "diff blobtag annotatedblob", "log -p --all",
                    "show blobtag^{}"],
            "agent": [{"target": "blobtag"}, {"target": "annotatedblob"}]}


def _notes(root: Path) -> dict[str, Any]:
    _init(root)
    head = _commit(root, "src")
    _git(root, "notes", "add", "-F", str(root / ".env"), "HEAD")
    notes = _git(root, "rev-parse", "refs/notes/commits").strip()
    tree = _git(root, "rev-parse", "refs/notes/commits^{tree}").strip()
    return {"ide": ["show HEAD", "show", "log -1", "log -p", "log --format=%N", "show --stat HEAD",
                    "log -p --all", "show refs/notes/commits", "log -p notes/commits", f"show {notes}",
                    f"diff {notes}~0 HEAD", f"show refs/notes/commits:{head}", f"diff {EMPTY_TREE} {tree}",
                    "diff 4b825dc refs/notes/commits^{tree}", f"diff {tree} HEAD"],
            "agent": [{"target": "HEAD"}, {}, {"target": "notes/commits"}, {"target": notes},
                      {"target": f"{EMPTY_TREE}..{tree}"}]}


def _notes_on_a_branch(root: Path) -> dict[str, Any]:
    """The notes' history put on a branch: reachable, a commit, its patches
    still notes filed under ids."""
    _notes(root)
    _git(root, "branch", "noted", "refs/notes/commits")
    return {"ide": ["log -p noted", "show noted", "diff noted~0 HEAD"],
            "agent": [{"target": "noted"}, {"target": "HEAD..noted"}]}


def _replaced(root: Path) -> dict[str, Any]:
    """A replace ref: .env's blob printed as src/app.py (D4-L2)."""
    _init(root)
    _commit(root, "src")
    app = _git(root, "rev-parse", "HEAD:src/app.py").strip()
    env = _git(root, "hash-object", "-w", "--", ".env").strip()
    _git(root, "replace", app, env)
    return {"ide": ["show HEAD", "diff", "log -p", "blame src/app.py", "show HEAD -- src/app.py",
                    "log -p -- src/app.py"],
            "agent": [{}, {"path": "src/app.py"}, {"target": "HEAD"}]}


def _submodule(root: Path) -> dict[str, Any]:
    inner = root / "vendor"
    inner.mkdir()
    _init(inner)
    (inner / "restic.pass").write_text(f"{SECRET}\n", encoding="utf-8")
    _commit(inner, message="inner")
    _init(root)
    _commit(root, "src", "vendor", message="with a submodule")
    (inner / "restic.pass").write_text(f"{SECRET}_two\n", encoding="utf-8")
    _commit(inner, message="inner two")
    _git(root, "config", "diff.submodule", "diff")
    return {"ide": ["diff", "diff HEAD", "log -p", "show"], "agent": [{}]}


def _shaped_by_config(root: Path) -> dict[str, Any]:
    _committed(root)
    for key, value in (("diff.noprefix", "true"), ("diff.mnemonicPrefix", "true"),
                       ("color.ui", "always"), ("diff.relative", "true"), ("log.showRoot", "true")):
        _git(root, "config", key, value)
    return {"ide": ["show HEAD", "diff", "log -p", "diff HEAD"], "agent": [{}, {"target": "HEAD"}]}


SETUPS: dict[str, Callable[[Path], dict[str, Any]]] = {
    "committed, then a fresh key in the work tree": _committed,
    "a tree, a tree's path, a tree's file": _trees,
    "taken out of the index, kept in history": _removed_from_the_index,
    "a store only its header makes Kazma's, gone from the disk": _gone_from_the_disk,
    "committed and undone (ORIG_HEAD, the commit's id)": _undone,
    "committed and amended away (the old id)": _amended,
    "stashed untracked (the stash's third parent)": _stashed_untracked,
    "staged and stashed, another stash on top": _stashed_staged,
    "a loose blob, by its id": _loose_blob,
    "a tag on a blob": _blob_tag,
    "a note made from the file": _notes,
    "the notes' history on a branch": _notes_on_a_branch,
    "a replace ref": _replaced,
    "a submodule shown as a diff": _submodule,
    "prefixes, colour and relative paths from the config": _shaped_by_config,
}


@pytest.mark.parametrize("setup", sorted(SETUPS))
def test_no_ide_git_read_prints_a_kazma_file(setup: str, ws: Path) -> None:
    reads = SETUPS[setup](ws)
    for command in reads["ide"]:
        answer = _ide(command)
        assert not _leaked(answer), (setup, command, answer)


@pytest.mark.parametrize("setup", sorted(SETUPS))
def test_no_agent_git_read_prints_a_kazma_file(setup: str, ws: Path) -> None:
    reads = SETUPS[setup](ws)
    for kwargs in reads["agent"]:
        said = _agent(**kwargs)
        assert not _leaked(said), (setup, kwargs, said)


def _old_rule(patch: pytest.MonkeyPatch) -> None:
    """The control's doors: any read runs as asked -- ``rev:path``, a reflog
    entry, ``stash show`` and patches of the stash's list among them -- with
    no global of the read's own and its output as printed."""
    from kazma_core.ide import git_policy
    from kazma_core.safety import git_kazma_files, git_read

    patch.setattr(git_policy, "_names_an_object", lambda _operand: False)
    patch.setattr(git_policy, "_NOT_WITH_A_PATCH", frozenset())
    patch.setattr(git_policy, "_REFLOG_SELECTOR", re.compile(r"(?!)"))
    patch.setattr(git_read, "READ_GLOBALS", ())
    listing = git_policy.CARD_LESS["stash list"]
    patch.setitem(git_policy.CARD_LESS, "stash list",
                  git_policy._Form(listing.flags | git_policy.PATCH_OPTIONS, listing.values, "none"))
    shown = git_policy._Form(git_policy.CARD_LESS["show"].flags | {"--include-untracked"}, frozenset(), "none")
    patch.setitem(git_policy.CARD_LESS, "stash show", shown)
    real_key = git_policy._form_key
    patch.setattr(git_policy, "_form_key",
                  lambda parts: "stash show" if parts[:2] == ["stash", "show"] else real_key(parts))

    def as_asked(_root: Path, _ask: Any, form: Any) -> Any:
        tail = ["--", *form.after] if form.after is not None else []
        return git_kazma_files._Plan([*form.head, *form.options, *form.before, *tail]), ""

    patch.setattr(git_kazma_files, "_plan", as_asked)


@pytest.mark.parametrize("setup", sorted(SETUPS))
def test_control_with_the_old_rule_each_setup_prints_the_secret(
    setup: str, ws: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    reads = SETUPS[setup](ws)
    _old_rule(monkeypatch)
    outputs = [_ide(c) for c in reads["ide"]] + [_agent(**k) for k in reads["agent"]]
    assert any(_leaked(out) for out in outputs), (setup, outputs)


# ── Each piece of the rule, on its own ──────────────────────────────────


def _no_exclusions(patch: pytest.MonkeyPatch) -> None:
    from kazma_core import store_registry

    patch.setattr(store_registry, "kazma_exclude_pathspecs", lambda _top: [])
    patch.setattr(store_registry, "kazma_excluded_among", lambda _top, _paths: set())


def _no_filter(patch: pytest.MonkeyPatch) -> None:
    from kazma_core.safety import git_kazma_files

    patch.setattr(git_kazma_files, "_drop_kazma_sections", lambda _tops, text: (text, 0))


def test_the_exclusions_alone_keep_the_sections_out(ws: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _committed(ws)
    _no_filter(monkeypatch)
    for command in ("show HEAD", "diff", "log -p", "show --binary HEAD"):
        assert not _leaked(_ide(command)), command


def test_the_filter_alone_drops_the_sections(ws: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _committed(ws)
    _no_exclusions(monkeypatch)
    for command in ("show HEAD", "diff", "log -p", "show --binary HEAD"):
        assert not _leaked(_ide(command)), command
    assert "Kazma's own files" in _ide("show HEAD")["output"], "the reader is told some is not shown"


def test_control_without_either_the_sections_come_back(ws: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _committed(ws)
    _no_exclusions(monkeypatch)
    _no_filter(monkeypatch)
    assert _leaked(_ide("show HEAD"))
    assert _leaked(_ide("show --binary HEAD"))


def test_control_without_the_commit_rule_a_tree_prints_the_env_file(
    ws: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kazma_core.safety import git_kazma_files

    _trees(ws)
    conf = _git(ws, "rev-parse", "HEAD:conf").strip()
    assert not _leaked(_ide(f"diff {EMPTY_TREE} {conf}"))
    monkeypatch.setattr(git_kazma_files, "_commits", lambda _ask, _revisions: ([], [], None))
    assert _leaked(_ide(f"diff {EMPTY_TREE} {conf}"))


def test_control_without_the_reachability_rule_another_repository_is_read(
    ws: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kazma_core.safety import git_kazma_files

    other = _another_repository(tmp_path)
    commit = _git(other, "rev-parse", "HEAD").strip()
    _init(ws)
    _commit(ws, "src")
    alternates = ws / ".git" / "objects" / "info" / "alternates"
    # Bytes: write_text ends the line with \r\n on Windows, and git then
    # finds no object directory there.
    alternates.write_bytes(f"{(other / '.git' / 'objects').as_posix()}\n".encode())
    assert _git(ws, "cat-file", "-t", commit).strip() == "commit", "the instrument: the alternate is read"
    assert OTHER not in _text(_ide(f"show {commit}"))
    monkeypatch.setattr(git_kazma_files, "_held_by_refs", lambda _ask, _ids, _holders: True)
    assert OTHER in _text(_ide(f"show {commit}"))


def test_control_without_the_notes_rule_a_branch_of_notes_prints_the_note(
    ws: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kazma_core.safety import git_kazma_files

    _notes_on_a_branch(ws)
    assert not _leaked(_ide("log -p noted"))
    monkeypatch.setattr(git_kazma_files, "_shares_notes_history", lambda _ask, _ids: False)
    assert _leaked(_ide("log -p noted"))


def test_control_without_the_patch_rule_log_all_walks_the_notes(
    ws: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kazma_core.ide import git_policy

    _notes(ws)
    assert not _leaked(_ide("log -p --all"))
    monkeypatch.setattr(git_policy, "_NOT_WITH_A_PATCH", frozenset())
    assert _leaked(_ide("log -p --all"))


def test_control_without_the_object_operand_rule_a_trees_path_is_read(
    ws: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``rev:path`` goes to the card on its words, and a tree's path again
    by the commit rule: each alone holds, without both it is printed."""
    from kazma_core.ide import git_policy
    from kazma_core.safety import git_kazma_files

    _trees(ws)
    monkeypatch.setattr(git_policy, "_names_an_object", lambda _operand: False)
    assert not _leaked(_ide(f"diff {EMPTY_TREE} HEAD:conf"))
    monkeypatch.setattr(git_kazma_files, "_commits", lambda _ask, _revisions: ([], [], None))
    assert _leaked(_ide(f"diff {EMPTY_TREE} HEAD:conf"))


def test_control_without_the_blame_rule_a_linked_worktree_prints_the_installs_env(
    ws: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kazma_core.safety import git_kazma_files

    linked = _linked_worktree(ws, tmp_path)
    assert (linked / ".env").is_file()
    assert not _leaked(_ide("blame .env"))

    def unchecked(_root: Path, tops: Any, form: Any) -> Any:
        return git_kazma_files._Plan(["blame", *form.options, "--", *form.before, *(form.after or [])], tops), ""

    monkeypatch.setattr(git_kazma_files, "_blame_plan", unchecked)
    assert _leaked(_ide("blame .env"))


# ── Each force flag, dropped ─────────────────────────────────────────────


def _drop_shape(flag: str) -> Callable[[pytest.MonkeyPatch], None]:
    def drop(patch: pytest.MonkeyPatch) -> None:
        from kazma_core.safety import git_kazma_files

        patch.setattr(git_kazma_files, "SECTION_SHAPE",
                      tuple(f for f in git_kazma_files.SECTION_SHAPE if f != flag))
    return drop


def _drop_globals(patch: pytest.MonkeyPatch) -> None:
    from kazma_core.safety import git_read

    patch.setattr(git_read, "READ_GLOBALS", ())


def _drop_notes(patch: pytest.MonkeyPatch) -> None:
    from kazma_core.safety import git_kazma_files

    patch.setattr(git_kazma_files, "NO_NOTES", ())


#: flag -> (how it is dropped, the setup, the read, whether the section
#: filter is off too: a flag that is a second layer behind the filter).
_FORCED: dict[str, tuple[Callable[[pytest.MonkeyPatch], None], Callable[[Path], Any], str, bool]] = {
    "--no-replace-objects": (_drop_globals, _replaced, "show HEAD", False),
    "--no-notes": (_drop_notes, _notes, "show HEAD", False),
    "--submodule=short": (_drop_shape("--submodule=short"), _submodule, "diff", True),
}


def test_every_force_flag_has_a_control() -> None:
    from kazma_core.safety import git_kazma_files, git_read

    forced = {*git_read.READ_GLOBALS, *git_kazma_files.NO_NOTES, *git_kazma_files.SECTION_SHAPE}
    with_a_program = {"--no-ext-diff", "--no-textconv"}
    through_a_junction = {"--default-prefix", "--no-color"}
    # Their controls start the promisor's transport and a submodule's own
    # filter: tests/test_git_settings_start_nothing_and_quote_no_file.py.
    in_the_settings_gate = {"--no-lazy-fetch", "--ignore-submodules=dirty"}
    # --no-relative: the IDE reads at the top (--work-tree=<root>, the
    # ceiling at its parent), where diff.relative changes nothing.
    assert forced == (set(_FORCED) | with_a_program | through_a_junction | in_the_settings_gate
                      | {"--no-relative"})


@pytest.mark.parametrize("flag", sorted(_FORCED))
def test_control_a_planted_dropped_force_flag_prints_the_secret(
    flag: str, ws: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    drop, setup, command, filter_off = _FORCED[flag]
    setup(ws)
    if filter_off:
        _no_filter(monkeypatch)
    assert not _leaked(_ide(command)), "with the flag"
    drop(monkeypatch)
    assert _leaked(_ide(command)), f"without {flag}"


@pytest.mark.parametrize("flag,config", [("--no-ext-diff", "diff.external"),
                                         ("--no-textconv", "diff.audit.textconv")])
def test_control_without_a_program_flag_the_repositorys_program_runs(
    flag: str, config: str, ws: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sys

    _committed(ws)
    (ws / ".gitattributes").write_text("*.py diff=audit\n", encoding="utf-8")
    _commit(ws, ".gitattributes")
    (ws / "src" / "app.py").write_text("changed\n", encoding="utf-8")
    marker = tmp_path / "program-ran.txt"
    program = tmp_path / "program.py"
    program.write_text(f"from pathlib import Path\nPath({str(marker)!r}).write_text('ran')\nprint('x')\n",
                       encoding="utf-8")
    _git(ws, "config", config, f'"{sys.executable}" "{program}"')
    _ide("diff")
    assert not marker.exists()
    _drop_shape(flag)(monkeypatch)
    _ide("diff")
    assert marker.exists(), f"without {flag} the repository's program runs"


def _junction(target: Path, at: Path) -> None:
    import _winapi

    _winapi.CreateJunction(str(target), str(at))


@pytest.mark.skipif(os.name != "nt", reason="git tracks files through a junction on Windows")
@pytest.mark.parametrize("flag,config,value", [("--default-prefix", "diff.noprefix", "true"),
                                               ("--no-color", "color.ui", "always")])
def test_control_without_a_shape_flag_a_junction_section_is_kept(
    flag: str, config: str, value: str, ws: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A store reached through a junction: only the filter drops it, and the
    filter knows a section by its ``a/`` and ``b/`` header."""
    _junction(ws / "kazma-data", ws / "lnk")
    _init(ws)
    _commit(ws, "src", "lnk")
    _git(ws, "config", config, value)
    assert not _leaked(_ide("show --binary HEAD"))
    _drop_shape(flag)(monkeypatch)
    assert _leaked(_ide("show --binary HEAD")), f"without {flag}"


# ── Only its contents make it Kazma's: a junction, a short name ─────────


@pytest.mark.skipif(os.name != "nt", reason="git tracks files through a junction on Windows")
def test_a_section_only_its_contents_make_kazmas_is_dropped_after_the_run(
    ws: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _junction(ws / "kazma-data", ws / "lnk")
    _init(ws)
    _commit(ws, "src", "lnk")
    for command in ("show --binary HEAD", "log -p --binary", "show HEAD"):
        answer = _ide(command)
        assert not _leaked(answer), (command, answer)
        assert "Kazma's own files" in answer["output"], command
    assert not _leaked(_ide("blame lnk/memo.bin"))
    _no_filter(monkeypatch)
    assert _leaked(_ide("show --binary HEAD")), "the control: the filter is what drops it"


def _short_name(path: Path) -> str | None:
    import ctypes

    buffer = ctypes.create_unicode_buffer(1024)
    if not ctypes.windll.kernel32.GetShortPathNameW(str(path), buffer, 1024):
        return None
    short = Path(buffer.value).name
    return short if "~" in short else None


@pytest.mark.skipif(os.name != "nt", reason="Windows short names")
def test_a_path_through_a_short_name_is_judged_as_the_folder_it_names(
    ws: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D4-L4: ``KAZMA-~1/memo.bin`` committed (with ``core.protectNTFS``
    off) is the data dir's ``memo.bin``."""
    short = _short_name(ws / "kazma-data")
    if short is None:
        pytest.skip("this volume keeps no short names")
    _init(ws)
    _commit(ws, "src")
    blob = _git(ws, "hash-object", "-w", "--", "kazma-data/memo.bin").strip()
    _git(ws, "-c", "core.protectNTFS=false", "update-index", "--add", "--cacheinfo",
         f"100644,{blob},{short}/memo.bin")
    _git(ws, "-c", "core.protectNTFS=false", "commit", "-q", "-m", "short")
    for command in ("show --binary HEAD", "log -p --binary"):
        assert not _leaked(_ide(command)), command
    _no_filter(monkeypatch)
    assert _leaked(_ide("show --binary HEAD")), "the control: the filter is what drops it"


# ── Where git looks: its variables, alternates, a worktree ──────────────


def _another_repository(tmp_path: Path) -> Path:
    other = tmp_path / "other"
    other.mkdir()
    _init(other)
    (other / "notes.txt").write_text(f"{OTHER}\n", encoding="utf-8")
    (other / ".env").write_text(f"KAZMA_VAULT_KEY={SECRET}_other\n", encoding="utf-8")
    _commit(other)
    return other


def test_gits_own_variables_never_point_a_read_elsewhere(
    ws: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    other = _another_repository(tmp_path)
    _init(ws)
    _commit(ws, "src")
    for key, value in (("GIT_DIR", other / ".git"), ("GIT_WORK_TREE", other),
                       ("GIT_INDEX_FILE", other / ".git" / "index"),
                       ("GIT_OBJECT_DIRECTORY", other / ".git" / "objects"),
                       ("GIT_ALTERNATE_OBJECT_DIRECTORIES", other / ".git" / "objects"),
                       ("GIT_REPLACE_REF_BASE", "refs/x/"),
                       ("GIT_CONFIG_PARAMETERS", "'diff.noprefix'='true'")):
        monkeypatch.setenv(key, str(value))
    shown = _ide("show HEAD")
    assert "greet_all" in _text(shown) and OTHER not in _text(shown), shown
    said = _agent(target="HEAD")
    assert "greet_all" in said and OTHER not in said, said


def test_objects_from_an_alternate_go_to_the_card(ws: Path, tmp_path: Path, carded: list[str]) -> None:
    other = _another_repository(tmp_path)
    commit = _git(other, "rev-parse", "HEAD").strip()
    blob = _git(other, "rev-parse", "HEAD:.env").strip()
    _init(ws)
    _commit(ws, "src")
    alternates = ws / ".git" / "objects" / "info" / "alternates"
    alternates.write_bytes(f"{(other / '.git' / 'objects').as_posix()}\n".encode())
    assert _git(ws, "cat-file", "-t", commit).strip() == "commit", "the instrument: the alternate is read"
    for command in (f"show {commit}", f"log -p {commit}", f"show {blob}", f"diff HEAD {commit}"):
        answer = _ide(command)
        assert not _leaked(answer) and OTHER not in _text(answer), command
    assert len(carded) == 4, carded


def _linked_worktree(ws: Path, tmp_path: Path) -> Path:
    from kazma_core.ide.service import reset_ide_service
    from kazma_core.workspace.binding import configure_workspace

    _committed(ws)
    linked = tmp_path / "linked"
    _git(ws, "worktree", "add", "-q", str(linked), "HEAD")
    configure_workspace(workspace=str(linked))
    reset_ide_service()
    return linked


def test_a_linked_worktree_is_read_by_the_same_rule(ws: Path, tmp_path: Path) -> None:
    _linked_worktree(ws, tmp_path)
    for command in ("show HEAD", "log -p", "diff HEAD~0", "blame .env", "show HEAD:.env",
                    "blame conf/kazma_env.py", "blame HEAD -- kazma-data/memo.bin", "show --binary HEAD"):
        assert not _leaked(_ide(command)), command


# ── A read names one of Kazma's files, or a folder git turns down ───────


def test_a_read_that_names_a_kazma_file_is_refused_before_anything_runs(
    ws: Path, carded: list[str],
) -> None:
    _committed(ws)
    for command in ("show HEAD:.env", "blame .env", "log -p -- conf/kazma_env.py",
                    "cat-file -p HEAD:kazma-data/vault.db", "diff -- .env"):
        answer = _ide(command)
        assert answer["ok"] is False and "Kazma" in answer["error"] and not _leaked(answer), (command, answer)
    assert carded == []


def _broken_head(root: Path, patch: pytest.MonkeyPatch) -> None:
    (root / ".git" / "objects").mkdir(parents=True)
    (root / ".git" / "refs").mkdir()
    (root / ".git" / "HEAD").write_text("garbage\n", encoding="utf-8")


def _unknown_extension(root: Path, patch: pytest.MonkeyPatch) -> None:
    _git(root, "init", "-q")
    _git(root, "config", "core.repositoryformatversion", "1")
    _git(root, "config", "extensions.madeupext", "true")


def _another_owner(root: Path, patch: pytest.MonkeyPatch) -> None:
    """Git's own switch for an untrusted owner, with no ``safe.directory``
    from any configuration; put back on the way to the process, as the
    doors drop git's variables."""
    _init(root)
    _commit(root, "src")
    empty = root.parent / "no-config"
    empty.write_text("", encoding="utf-8")
    from kazma_core.safety import git_read

    real = git_read.run_git  # every git process of a card-less read

    def run(argv: Any, *, env: dict[str, str], **kwargs: Any) -> Any:
        env = {**env, "GIT_TEST_ASSUME_DIFFERENT_OWNER": "1", "GIT_CONFIG_GLOBAL": str(empty),
               "GIT_CONFIG_NOSYSTEM": "1"}
        return real(argv, env=env, **kwargs)

    patch.setattr(git_read, "run_git", run)


_TURNED_DOWN = {"a broken HEAD": _broken_head, "an unknown extension": _unknown_extension,
                "another owner": _another_owner, "no repository": lambda _root, _patch: None}


@pytest.mark.parametrize("shape", sorted(_TURNED_DOWN))
def test_a_content_read_runs_only_in_a_work_tree_git_itself_accepts(
    shape: str, ws: Path, monkeypatch: pytest.MonkeyPatch, carded: list[str],
) -> None:
    _TURNED_DOWN[shape](ws, monkeypatch)
    for command in ("diff kazma-data src", "diff . src", "show HEAD", "blame src/app.py"):
        answer = _ide(command)
        assert not _leaked(answer), (command, answer)
    assert "git diff kazma-data src" in carded and "git diff . src" in carded, carded


# ── The exclusions agree with git; the screen through a link ────────────


def test_the_exclusion_matcher_agrees_with_git(ws: Path) -> None:
    from kazma_core.store_registry import SECRET_FILE_NAMES, kazma_exclude_pathspecs, kazma_excluded_among

    paths = [
        ".env", "a/.env", "b/.ENV", ".env.local", "c/.Env.Production", "d/x.db", "e/x.DB-wal", "f/x.db.bak",
        "g/x.sqlite3", "h/x.db_old", "i/x.db~", "kazma-data/memo.bin", "kazma-data/deep/x.txt",
        "kazma-datax/y.txt", "conf/kazma_env.py", "conf/kazma_env.pyc", "conf/other.py", "src/app.py",
        "j/vault.db", "k/hitl_gates.db-shm", "envy/.envrc", "l/x.dbx", "m/a.env",
        *(f"n{i}/{name}" for i, name in enumerate(sorted(SECRET_FILE_NAMES))),
    ]
    for rel in paths:
        (ws / rel).parent.mkdir(parents=True, exist_ok=True)
        (ws / rel).write_text("x\n", encoding="utf-8")
    _init(ws)
    _git(ws, "add", "-f", "--", *paths)
    kept = set(_git(ws, "ls-files", "--", ":(top)", *kazma_exclude_pathspecs(ws)).split())
    expected = set(paths) - kazma_excluded_among(ws, paths)
    assert kept == expected
    assert {"src/app.py", "conf/other.py", "kazma-datax/y.txt", "envy/.envrc", "l/x.dbx"} <= kept
    assert {".env", "b/.ENV", "kazma-data/deep/x.txt", "conf/kazma_env.py", "e/x.DB-wal"} & kept == set()


def _link(target: Path, at: Path) -> None:
    if os.name == "nt":
        _junction(target, at)
    else:
        at.symlink_to(target, target_is_directory=True)


def test_the_screen_answers_alike_for_a_root_named_through_a_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kazma_core.store_registry import is_kazma_store, kazma_paths_among

    real = tmp_path / "real"
    data = real / "kazma-data"
    data.mkdir(parents=True)
    (data / "memo.bin").write_bytes(b"SQLite format 3\x00x")
    (real / "conf").mkdir()
    (real / "conf" / "env.py").write_text("x\n", encoding="utf-8")
    monkeypatch.setenv("KAZMA_DATA_DIR", str(data))
    monkeypatch.setenv("KAZMA_ENV_FILE", str(real / "conf" / "env.py"))
    alias = tmp_path / "alias"
    _link(real, alias)
    paths = ["kazma-data/memo.bin", "conf/env.py", "src/app.py"]
    expected = sorted(p for p in paths if is_kazma_store(real / p))
    assert expected == ["conf/env.py", "kazma-data/memo.bin"]
    assert kazma_paths_among(real, paths) == expected
    assert kazma_paths_among(alias, paths) == expected


def test_the_screen_keeps_a_short_name_as_a_candidate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from kazma_core import store_registry

    asked: list[str] = []
    monkeypatch.setattr(store_registry, "is_kazma_store",
                        lambda path, **_k: asked.append(Path(path).name) is not None)
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path / "elsewhere"))
    monkeypatch.delenv("KAZMA_ENV_FILE", raising=False)
    store_registry.kazma_paths_among(tmp_path, ["KAZMA-~1/memo.bin", "src/notes.txt"])
    assert asked == ["memo.bin"]


# ── What a read leaves out, it says: the count, in the reader's language ─


def test_a_read_says_how_many_items_it_left_out(ws: Path) -> None:
    """D4-L3: a commit that touched only Kazma's files stays in the log, and
    a staged Kazma file is counted, not silently gone."""
    from kazma_ui.i18n import t_plural

    _init(ws)
    _commit(ws, "src", message="the code")
    only = _commit(ws, ".env", "conf/kazma_env.py", message="only kazma files")
    log = _ide("log -p")["output"]
    assert only in log and "only kazma files" in log and not _leaked(log)
    assert t_plural("ide.git_items_not_shown", 2, "en") in log
    show = _ide("show HEAD")["output"]
    assert only in show and t_plural("ide.git_items_not_shown", 2, "en") in show
    (ws / ".env").write_text(f"KAZMA_VAULT_KEY={SECRET}_staged\n", encoding="utf-8")
    _git(ws, "add", "-f", "--", ".env")
    staged = _ide("diff --cached")["output"]
    assert staged == t_plural("ide.git_items_not_shown", 1, "en"), staged
    assert "Kazma's own files" not in _ide("diff --cached -- src")["output"]
    assert "Kazma's own files" in _agent(target="HEAD")


def test_the_not_shown_line_is_in_the_readers_language(ws: Path) -> None:
    from kazma_ui import i18n

    _committed(ws)
    token = i18n.set_request_language("ar")
    try:
        output = _ide("show HEAD")["output"]
    finally:
        i18n._REQUEST_LANG.reset(token)
    assert NOT_SHOWN_AR in output and "Kazma's own files" not in output, output


def test_names_only_reads_run_in_a_repository_that_holds_kazma_files(ws: Path, carded: list[str]) -> None:
    _committed(ws)
    (ws / "src" / "app.py").write_text("def greet_all(items):\n    return list(items)\n", encoding="utf-8")
    for command in ("log --oneline", "log --stat", "show --stat HEAD", "diff --stat", "blame src/app.py",
                    "status --short", "branch --list", "ls-files", "rev-parse HEAD", "stash list",
                    "log --name-only --all", "rev-list --all --count", "describe --always", "shortlog -sn"):
        answer = _ide(command)
        assert answer["ok"] is True and not _leaked(answer), (command, answer)
    assert carded == []
    changed = _ide("diff")
    assert changed["ok"] is True and "list(items)" in changed["output"] and not _leaked(changed), changed
    assert "list(items)" in _agent()


# ── The sweep: every allowed option, where the secret lives ─────────────


def _everywhere(root: Path) -> None:
    """One repository that holds the secret every way the classes above do:
    committed, fresh in the work tree, untracked in a stash, in a note, on a
    tag, in a commit that was undone."""
    _init(root)
    _commit(root)
    _git(root, "notes", "add", "-F", str(root / ".env"), "HEAD")
    blob = _git(root, "rev-parse", "HEAD:.env").strip()
    _git(root, "tag", "blobtag", blob)
    (root / "restic.pass").write_text(f"{SECRET}_restic\n", encoding="utf-8")
    _commit(root, "restic.pass", message="oops")
    _git(root, "reset", "-q", "--hard", "HEAD~1")
    (root / ".env.local").write_text(f"KAZMA_VAULT_KEY={SECRET}_local\n", encoding="utf-8")
    (root / ".env").write_text(f"KAZMA_VAULT_KEY={SECRET}_stashed\n", encoding="utf-8")
    _git(root, "stash", "-q", "-u")
    (root / ".env").write_text(f"KAZMA_VAULT_KEY={SECRET}_fresh\n", encoding="utf-8")


#: The operands each verb's sweep reads with: the card-less path, where an
#: option could change what is printed, and the words an option could slip
#: past. Each object and ref the secret is in has its own setup above, and
#: the commit rule reads no option.
_SWEEP_FORMS = {
    "diff": ["diff {o}", "diff {o} HEAD", "diff {o} HEAD:conf"],
    "show": ["show {o}", "show {o} HEAD:conf"],
    "log": ["log {o} -p", "log {o} -p --all"],
    "stash list": ["stash list {o}"],
    "blame": ["blame {o} src/app.py", "blame {o} HEAD -- .env"],
}


def _sweep(key: str) -> list[str]:
    from kazma_core.ide.git_policy import CARD_LESS

    form = CARD_LESS[key]
    values = [f"{name}={_VALUE_EXAMPLES[name]}" if name.startswith("--") else f"{name} {_VALUE_EXAMPLES[name]}"
              for name in sorted(form.values)]
    forms = _SWEEP_FORMS.get(key, [f"{key} {{o}}"])
    return [shape.format(o=option) for option in [*sorted(form.flags), *values] for shape in forms]


@pytest.mark.parametrize("key", ("diff", "show", "log", "stash list", "blame", "status", "ls-files", "rev-parse",
                                 "describe", "rev-list", "shortlog", "branch", "tag"))
def test_the_sweep_prints_no_kazma_file(key: str, ws: Path) -> None:
    _everywhere(ws)
    leaked = [command for command in _sweep(key) if _leaked(_ide(command))]
    assert not leaked, leaked


@pytest.mark.parametrize("key", ("diff", "show", "log"))
def test_control_with_the_old_rule_the_sweep_prints_the_secret(
    key: str, ws: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _everywhere(ws)
    _old_rule(monkeypatch)
    assert any(_leaked(_ide(command)) for command in _sweep(key))


def test_the_agents_git_reads_go_through_the_same_list() -> None:
    """git_status, git_log and git_diff all run through ``_git_read``, which
    asks ``card_less_form`` and ``run_card_less``."""
    import ast

    from kazma_skills.native.git_github_manager import tools

    tree = ast.parse(Path(tools.__file__).read_text(encoding="utf-8"))
    readers = {node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
               and any(isinstance(c, ast.Call) and getattr(c.func, "id", "") == "_git_read" for c in ast.walk(node))}
    assert {"git_status", "git_log", "git_diff"} <= readers, readers
    reader = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "_git_read")
    # Called, or handed to a thread (``asyncio.to_thread(card_less_form, ...)``).
    named = {n.id for n in ast.walk(reader) if isinstance(n, ast.Name)}
    assert {"card_less_form", "run_card_less"} <= named, named
