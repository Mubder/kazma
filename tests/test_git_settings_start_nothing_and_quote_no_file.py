"""No repository setting makes a card-less git read start a program or print
part of a file (AGENTS.md section 10; ``safety/git_read.py``,
``safety/git_kazma_files.py``, ``ide/service.py``).

The IDE's git and the agent's git read tools run with no approval card in a
repository whose ``.git/config`` anyone who could write it chose. A setting
can name a program git starts (a signature program, a filter, a hook, a
transport) or a file git reads and then quotes (an attributes file, a
mailmap, a list of object ids). The reads switch each off
(``git_read.read_settings``, ``READ_GLOBALS``, ``BLAME_SHAPE``), and git's
own error text never reaches an answer: it can quote what a file holds.

What this file holds:

- git's own list of settings, as data (``tests/fixtures/git_config_keys.json``:
  the names ``git help --config`` prints, each that names a program or a file
  classified from git's documentation), and the installed git's list holds
  no such name the data does not classify;
- every program and file setting of that data planted at once, with the
  settings that make a read verify a signature, a signed commit of each
  format and a signed tag; every card-less form and the agent's tools run:
  no program starts, no read prints the file, nothing reaches the log, the
  file is untouched, and the user's work still shows;
- a submodule with settings of its own, a partial clone whose missing
  object a fetch would bring, settings that move what git reads;
- a control for each override: dropped, its setting starts its program or
  prints the file again; and a failed read answers a sentence, never git's
  text, with the old answer as control.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from tests import test_git_reads_print_no_kazma_file as _reads
from tests.test_git_reads_print_no_kazma_file import (
    SECRET,
    _commit,
    _git,
    _ide,
    _init,
    _leaked,
    card_less_commands,
)

#: The install whose workspace is the install folder (as on live), and the
#: IDE's approval card recorded (none runs), from the reads' own gate.
ws = _reads.ws
carded = _reads.carded

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="these reads need git")

DATA = json.loads((Path(__file__).parent / "fixtures" / "git_config_keys.json").read_text(encoding="utf-8"))
PY = Path(sys.executable).as_posix()
#: A Kazma file shaped so each way of reading it shows: a line of object ids
#: (blame's list), an attribute line with a space (an attribute git cannot
#: parse is quoted), a mailmap line for the test commits' address.
ENV_TEXT = (f"KAZMA_VAULT_KEY={SECRET}\n"
            f'KAZMA_SMTP_PASSWORD="pass {SECRET}!x"\n'
            f"KAZMA_SMTP_FROM={SECRET} <test@example.invalid>\n")
#: Every hook event git documents (githooks(5)).
HOOK_EVENTS = (
    "applypatch-msg", "pre-applypatch", "post-applypatch", "pre-commit", "pre-merge-commit",
    "prepare-commit-msg", "commit-msg", "post-commit", "pre-rebase", "post-checkout", "post-merge",
    "pre-push", "pre-receive", "update", "proc-receive", "post-receive", "post-update",
    "reference-transaction", "push-to-checkout", "pre-auto-gc", "post-rewrite", "sendemail-validate",
    "fsmonitor-watchman", "p4-changelist", "p4-prepare-changelist", "p4-post-changelist",
    "p4-pre-submit", "post-index-change",
)
#: The card-less verbs' own words, for ``pager.<cmd>`` and ``alias.*``.
VERBS = ("status", "log", "diff", "show", "blame", "branch", "tag", "rev-parse", "ls-files", "describe",
         "rev-list", "shortlog", "stash", "version")
SIGNED = {"openpgp": "PGP SIGNATURE", "ssh": "SSH SIGNATURE", "x509": "SIGNED MESSAGE"}


# ── git's own list, as data ─────────────────────────────────────────────


#: A name whose last word says its value may be a program or a file. The
#: data classifies every listed name this matches.
_MAY_NAME_ONE = re.compile(
    r"(?i)(cmd|command|program|helper|hook|editor|pager|path|file|dir|directory|driver|textconv|clean|"
    r"smudge|process|proxy|tunnel|external|browser|viewer|askpass|pack|vcs|httpd|fsmonitor|template|blob|"
    r"tree|url|target|signature|sort|pretty|\*)$")
_KINDS = ("programs", "files", "signature", "moves", "reopens", "plain")


def test_the_data_is_gits_own_list_classified() -> None:
    listed = set(DATA["listed"])
    assert len(listed) > 900, "the data holds git's whole list"
    classified = [name for kind in _KINDS for name in DATA[kind]]
    assert len(classified) == len(set(classified)), "each name has one kind"
    assert set(classified) <= listed | set(DATA["documented_elsewhere"])
    unclassified = sorted(n for n in listed if _MAY_NAME_ONE.search(n) and n not in classified)
    assert not unclassified, unclassified


def test_the_installed_git_lists_no_setting_the_data_leaves_out() -> None:
    """A newer git's new program or file setting fails here until the data
    classifies it from git's documentation (and the sweep plants it)."""
    listed = subprocess.run([shutil.which("git") or "git", "help", "--config"], capture_output=True,
                            text=True, check=True).stdout.splitlines()
    classified = {name for kind in _KINDS for name in DATA[kind]}
    new = sorted(n.strip() for n in listed
                 if n.strip() and not n.startswith("'") and _MAY_NAME_ONE.search(n.strip())
                 and n.strip() not in classified)
    assert not new, f"git {DATA['git']} classified; this git also lists {new}"


# ── The planted repository ──────────────────────────────────────────────


class Planted:
    """The marker every planted program writes to, and the Kazma file."""

    def __init__(self, tmp: Path, env_file: Path) -> None:
        self.marker = tmp / "ran.txt"
        self.script = tmp / "marker.py"
        self.env_file = env_file
        self.tmp = tmp
        self.script.write_text(
            "import sys\n"
            f"open({str(self.marker)!r}, 'a').write(sys.argv[1] + '\\n')\n"
            "if 'pass' in sys.argv[2:3]:\n"
            "    sys.stdout.write(sys.stdin.read())\n"
            "sys.exit(0 if 'pass' in sys.argv[2:3] else 1)\n",
            encoding="utf-8")

    def command(self, key: str, *extra: str) -> str:
        """A shell command git runs (``sh -c``) that marks *key*."""
        return " ".join([f'"{PY}"', f'"{self.script.as_posix()}"', key, *extra])

    def program(self, key: str) -> str:
        """A program path git starts with no shell, marking *key*."""
        path = self.tmp / f"program-{re.sub(r'[^A-Za-z0-9]+', '-', key)}.sh"
        path.write_bytes(f'#!/bin/sh\nexec "{PY}" "{self.script.as_posix()}" "{key}" "$@"\n'.encode())
        path.chmod(0o755)
        return path.as_posix()

    def hooks(self) -> str:
        folder = self.tmp / "hooks"
        folder.mkdir(exist_ok=True)
        for event in HOOK_EVENTS:
            (folder / event).write_bytes(
                f'#!/bin/sh\nexec "{PY}" "{self.script.as_posix()}" "hook:{event}"\n'.encode())
            (folder / event).chmod(0o755)
        return folder.as_posix()

    def ran(self) -> list[str]:
        return self.marker.read_text(encoding="utf-8").split() if self.marker.exists() else []


#: Settings git starts with no shell: the value is the program's path.
_NO_SHELL = frozenset({"core.askPass", "gpg.program", "gpg.<format>.program", "browser.<tool>.path",
                       "man.<tool>.path", "difftool.<tool>.path", "mergetool.<tool>.path",
                       "sendemail.smtpServer"})
#: Settings whose value starts with ``!`` to run a shell command.
_BANG = frozenset({"alias.*", "submodule.<name>.update", "credential.helper", "credential.<url>.*"})
#: Settings whose value is a URL a transport opens, or a name git looks up.
_URL = frozenset({"remote.<name>.url", "remote.<name>.pushurl", "submodule.<name>.url"})
_NAMED = {"web.browser": "kz", "help.browser": "kz", "man.viewer": "kz", "remote.<name>.vcs": "kz",
          "remote.<name>.proxy": "http://127.0.0.1:9", "http.proxy": "http://127.0.0.1:9"}


def _names(documented: str) -> list[str]:
    """The concrete names a documented name is planted as."""
    if documented == "pager.<cmd>":
        return [f"pager.{verb}" for verb in VERBS]
    if documented == "alias.*":
        return [f"alias.{verb}" for verb in (*VERBS, "kz")]
    if documented == "gpg.<format>.program":
        return [f"gpg.{fmt}.program" for fmt in SIGNED]
    if documented == "credential.<url>.*":
        return ["credential.https://example.invalid.helper"]
    return [re.sub(r"<[^>]+>|\*", "kz", documented)]


def _program_settings(planted: Planted) -> list[tuple[str, str]]:
    settings: list[tuple[str, str]] = []
    for documented in DATA["programs"]:
        for name in _names(documented):
            if documented == "core.hooksPath":
                value = planted.hooks()
            elif documented in _NO_SHELL:
                value = planted.program(name)
            elif documented in _BANG:
                value = "!" + planted.command(name)
            elif documented in _URL:
                value = "ext::" + planted.command(name)
            elif documented in _NAMED:
                value = _NAMED[documented]
            elif documented.startswith("filter."):
                value = planted.command(name, "pass")
            else:
                value = planted.command(name)
            settings.append((name, value))
    settings += [("hook.kz.event", event) for event in HOOK_EVENTS]
    settings += [("protocol.allow", "always")]
    return settings


def _mailmap_blob(root: Path) -> str:
    """A blob shaped as a mailmap line naming the Kazma file's secret."""
    return subprocess.run(
        [shutil.which("git") or "git", "hash-object", "-w", "--stdin"], cwd=root, check=True,
        input=f"{SECRET} <test@example.invalid>\n".encode(), capture_output=True,
    ).stdout.decode().strip()


def _file_settings(planted: Planted, root: Path) -> list[tuple[str, str]]:
    blob = _mailmap_blob(root)
    blobs = {"mailmap.blob": blob, "sendemail.mailmap.blob": blob, "attr.tree": "HEAD"}
    return [(name, blobs.get(documented, planted.env_file.as_posix()))
            for documented in DATA["files"] for name in _names(documented)]


def _signature_settings() -> list[tuple[str, str]]:
    assert set(DATA["signature"]) == {"log.showSignature", "format.pretty", "pretty.<name>", "tag.sort",
                                      "branch.sort"}, "a new signature setting is planted here"
    return [("log.showSignature", "true"), ("format.pretty", "kz"),
            ("pretty.kz", "format:%h %G? %GK %GS %GG %s"), ("tag.sort", "*signature"),
            ("branch.sort", "signature")]


def _signed(root: Path) -> None:
    """A commit signed in each format, each on a branch, and a signed tag:
    git verifies a signature it is asked about with that format's program."""
    tree = _git(root, "rev-parse", "HEAD^{tree}").strip()
    head = _git(root, "rev-parse", "HEAD").strip()
    who = "Kazma Test <test@example.invalid> 1700000000 +0000"
    for fmt, word in SIGNED.items():
        body = (f"tree {tree}\nparent {head}\nauthor {who}\ncommitter {who}\n"
                f"gpgsig -----BEGIN {word}-----\n \n AAAA\n -----END {word}-----\n\nsigned {fmt}\n")
        _git(root, "update-ref", f"refs/heads/sig-{fmt}", _object(root, "commit", body))
    tag = (f"object {head}\ntype commit\ntag signed\ntagger {who}\n\nsigned tag\n"
           "-----BEGIN PGP SIGNATURE-----\n\nAAAA\n-----END PGP SIGNATURE-----\n")
    _git(root, "update-ref", "refs/tags/signed", _object(root, "tag", tag))


def _object(root: Path, kind: str, body: str) -> str:
    return subprocess.run([shutil.which("git") or "git", "hash-object", "-t", kind, "-w", "--stdin"],
                          cwd=root, input=body.encode(), capture_output=True, check=True).stdout.decode().strip()


def _repository(root: Path, tmp: Path) -> Planted:
    """The workspace as the install folder (as on live): history, a driver
    for every file, signed commits, the Kazma file shaped to show, and a
    same-size change git must read through the file's filter and
    attributes."""
    planted = Planted(tmp, root / ".env")
    planted.env_file.write_bytes(ENV_TEXT.encode())
    _init(root)
    (root / ".gitattributes").write_bytes(b"* diff=kz filter=kz merge=kz\n")
    _commit(root, "src", ".gitattributes", message="first")
    (root / "src" / "app.py").write_text("def greet_all(items):\n    return sorted(items)\n", encoding="utf-8")
    _commit(root, "src", message="sorted greeting")
    _git(root, "branch", "side", "HEAD~1")
    _signed(root)
    (root / "src" / "app.py").write_text("def greet_all(items):\n    return SORTED(items)\n", encoding="utf-8")
    return planted


def _arm(root: Path, settings: list[tuple[str, str]]) -> None:
    for name, value in settings:
        _git(root, "config", "--add", name, value)


def _agent_reads() -> dict[str, str]:
    from kazma_skills.native.git_github_manager import tools

    return {
        "git_status()": asyncio.run(tools.git_status()),
        "git_log()": asyncio.run(tools.git_log()),
        "git_log(oneline=False)": asyncio.run(tools.git_log(oneline=False)),
        "git_log(path)": asyncio.run(tools.git_log(path="src/app.py")),
        "git_diff()": asyncio.run(tools.git_diff()),
        "git_diff(stat=True)": asyncio.run(tools.git_diff(stat=True)),
        "git_diff(HEAD)": asyncio.run(tools.git_diff(target="HEAD")),
        "git_diff(sig-openpgp)": asyncio.run(tools.git_diff(target="sig-openpgp")),
    }


_SIGNED_READS = ("log -1 sig-openpgp", "log -1 sig-ssh", "show sig-x509", "log --oneline --all", "tag",
                 "tag -l", "branch", "branch -v", "log --stat -1", "shortlog -sn HEAD", "blame src/app.py",
                 "status -s", "show HEAD", "diff")
#: Reads that must answer, showing the user's work, with every setting planted.
_MUST_ANSWER = {"status -s": "src/app.py", "log --oneline": "sorted greeting", "show HEAD": "sorted greeting",
                "diff": "SORTED(items)", "blame src/app.py": "greet_all", "branch": "main",
                "shortlog -sn HEAD": "Kazma Test"}


def _raw_answer(_parts: list[str], code: int, out: str, err: str) -> dict[str, Any]:
    """The IDE's answer before the sentence: git's output and error text."""
    text = (out + err).strip()
    return {"ok": code == 0, "error": None if code == 0 else text, "output": text}


@pytest.fixture()
def raw(monkeypatch: pytest.MonkeyPatch) -> None:
    """Git's own text in every answer, as before the sentence: these tests
    judge what git printed; the sentence is a layer judged on its own."""
    from kazma_core.ide import service
    from kazma_skills.native.git_github_manager import tools

    monkeypatch.setattr(service, "_git_answer", _raw_answer)
    monkeypatch.setattr(tools, "_read_failed", lambda verb, code, err: f"Error: git {verb} failed: {err}")


def _logged(caplog: pytest.LogCaptureFixture) -> str:
    return "\n".join(record.getMessage() for record in caplog.records)


def test_no_setting_starts_a_program_or_quotes_a_file(
    ws: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture, raw: None,
) -> None:
    caplog.set_level(logging.DEBUG)
    planted = _repository(ws, tmp_path)
    _arm(ws, [*_program_settings(planted), *_file_settings(planted, ws), *_signature_settings()])
    leaks, fired = [], {}
    for command in [*card_less_commands(), *_SIGNED_READS]:
        before = planted.ran()
        answer = _ide(command)
        if planted.ran() != before:
            fired[command] = planted.ran()[len(before):]
        if _leaked(answer):
            leaks.append(command)
        if command in _MUST_ANSWER:
            assert answer["ok"] and _MUST_ANSWER[command] in answer["output"], (command, answer)
    for name, said in _agent_reads().items():
        if _leaked(said):
            leaks.append(name)
    assert "sorted greeting" in _agent_reads()["git_log()"]
    assert not fired, f"programs the repository's settings started: {fired}"
    assert not leaks, f"reads that printed the Kazma file: {leaks}"
    assert SECRET not in _logged(caplog), "git's text in the log quoted the Kazma file"
    assert planted.env_file.read_bytes() == ENV_TEXT.encode(), "a read wrote the Kazma file"


def _submodule(root: Path, planted: Planted) -> None:
    """A submodule whose own configuration names a filter, a diff driver,
    fsmonitor and hooks, with a same-size change its filter must read."""
    inner = root / "vendor"
    inner.mkdir()
    _init(inner)
    (inner / ".gitattributes").write_bytes(b"* filter=inner diff=inner\n")
    (inner / "lib.txt").write_text("alpha\n", encoding="utf-8")
    _commit(inner, message="inner")
    _commit(root, "vendor", message="with a submodule")
    for name, value in (("filter.inner.clean", planted.command("sub:filter.clean", "pass")),
                        ("filter.inner.process", planted.command("sub:filter.process")),
                        ("diff.inner.textconv", planted.command("sub:textconv")),
                        ("diff.inner.command", planted.command("sub:diff.command")),
                        ("core.fsmonitor", planted.command("sub:fsmonitor")),
                        ("core.hooksPath", planted.hooks())):
        _git(inner, "config", name, value)
    _git(root, "config", "status.submoduleSummary", "true")
    (inner / "lib.txt").write_text("omega\n", encoding="utf-8")


def test_a_submodules_own_settings_start_nothing(ws: Path, tmp_path: Path, raw: None) -> None:
    planted = _repository(ws, tmp_path)
    _submodule(ws, planted)
    for command in ("status", "status -s", "status --porcelain", "diff", "diff --stat", "diff HEAD", "log -p -1"):
        _ide(command)
    assert "src/app.py" in _agent_reads()["git_status()"], "the read answered"
    assert not planted.ran(), planted.ran()


# ── Behaviour, not names: settings that reopen a route, drivers of any name


def _absorbed_submodule(root: Path, planted: Planted) -> Path:
    """A submodule added the usual way (its repository under ``.git/modules``,
    named in ``.gitmodules``) whose own configuration names a filter for
    every file, with a same-size change the filter must read."""
    lib = planted.tmp / "lib"
    lib.mkdir()
    _init(lib)
    (lib / ".gitattributes").write_bytes(b"* filter=inner\n")
    (lib / "lib.txt").write_text("alpha\n", encoding="utf-8")
    _commit(lib, message="lib")
    _git(root, "-c", "protocol.file.allow=always", "submodule", "add", "-q", lib.as_posix(), "vendor")
    _git(root, "commit", "-q", "-m", "with a submodule")
    inner = root / "vendor"
    _git(inner, "config", "filter.inner.clean", planted.command("sub:filter.clean", "pass"))
    _git(inner, "config", "filter.inner.process", planted.command("sub:filter.process"))
    (inner / "lib.txt").write_text("omega\n", encoding="utf-8")
    return inner


def _ignore_in_config(value: str) -> Callable[[Path, Planted], None]:
    def plant(root: Path, _planted: Planted) -> None:
        _git(root, "config", "submodule.vendor.ignore", value)
    return plant


def _ignore_included(root: Path, planted: Planted) -> None:
    included = planted.tmp / "submodules.cfg"
    included.write_bytes(b'[submodule "vendor"]\n\tignore = none\n')
    _git(root, "config", "include.path", included.as_posix())


def _ignore_in_gitmodules(*, committed: bool) -> Callable[[Path, Planted], None]:
    def plant(root: Path, _planted: Planted) -> None:
        with (root / ".gitmodules").open("ab") as fh:
            fh.write(b"\tignore = none\n")
        if committed:
            _git(root, "commit", "-q", "-m", "ignore none", "--", ".gitmodules")
    return plant


#: Each way a submodule's ``ignore`` can reopen its work tree: git lets it
#: win over a ``diff.ignoreSubmodules`` default, from any of these.
_REOPENED: dict[str, Callable[[Path, Planted], None]] = {
    "ignore=none in .git/config": _ignore_in_config("none"),
    "ignore=untracked in .git/config": _ignore_in_config("untracked"),
    "ignore=none in an included file": _ignore_included,
    "ignore=none in .gitmodules, committed": _ignore_in_gitmodules(committed=True),
    "ignore=none in .gitmodules, in the work tree only": _ignore_in_gitmodules(committed=False),
}
#: The other settings that reopen a route, planted with each.
_REOPEN_SETTINGS = (("diff.ignoreSubmodules", "none"), ("status.submoduleSummary", "true"),
                    ("submodule.recurse", "true"), ("diff.submodule", "diff"))


def _refusals(root: Path) -> None:
    """The check before a git command's card (shell_exec), which reads git."""
    from kazma_core.safety.git_tree_writes import git_command_refusal

    for words in (["stash"], ["checkout", "side"], ["restore", "."], ["checkout", "--", "."]):
        git_command_refusal(words, root)


def _ran_through_each_door(root: Path, planted: Planted, commands: tuple[str, ...]) -> dict[str, list[str]]:
    ran: dict[str, list[str]] = {}
    for door, read in (("IDE", lambda: [_ide(c) for c in commands]), ("agent", _agent_reads),
                       ("check before the card", lambda: _refusals(root))):
        before = len(planted.ran())
        read()
        ran[door] = planted.ran()[before:]
    return ran


_WORK_TREE_READS = ("status", "status -s", "status --porcelain", "diff", "diff --stat", "diff HEAD",
                    "diff --exit-code", "ls-files -m", "log -p -1", "show HEAD")


def test_the_reopening_settings_are_all_planted() -> None:
    assert {"submodule.<name>.ignore", *(name for name, _ in _REOPEN_SETTINGS)} == set(DATA["reopens"])


@pytest.mark.parametrize("how", sorted(_REOPENED))
def test_a_setting_that_reopens_a_submodule_starts_nothing(how: str, ws: Path, tmp_path: Path, raw: None) -> None:
    planted = _repository(ws, tmp_path)
    _absorbed_submodule(ws, planted)
    _REOPENED[how](ws, planted)
    for name, value in _REOPEN_SETTINGS:
        _git(ws, "config", name, value)
    ran = _ran_through_each_door(ws, planted, _WORK_TREE_READS)
    assert not any(ran.values()), (how, ran)


#: Settings the all-at-once sweep leaves out: those that move what git
#: reads (their own test) and those that change what a repository is.
_NOT_SWEPT = frozenset({"include.path", "includeIf.kz.path", "core.worktree", "core.bare",
                        "core.repositoryFormatVersion", "extensions.kz", "core.sharedRepository",
                        "safe.directory"})
#: What git itself runs while the sweep finds the settings it refuses.
_PLAIN_READS = (("status", "-s"), ("log", "-1", "-p"), ("diff", "HEAD"), ("blame", "src/app.py"),
                ("branch", "-v"), ("tag", "-l"), ("shortlog", "-sn", "HEAD"), ("ls-files", "-m"),
                ("show", "HEAD"), ("stash", "list"), ("describe", "--always"), ("rev-list", "--count", "HEAD"),
                ("rev-parse", "HEAD"), ("diff",))
_SWEEP_READS = ("status", "status -s", "diff", "diff HEAD", "diff --stat", "show HEAD", "log -1", "log -p -1",
                "log --oneline", "log --format=%aN", "blame src/app.py", "branch -v", "tag -l", "shortlog -sn HEAD",
                "ls-files -m", "describe --always", "stash list", "rev-list --count HEAD", "log -1 sig-openpgp",
                "log -1 sig-ssh", "show sig-x509")


def _swept_names() -> list[str]:
    """Every name git lists, placeholders filled, but those git refuses a
    text value for (the data, found by this sweep) and :data:`_NOT_SWEPT`."""
    names = []
    for documented in DATA["listed"]:
        name = re.sub(r"<[^>]+>|\*", "kz", documented)
        if ("hasconfig:" in documented or name in _NOT_SWEPT or name in DATA["refused_with_a_text_value"]
                or not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]*", name.rpartition(".")[2])):
            continue
        names.append(name)
    return names


def _config_text(names: list[str], value: str) -> str:
    def quoted(text: str) -> str:
        return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'

    text = []
    for name in names:
        section, _, rest = name.partition(".")
        sub, _, key = rest.rpartition(".")
        text.append(f"[{section} {quoted(sub)}]\n\t{key} = {quoted(value)}\n" if sub
                    else f"[{section}]\n\t{key} = {quoted(value)}\n")
    return "".join(text)


def _plain(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([shutil.which("git") or "git", *args], cwd=root, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL, timeout=60, check=False)


def _plant_every_setting(root: Path, value: str) -> list[str]:
    """Every swept name at *value*, written into ``.git/config`` (``git
    config`` itself stops on a value git refuses); a name git still refuses
    -- named in its error, or found by halving -- is taken out. Answers
    those taken out."""
    config = root / ".git" / "config"
    base = config.read_text(encoding="utf-8")
    names = _swept_names()

    def failing(chosen: list[str]) -> str | None:
        config.write_text(base + _config_text(chosen, value), encoding="utf-8")
        for args in _PLAIN_READS:
            done = _plain(root, *args)
            if done.returncode not in (0, 1):
                return done.stderr
        return None

    refused: list[str] = []
    while (err := failing(names)) is not None:
        assert len(refused) < 25, f"git refuses more settings than the data says: {refused} (refresh it)"
        named = re.search(r"for '([^']+)'", err)
        lower = {name.lower(): name for name in names}
        if named and named.group(1).lower() in lower:
            bad = lower[named.group(1).lower()]
        else:
            low, high = 0, len(names)
            while high - low > 1:
                middle = (low + high) // 2
                low, high = (low, middle) if failing(names[:middle]) is not None else (middle, high)
            bad = names[low]
        refused.append(bad)
        names.remove(bad)
    return refused


@pytest.mark.parametrize("kind", ("a program", "a Kazma file"))
def test_every_setting_git_lists_at_once_starts_nothing_and_prints_no_kazma_file(
    kind: str, ws: Path, tmp_path: Path, raw: None, caplog: pytest.LogCaptureFixture,
) -> None:
    """Behaviour, not names: every setting git lists, at a program or at the
    Kazma file, whatever its name says, through every door."""
    caplog.set_level(logging.DEBUG)
    planted = _repository(ws, tmp_path)
    value = planted.command("any-setting", "pass") if kind == "a program" else planted.env_file.as_posix()
    _plant_every_setting(ws, value)
    planted.marker.unlink(missing_ok=True)
    answers: list[Any] = [_ide(c) for c in _SWEEP_READS]
    answers += list(_agent_reads().values())
    _refusals(ws)
    assert not planted.ran(), planted.ran()
    assert not any(_leaked(a) for a in answers) and SECRET not in _logged(caplog)
    for command in ("status -s", "log --oneline", "show HEAD", "diff", "blame src/app.py"):
        assert _ide(command)["ok"], f"the read answers with every setting planted: {command}"
    # The instrument: git as it is starts the program or prints the file.
    plain = [_plain(ws, *args) for args in _PLAIN_READS]
    if kind == "a program":
        assert planted.ran(), "git itself started nothing: this proves nothing"
    else:
        assert any(SECRET in p.stdout + p.stderr for p in plain), "git itself printed nothing: this proves nothing"


#: Filter driver names git takes from ``.gitattributes``: one holding ``=``
#: (``-c`` splits at the first), the empty name, a dot, case, another
#: script. Each drives its own file, as a clean or a process filter.
_DRIVER_NAMES = {"": "clean", "a=b": "clean", "c=d": "process", "x.y": "process", "UP": "clean",
                 "A=B": "process", "مرشح": "clean", "a==b": "process"}


def _odd_drivers(root: Path, planted: Planted) -> None:
    lines = []
    for index, name in enumerate(_DRIVER_NAMES):
        (root / "src" / f"drv{index}.txt").write_text("alpha\n", encoding="utf-8")
        lines.append(f"drv{index}.txt filter={name}\n")
    (root / "src" / ".gitattributes").write_text("".join(lines), encoding="utf-8")
    _git(root, "add", "-f", "--", "src")
    _git(root, "commit", "-q", "-m", "drivers")
    for index, (name, kind) in enumerate(_DRIVER_NAMES.items()):
        extra = ["pass"] if kind == "clean" else []
        _git(root, "config", f"filter.{name}.{kind}", planted.command(f"driver{index}", *extra))
        (root / "src" / f"drv{index}.txt").write_text("omega\n", encoding="utf-8")


_DRIVER_READS = ("status", "status -s", "status --porcelain", "diff", "diff --stat", "diff HEAD",
                 "diff --exit-code", "ls-files -m", "blame src/drv1.txt", "blame src/drv2.txt")


def test_no_filter_driver_runs_whatever_its_name(ws: Path, tmp_path: Path, raw: None) -> None:
    planted = _repository(ws, tmp_path)
    _odd_drivers(ws, planted)
    ran = _ran_through_each_door(ws, planted, _DRIVER_READS)
    assert not any(ran.values()), ran


def _as_dash_c(env: dict[str, str], listed: str = "") -> dict[str, str]:
    """The control's settings: the same pairs as ``git -c key=value`` passes
    them (``GIT_CONFIG_PARAMETERS``, split at the first ``=``)."""
    from kazma_core.safety import git_read

    pairs = [setting.split("=", 1) for setting in git_read.read_settings()]
    pairs += [[key, "false" if key.lower().endswith(".required") else ""] for key in listed.split("\0") if key]
    env["GIT_CONFIG_PARAMETERS"] = " ".join(f"'{key}={value}'" for key, value in pairs)
    return env


def test_control_as_dash_c_a_driver_named_with_equals_runs_in_every_door(
    ws: Path, tmp_path: Path, raw: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kazma_core.safety import git_read

    planted = _repository(ws, tmp_path)
    _odd_drivers(ws, planted)
    monkeypatch.setattr(git_read, "read_config_env", _as_dash_c)
    with_equals = {f"driver{i}" for i, name in enumerate(_DRIVER_NAMES) if "=" in name}
    ran = _ran_through_each_door(ws, planted, _DRIVER_READS)
    assert all(with_equals & set(names) for names in ran.values()), ran


# ── Every read is bounded, with what git starts ─────────────────────────


def _holder() -> list[str]:
    """A program that starts one more holding the output pipes, then waits."""
    return [sys.executable, "-c",
            "import subprocess, sys, time; "
            "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(12)']); time.sleep(12)"]


def test_a_read_that_never_ends_is_cut_with_what_it_started(tmp_path: Path) -> None:
    import os
    import time

    from kazma_core.safety.git_read import run_git

    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        run_git(_holder(), cwd=str(tmp_path), env=dict(os.environ), timeout=2)
    assert time.monotonic() - started < 8


def test_control_a_bare_timeout_waits_for_what_the_read_started(tmp_path: Path) -> None:
    import threading

    done = threading.Event()

    def bare() -> None:
        try:
            subprocess.run(_holder(), cwd=str(tmp_path), capture_output=True, timeout=2, check=False)
        except subprocess.TimeoutExpired:
            pass
        done.set()

    worker = threading.Thread(target=bare, daemon=True)
    worker.start()
    assert not done.wait(6), "subprocess.run returned on its timeout: this control proves nothing"
    done.wait(20)


def test_every_door_runs_git_through_the_bounded_runner() -> None:
    import ast

    root = Path(__file__).resolve().parents[1]
    places = {"kazma-core/kazma_core/ide/service.py": "_git_read",
              "kazma-skills/kazma_skills/native/git_github_manager/tools.py": "_git_read",
              "kazma-core/kazma_core/safety/git_read.py": "run_read"}
    for rel, name in places.items():
        tree = ast.parse((root / rel).read_text(encoding="utf-8"))
        func = next(n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and n.name == name)
        called = {n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", "")
                  for n in ast.walk(func) if isinstance(n, ast.Call)}
        assert "run_git" in called and "read_config_env" in called, rel
        assert not called & {"run", "Popen", "check_output", "call"}, (rel, called)


def _partial_clone(root: Path, tmp: Path, planted: Planted) -> str:
    """The workspace as a partial clone missing a blob its history names:
    reading it would fetch from the promisor, starting its upload-pack."""
    other = tmp / "promisor"
    other.mkdir()
    _init(other)
    gone = _git(root, "rev-parse", "HEAD:src/app.py").strip()
    loose = root / ".git" / "objects" / gone[:2] / gone[2:]
    assert loose.is_file(), "the instrument: the blob is a loose object"
    loose.chmod(0o644)  # git writes objects read-only
    loose.unlink()
    for name, value in (("core.repositoryFormatVersion", "1"), ("extensions.partialClone", "kz"),
                        ("remote.kz.promisor", "true"), ("remote.kz.url", other.as_posix()),
                        ("remote.kz.uploadpack", planted.command("upload-pack")),
                        ("protocol.file.allow", "always")):
        _git(root, "config", name, value)
    return gone


def test_a_partial_clone_fetches_nothing(ws: Path, tmp_path: Path, raw: None) -> None:
    planted = _repository(ws, tmp_path)
    _partial_clone(ws, tmp_path, planted)
    for command in ("show HEAD", "diff HEAD~1", "log -p -1", "blame src/app.py"):
        _ide(command)
    _agent_reads()
    assert not planted.ran(), planted.ran()


def _moved(root: Path, planted: Planted, setting: str) -> None:
    value = (root / "kazma-data").as_posix() if setting == "core.worktree" else planted.env_file.as_posix()
    name = "includeIf.onbranch:main.path" if setting == "includeIf.<condition>.path" else setting
    _git(root, "config", name, value)


@pytest.mark.parametrize("setting", DATA["moves"])
def test_a_setting_that_moves_what_git_reads_prints_no_kazma_file(
    setting: str, ws: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture, raw: None,
) -> None:
    caplog.set_level(logging.DEBUG)
    planted = _repository(ws, tmp_path)
    _moved(ws, planted, setting)
    answers = [_ide(c) for c in ("status -s", "diff", "log -p -1", "show HEAD", "blame src/app.py",
                                 "log --format=%an")]
    answers += list(_agent_reads().values())
    assert not any(_leaked(a) for a in answers), answers
    assert SECRET not in _logged(caplog)
    assert not planted.ran()


# ── Each override, dropped ──────────────────────────────────────────────


def _without_setting(setting: str) -> Callable[[pytest.MonkeyPatch], None]:
    def drop(patch: pytest.MonkeyPatch) -> None:
        from kazma_core.safety import git_read

        patch.setattr(git_read, "READ_CONFIG", tuple(s for s in git_read.READ_CONFIG if s != setting))
    return drop


def _without_programs(*keys: str) -> Callable[[pytest.MonkeyPatch], None]:
    def drop(patch: pytest.MonkeyPatch) -> None:
        from kazma_core.safety import git_read

        patch.setattr(git_read, "NO_PROGRAM_KEYS", tuple(k for k in git_read.NO_PROGRAM_KEYS if k not in keys))
    return drop


def _without_global(flag: str) -> Callable[[pytest.MonkeyPatch], None]:
    def drop(patch: pytest.MonkeyPatch) -> None:
        from kazma_core.safety import git_read

        patch.setattr(git_read, "READ_GLOBALS", tuple(f for f in git_read.READ_GLOBALS if f != flag))
    return drop


def _without_blame(flag: str) -> Callable[[pytest.MonkeyPatch], None]:
    def drop(patch: pytest.MonkeyPatch) -> None:
        from kazma_core.safety import git_kazma_files

        patch.setattr(git_kazma_files, "BLAME_SHAPE", tuple(f for f in git_kazma_files.BLAME_SHAPE if f != flag))
    return drop


def _both(*drops: Callable[[pytest.MonkeyPatch], None]) -> Callable[[pytest.MonkeyPatch], None]:
    def drop(patch: pytest.MonkeyPatch) -> None:
        for one in drops:
            one(patch)
    return drop


def _plant_file(name: str) -> Callable[[Path, Planted], None]:
    def arm(root: Path, planted: Planted) -> None:
        _git(root, "config", name, _mailmap_blob(root) if name == "mailmap.blob" else planted.env_file.as_posix())
    return arm


def _plant_signature(key: str, *, how: str = "pretty") -> Callable[[Path, Planted], None]:
    def arm(root: Path, planted: Planted) -> None:
        _git(root, "config", key, planted.program(key))
        if key == "gpg.ssh.program":
            # git verifies an ssh signature only against a list of signers.
            signers = planted.tmp / "allowed_signers"
            signers.write_bytes(b"")
            _git(root, "config", "gpg.ssh.allowedSignersFile", signers.as_posix())
        if how == "pretty":
            _git(root, "config", "pretty.kz", "format:%h %G? %s")
            _git(root, "config", "format.pretty", "kz")
        else:
            _git(root, "config", "log.showSignature", "true")
    return arm


def _plant_fsmonitor(root: Path, planted: Planted) -> None:
    _git(root, "config", "core.fsmonitor", planted.command("fsmonitor"))


def _plant_partial(root: Path, planted: Planted) -> None:
    _partial_clone(root, planted.tmp, planted)


def _without_submodule_option(patch: pytest.MonkeyPatch) -> None:
    from kazma_core.safety import git_kazma_files, git_read

    patch.setattr(git_read, "NO_SUBMODULE_TREES", ())
    patch.setattr(git_kazma_files, "STATUS_SHAPE", ())
    patch.setattr(git_kazma_files, "SECTION_SHAPE",
                  tuple(f for f in git_kazma_files.SECTION_SHAPE if f != "--ignore-submodules=dirty"))


def _plant_reopened_submodule(root: Path, planted: Planted) -> None:
    _absorbed_submodule(root, planted)
    _ignore_in_config("none")(root, planted)


#: Each override -> (how it is dropped, what is planted, the IDE reads, the
#: agent's tools read too, what shows: "ran" a program or "printed" the file
#: in an answer or the log).
_OVERRIDES: dict[str, tuple[Callable[[pytest.MonkeyPatch], None], Callable[[Path, Planted], None],
                            tuple[str, ...], bool, str]] = {
    "core.fsmonitor=false": (_without_setting("core.fsmonitor=false"), _plant_fsmonitor,
                             ("status -s",), True, "ran"),
    "core.attributesFile=": (_without_setting("core.attributesFile="), _plant_file("core.attributesFile"),
                             ("status -s", "diff"), False, "printed"),
    "mailmap.file=": (_without_setting("mailmap.file="), _plant_file("mailmap.file"),
                      ("log --format=%aN", "shortlog -sn HEAD"), True, "printed"),
    "mailmap.blob=": (_without_setting("mailmap.blob="), _plant_file("mailmap.blob"),
                      ("log --format=%aN", "shortlog -sn HEAD"), True, "printed"),
    # A second layer behind the command-line option.
    "diff.ignoreSubmodules=dirty": (_both(_without_setting("diff.ignoreSubmodules=dirty"),
                                         _without_submodule_option), _submodule,
                                    ("status -s",), True, "ran"),
    # A submodule's own ignore=none wins over the setting; the option wins over it.
    "--ignore-submodules=dirty": (_without_submodule_option, _plant_reopened_submodule,
                                  ("status -s", "diff"), True, "ran"),
    # A second layer: behind it, the signature program cannot start.
    "log.showSignature=false": (_both(_without_setting("log.showSignature=false"),
                                      _without_programs("gpg.program")),
                                _plant_signature("gpg.program", how="show"),
                                ("log -1 sig-openpgp",), True, "ran"),
    # Planted under its newer name: one setting in git, so one override.
    "gpg.program": (_without_programs("gpg.program"), _plant_signature("gpg.openpgp.program"),
                    ("log -1 sig-openpgp",), True, "ran"),
    "gpg.ssh.program": (_without_programs("gpg.ssh.program"), _plant_signature("gpg.ssh.program"),
                        ("log -1 sig-ssh",), False, "ran"),
    "gpg.x509.program": (_without_programs("gpg.x509.program"), _plant_signature("gpg.x509.program"),
                         ("log -1 sig-x509",), False, "ran"),
    "--no-ignore-revs-file": (_without_blame("--no-ignore-revs-file"), _plant_file("blame.ignoreRevsFile"),
                              ("blame src/app.py",), False, "printed"),
    # The IDE also sets GIT_NO_LAZY_FETCH: the agent's tools show it.
    "--no-lazy-fetch": (_without_global("--no-lazy-fetch"), _plant_partial, (), True, "ran"),
}
#: Overrides with no control, and why.
_NO_CONTROL = {
    "core.pager=cat": "a pager starts only on a terminal; a read's output is a pipe",
    "core.hooksPath": "no hook event fires on a read with --no-optional-locks (the sweep plants every one)",
}


def test_every_override_has_a_control() -> None:
    from kazma_core.safety import git_kazma_files, git_read

    overrides = {*git_read.READ_CONFIG, *git_read.NO_PROGRAM_KEYS, "core.hooksPath",
                 *git_read.NO_SUBMODULE_TREES, *git_kazma_files.STATUS_SHAPE,
                 *(f for f in git_kazma_files.BLAME_SHAPE if f != "--no-textconv"),
                 *(f for f in git_read.READ_GLOBALS if f != "--no-replace-objects")}
    # --no-textconv and --no-replace-objects: controls in
    # tests/test_git_reads_print_no_kazma_file.py.
    assert overrides == set(_OVERRIDES) | set(_NO_CONTROL)


@pytest.mark.parametrize("override", sorted(_OVERRIDES))
def test_control_a_planted_dropped_override_lets_its_setting_through(
    override: str, ws: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture, raw: None,
) -> None:
    caplog.set_level(logging.DEBUG)
    drop, arm, commands, agent_too, shows = _OVERRIDES[override]
    planted = _repository(ws, tmp_path)
    arm(ws, planted)

    def through_the_doors() -> bool:
        caplog.clear()
        answers: list[Any] = [_ide(c) for c in commands]
        if agent_too:
            answers += list(_agent_reads().values())
        if shows == "ran":
            return bool(planted.ran())
        return any(_leaked(a) for a in answers) or SECRET in _logged(caplog)

    assert not through_the_doors(), f"with {override}"
    drop(monkeypatch)
    assert through_the_doors(), f"without {override} the setting is not let through: this proves nothing"


# ── A failed read says a sentence; git's text goes to the log ───────────


def test_a_failed_read_answers_a_sentence_in_the_readers_language(
    ws: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    from kazma_ui import i18n

    caplog.set_level(logging.DEBUG)
    _repository(ws, tmp_path)
    failed = _ide("log no-such-branch")
    assert failed["ok"] is False and failed["error"] == i18n.t("ide.git_read_failed", "en", code=128), failed
    assert "no-such-branch" not in f"{failed['output']} {failed['error']}"
    assert "no-such-branch" in _logged(caplog), "git's own text is kept in the log"
    token = i18n.set_request_language("ar")
    try:
        arabic = _ide("log no-such-branch")
    finally:
        i18n._REQUEST_LANG.reset(token)
    assert arabic["error"] == i18n.t("ide.git_read_failed", "ar", code=128), arabic
    from kazma_skills.native.git_github_manager import tools

    _git(ws, "checkout", "-q", "--orphan", "empty")
    said = asyncio.run(tools.git_log())
    assert said.startswith("Error: git log failed (exit code 128)") and "does not have any commits" not in said


def test_differences_found_with_exit_code_are_an_answer(ws: Path, tmp_path: Path) -> None:
    """``diff --exit-code`` and ``--quiet`` exit 1 when they find
    differences: an answer, said in a sentence, not a failure."""
    from kazma_ui import i18n

    _repository(ws, tmp_path)
    found = _ide("diff --exit-code")
    assert found["ok"] is True and found.get("exit_code") == 1, found
    assert "SORTED(items)" in found["output"] and i18n.t("ide.git_read_differences", "en") in found["output"]
    quiet = _ide("diff --quiet")
    assert quiet["ok"] is True and quiet["output"] == i18n.t("ide.git_read_differences", "en"), quiet
    _git(ws, "checkout", "-q", "--", "src/app.py")
    same = _ide("diff --exit-code")
    assert same == {"ok": True, "error": None, "output": ""}, same


def test_control_with_the_old_answer_gits_text_quotes_the_file(
    ws: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With blame's override dropped, git's error quotes the file's first
    line: the sentence keeps it out of the answer, the old answer did not."""
    from kazma_core.ide import service

    planted = _repository(ws, tmp_path)
    _plant_file("blame.ignoreRevsFile")(ws, planted)
    _without_blame("--no-ignore-revs-file")(monkeypatch)
    assert not _leaked(_ide("blame src/app.py"))

    monkeypatch.setattr(service, "_git_answer", _raw_answer)
    assert _leaked(_ide("blame src/app.py"))
