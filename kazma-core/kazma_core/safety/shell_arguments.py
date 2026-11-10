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
