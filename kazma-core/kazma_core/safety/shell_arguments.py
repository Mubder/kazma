"""Argument reading for shell_exec's post-approval policy.

Three questions, each answered here so the policy asks them one way:

* which values a short option cluster carries (``short_option_values``);
* which word is the SUBCOMMAND of git or uv, after their global options
  (``split_subcommand``) -- the first word that does not start with "-" is
  not it: ``git -C . config alias.x "!cmd"`` has ``.`` there, and
  ``uv --color never run ...`` has ``never`` (2026-11-10 audit);
* which values a token carries attached to its option (``attached_values``),
  for every binary, so ``sort -oC:\\x.bat`` or ``date -f<path>`` reaches the
  path policy like a separate operand does.
"""
from __future__ import annotations

import os
import shlex


def split_command_line(command: str) -> list[str]:
    """``shell_exec``'s words of *command*, as it runs them (no shell).

    ``shlex.split`` defaults to posix=True, which treats "\\" as an escape:
    on Windows ``git -C C:\\repo status`` became ``-C C:repo``. So Windows
    splits non-posix and strips ONE matched pair of surrounding quotes per
    word. Raises ``ValueError`` on an unbalanced quote. The approval card's
    disclosures read the same words (``safety.git_repo_programs``).
    """
    if os.name == "nt":
        return [
            a[1:-1] if len(a) >= 2 and a[0] == a[-1] and a[0] in ("'", '"') else a
            for a in shlex.split(command, posix=False)
        ]
    return shlex.split(command)


# git is not here: its short options before the subcommand are read by
# split_subcommand, and after it they belong to the subcommand
# (``git log -3`` was refused as an "unsupported git short option").
_SHORT_VALUES = {
    "grep": "femABCDd", "jq": "fL", "zip": "bO", "unzip": "d",
    "tar": "fCIT", "sort": "ktoST",
}
_TAR_SWITCHES = frozenset("ctvzjJaO")
_SHORT_SWITCHES = {
    "grep": frozenset("EFGPivwxclLnoHhqsIraRUzZ"),
    "jq": frozenset("nrscRMCaSje"),
    "zip": frozenset("rqv09XjD"),
    "unzip": frozenset("pqlnvfojC"),
    "sort": frozenset("bdfghiMnRrsuzcCmV"),
}

# git's global options (git(1), "OPTIONS"), before the subcommand.
_GIT_GLOBAL_SWITCHES = frozenset({
    "-p", "--paginate", "-P", "--no-pager", "--bare",
    "--no-replace-objects", "--no-lazy-fetch", "--no-optional-locks",
    "--no-advice", "--literal-pathspecs", "--glob-pathspecs",
    "--noglob-pathspecs", "--icase-pathspecs",
    "--html-path", "--man-path", "--info-path",
    "-v", "--version", "-h", "--help",
})
# Each takes a value, separate or after "=" (``-C`` separate only).
_GIT_GLOBAL_VALUES = frozenset({
    "-C", "--git-dir", "--work-tree", "--namespace", "--attr-source",
})
# Global options that set configuration or run a program: refused.
_GIT_GLOBAL_REFUSED = frozenset({
    "-c", "--config-env", "--exec-path", "--super-prefix",
})

# uv's global options (``uv help``), before the subcommand.
_UV_GLOBAL_SWITCHES = frozenset({
    "-q", "--quiet", "-v", "--verbose", "-n", "--no-cache",
    "--system-certs", "--native-tls", "--no-native-tls", "--offline",
    "--no-progress", "--no-config", "--managed-python",
    "--no-managed-python", "--no-python-downloads",
    "--allow-python-downloads", "--preview", "--no-preview",
    "-h", "--help", "-V", "--version",
})
_UV_GLOBAL_VALUES = frozenset({
    "--color", "--allow-insecure-host", "--directory", "--project",
    "--config-file", "--cache-dir", "--python-preference",
    "--preview-features",
})


def split_subcommand(binary: str, args: list[str]) -> tuple[int, str]:
    """Return ``(index, subcommand)`` of git's or uv's subcommand in *args*.

    *args* is the argument list without the binary. Global options are
    consumed with their values; an option that is not a known global option
    raises ``ValueError`` (it could be one that takes a value, and the word
    after it would then be taken for the subcommand). With no subcommand the
    index is ``len(args)`` and the name ``""``. Other binaries: the first
    word that does not start with "-".
    """
    if binary == "git":
        switches, values, refused = (
            _GIT_GLOBAL_SWITCHES, _GIT_GLOBAL_VALUES, _GIT_GLOBAL_REFUSED,
        )
    elif binary == "uv":
        switches, values, refused = (
            _UV_GLOBAL_SWITCHES, _UV_GLOBAL_VALUES, frozenset(),
        )
    else:
        for index, arg in enumerate(args):
            if not arg.startswith("-"):
                return index, arg
        return len(args), ""
    index = 0
    while index < len(args):
        arg = args[index]
        if not arg.startswith("-") or arg == "-":
            return index, arg
        flag, has_value, _ = arg.partition("=")
        if flag in refused or (binary == "git" and arg[:2] == "-c"):
            raise ValueError(
                f"'{flag}' is not allowed for {binary}: it sets configuration "
                "or names a program to run."
            )
        if binary == "uv" and not has_value and _uv_switch_cluster(arg):
            index += 1
            continue
        if flag in switches and not has_value:
            index += 1
            continue
        if flag in values:
            if has_value:
                if flag == "-C":
                    break  # git takes -C's value only as the next word
                index += 1
            else:
                index += 2
            continue
        break
    else:
        return len(args), ""
    raise ValueError(
        f"Unknown {binary} option '{args[index]}' before the subcommand; "
        "name the subcommand first or use a native tool."
    )


def _uv_switch_cluster(arg: str) -> bool:
    """``-qq`` / ``-vv`` / ``-qn``: uv's repeatable short switches."""
    return (
        len(arg) > 2 and arg[0] == "-" and arg[1] != "-"
        and all(ch in "qvn" for ch in arg[1:])
    )


def matches_blocked(flag: str, blocked: tuple[str, ...] | frozenset[str]) -> bool:
    """True when *flag* is a blocked option, or an abbreviation of a long one.

    GNU getopt and git's parse-options accept an unambiguous prefix of a long
    option (``--open-files=``, ``--compress=``), so a long flag that starts a
    blocked long option is that option.
    """
    if flag in blocked:
        return True
    if flag.startswith("--") and len(flag) > 2:
        return any(b.startswith("--") and b.startswith(flag) for b in blocked)
    return False


def attached_values(argument: str) -> list[str]:
    """Every value a single-dash token could carry attached to an option.

    ``-oC:\\out.bat`` gives ``C:\\out.bat``; which letter takes the value
    depends on the binary, so every tail after the first option letter is
    returned (``-ro/x`` gives ``o/x`` and ``/x``). Long options carry theirs
    after "=", which the caller reads.
    """
    if len(argument) <= 2 or argument[0] != "-" or argument[1] == "-":
        return []
    return [argument[i:] for i in range(2, len(argument))]


def program_name(argv0: str) -> str:
    """The program a command's first word names (``git.exe`` and a full path to git are git)."""
    from pathlib import Path

    p = Path(argv0)
    if os.name == "nt" and p.suffix.lower() == ".exe":
        return p.stem
    return p.name


# ── options that start another program, or write where no check sees ─────
#
# The binary allowlist vets the first word only, and the path guard vets
# the values; neither catches an allowlisted binary asked to run another
# program by name (audit F-03: ``find . -exec whoami +``). One table, read
# by shell_exec after its approval and by the check before the card
# (safety/shell_paths).

_EXEC_CAPABLE_ARGS: dict[str, tuple[str, ...]] = {
    "find": (
        "-exec", "-execdir", "-ok", "-okdir",
        "-delete", "-fprintf", "-fprint", "-fprint0", "-fls",
    ),
    "git": (
        "--upload-pack", "--receive-pack", "--exec-path",
        "-c", "--config-env", "--upload-archive",
        # git grep opens matching files in a program it names.
        "--open-files-in-pager",
        # git difftool runs the command it names.
        "--extcmd",
    ),
    # sort compresses temporary files through a program it names, and
    # writes where -o/--output says; --files0-from takes the files to read
    # from a list no path check sees.
    "sort": (
        "--compress-program", "-o", "--output", "--files0-from",
    ),
    "wc": ("--files0-from",),
    "du": ("--files0-from",),
    # GNU tar runs a program at a volume change (-F/--info-script,
    # --new-volume-script; -M/--multi-volume asks for one, --tape-length
    # starts the change), at a checkpoint (--checkpoint-action=exec=), for
    # each member (--to-command), as its compressor (-I), and to reach a
    # remote archive (--rsh-command, --rmt-command). The 2026-11 review ran
    # `tar -c --multi-volume --tape-length=10 --info-script=<cmd>`.
    "tar": (
        "--use-compress-program", "--to-command", "-I",
        "--checkpoint-action", "--rmt-command", "--rsh-command",
        "-F", "--info-script", "--new-volume-script",
        "-M", "--multi-volume", "--tape-length",
        "--index-file", "--listed-incremental", "-g", "--volno-file",
        "-T", "--files-from", "--extract", "--get", "--absolute-names", "--dereference",
    ),
    "zip": ("-TT", "--unzip-command"),
    "unzip": ("-TT",),
    "grep": ("-f", "--file"),  # reads a pattern file outside cwd
    "jq": ("-f", "--from-file"),
}

#: The git subcommands shell_exec runs: ones that start no program beyond
#: what the repository itself configures (hooks, filters, drivers -- named
#: on the approval card, safety/git_repo_programs) and reach no network.
#: An allowlist, never a longer denylist: ``web--browse --browser=<cmd>``,
#: ``send-email --to-cmd=<cmd>`` and ``instaweb --httpd=<cmd>`` each ran a
#: program (2026-11 review), and an alias (``alias.x = !cmd``) is a name no
#: list of git's own commands holds. Network and history rewriting go
#: through the native git tools (git_pull, git_push, git_checkout...).
GIT_SUBCOMMANDS: frozenset[str] = frozenset({
    # reading
    "status", "log", "show", "diff", "blame", "annotate", "shortlog",
    "describe", "rev-parse", "rev-list", "ls-files", "ls-tree", "cat-file",
    "show-ref", "for-each-ref", "name-rev", "merge-base", "diff-tree",
    "diff-index", "diff-files", "whatchanged", "reflog", "grep",
    "check-ignore", "check-attr", "count-objects", "range-diff", "cherry",
    "show-branch", "version", "config",
    # changing the local repository
    "init", "add", "commit", "checkout", "switch", "restore", "branch", "tag",
    "merge", "stash", "mv", "rm", "cherry-pick", "revert", "worktree",
})

#: git subcommands refused outright, with the words of the old denylist
#: (network, credentials, history rewriting, programs it names).
_GIT_BLOCKED = frozenset({
    "push", "credential", "credential-manager",
    "credential-store", "credential-cache",
    "reset", "rebase", "filter-branch", "filter-repo",
    "remote", "submodule",
    # `clone` reaches the network and honours transport helpers (ext::,
    # gitProxy). Cloning belongs to the audited /api/github/repos/clone
    # route (audit F-03).
    "clone", "fetch", "archive", "daemon", "http-backend",
    # They start the merge or diff tool the configuration (or -x/--extcmd)
    # names, whatever the arguments.
    "mergetool", "difftool",
})


def _tar_archive_names(words: list[str]) -> list[str]:
    """The archive names a tar command names (``-f X``, ``-fX``, ``--file=X``, ``-cvf X``)."""
    names: list[str] = []
    i = 0
    while i < len(words):
        word = words[i]
        if word == "--":
            break
        if word.startswith("--"):
            flag, has_value, value = word.partition("=")
            # Exactly: every shorter prefix is also one of --files-from's.
            if flag == "--file":
                if has_value:
                    names.append(value)
                elif i + 1 < len(words):
                    names.append(words[i + 1])
                    i += 1
        elif word.startswith("-") and len(word) > 1:
            letters = word[1:]
            for j, ch in enumerate(letters):
                if ch in _SHORT_VALUES["tar"]:
                    if ch == "f":
                        rest = letters[j + 1:]
                        if rest:
                            names.append(rest)
                        elif i + 1 < len(words):
                            names.append(words[i + 1])
                            i += 1
                    elif not letters[j + 1:]:
                        i += 1  # the value of -C/-I/-T is the next word
                    break
        i += 1
    return names


def _remote_archive_reason(name: str) -> str | None:
    """Why a tar archive name is refused as ``host:path`` (GNU tar's remote form), or None.

    GNU tar reads an archive name holding a colon before any slash as
    ``[user@]host:file`` and reaches that host over rsh/ssh. A Windows drive
    path (``C:\\x.tar``, ``C:/x.tar``) is a local file.
    """
    text = str(name or "")
    if ":" not in text:
        return None
    if (
        os.name == "nt"
        and len(text) >= 3
        and text[0].isalpha()
        and text[1] == ":"
        and text[2] in "\\/"
    ):
        return None
    head = text.split(":", 1)[0]
    if "/" in head or "\\" in head:
        return None
    return (
        f"tar archive '{text}' is in the host:path form, which tar opens on "
        "another machine over rsh/ssh. Name a local archive inside the workspace."
    )


def argument_refusal(binary: str, sub: str, words: list[str], sub_words: list[str]) -> str | None:
    """What shell_exec refuses from a command's options alone, or None.

    *words* are the command's words after the program, *sub* git's or uv's
    subcommand (``split_subcommand``) and *sub_words* the words after it.
    The one answer for the tool (after its approval) and the check before
    the approval card.
    """
    for word in words:
        flag = word.split("=", 1)[0]
        # A long option may be abbreviated (``--open-files=``).
        if matches_blocked(flag, _EXEC_CAPABLE_ARGS.get(binary, ())):
            return (
                f"'{flag}' is not allowed for {binary} — it runs "
                f"another program (or writes/deletes outside the vetted "
                f"argument set) and would bypass the binary allowlist. "
                f"Use python_exec for multi-step work, or a native "
                f"file_*/git_* tool."
            )
    if binary == "tar":
        if words and not words[0].startswith("-"):
            return "tar requires explicit dash options; old-style option clusters are refused."
        for name in _tar_archive_names(words):
            reason = _remote_archive_reason(name)
            if reason:
                return reason
    # uv and git run other programs by subcommand, not by flag (audit
    # 2026-10-26 AUD-085): `uv run python -c ...` and `uv tool run` start an
    # interpreter, and `git config alias.x "!cmd"` then `git x` runs a shell
    # command -- each after one approved card, the class F-03 closed for
    # `find -exec`.
    if binary == "uv" and (
        sub in ("run", "tool")
        or any(w.split("=", 1)[0] == "--with" for w in words)
    ):
        return (
            f"'uv {sub or 'run'}' runs another program and would bypass the "
            "binary allowlist. Use python_exec, or `uv pip`/`uv sync`."
        )
    if binary != "git":
        return None
    if sub == "config" and not any(
        w in ("--get", "--get-all", "--get-regexp", "--list", "-l", "--show-origin")
        for w in sub_words
    ):
        return (
            "git config may only be read here (--get/--list): a written "
            "alias or core.* value runs a program on the next git call."
        )
    if (
        # -O may sit in a cluster (``-iO<cmd>``); a pattern attached to -e
        # that holds an O is refused too (use ``-e PATTERN``).
        (sub == "grep" and any(
            w.startswith("-") and not w.startswith("--") and "O" in w[1:]
            for w in sub_words
        ))
        or (sub == "bisect" and "run" in sub_words)
    ):
        return (
            f"git {sub} with these arguments runs another program "
            "(grep -O, bisect run) and would bypass the binary "
            "allowlist. Use a native git_* tool."
        )
    joined = " ".join(words).lower()
    if (
        sub in _GIT_BLOCKED
        or "config --global" in joined
        or "clean -fd" in joined
        or " push " in f" {joined} "
        or "--force" in joined
        or " -f " in f" {joined} "
    ):
        return (
            f"git subcommand/args not allowed: {' '.join(words)}. "
            "push/force/credential/reset/rebase/global config/clean -fd are blocked."
        )
    if sub and sub not in GIT_SUBCOMMANDS:
        return (
            f"'git {sub}' is not an allowed git subcommand here: shell_exec "
            "runs git's own reading and local commands only (status, log, diff, "
            "show, grep, add, commit, branch, checkout...). Others start "
            "another program or reach the network; use a native git_* tool."
        )
    return None


def short_option_values(binary: str, argument: str) -> list[tuple[str, str]]:
    """Decode attached values; refuse ambiguous tar clusters and old-style flags.

    A short value consumes the rest of its token, just as the binary does.
    For tar, the permitted operation is create/list with explicit dash options;
    extraction, dereferencing and file-list indirection require a native tool.
    """
    if not argument.startswith("-") or argument.startswith("--"):
        return []
    values = _SHORT_VALUES.get(binary, "")
    switches = _TAR_SWITCHES if binary == "tar" else _SHORT_SWITCHES.get(binary)
    if switches is None:
        return []
    for index, flag in enumerate(argument[1:], 1):
        if flag in values:
            return [("-" + flag, argument[index + 1:])]
        if flag not in switches:
            raise ValueError(f"Unsupported {binary} short option '-{flag}'; use explicit options or a native tool")
    return []
