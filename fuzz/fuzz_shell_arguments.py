"""Fuzz shell_exec's command reading: any command is read or refused, cleanly.

``shell_exec`` splits a command the model wrote (``split_command_line``),
finds git's and uv's subcommand (``split_subcommand``) and every value it
carries (``path_candidates``) before anything runs. Whatever the text, each
either answers with words or refuses with ``ValueError`` -- the refusal the
tool turns into its answer. Any other exception would end the tool call as
"effects unknown".

Run under Atheris: ``python fuzz/fuzz_shell_arguments.py -max_total_time=60``.
"""

from __future__ import annotations

import sys

TOKENS = (
    "git ", "uv ", "grep ", "find ", "tar ", "cat ", "-C ", "--work-tree=", "-c ",
    "checkout ", "-exec ", "+", ";", "|", "&&", ">", "<", "\"", "'", "\\", " ",
    "\t", "\n", "--", "-", "=", "a", ".", "/", "~", "$", "*", "{a,b}", "\x00",
)
SEEDS = (b"git -C . status", b"grep -r 'a b' .", b"tar -cf x.tar dir", b"echo \"unclosed")


def check(data: bytes) -> None:
    from kazma_core.safety.shell_arguments import program_name, split_command_line, split_subcommand
    from kazma_core.safety.shell_paths import path_candidates

    text = data.decode("utf-8", errors="replace")
    try:
        words = split_command_line(text)
    except ValueError:
        return
    assert isinstance(words, list) and all(isinstance(w, str) for w in words)
    if not words:
        return
    binary = program_name(words[0])
    try:
        index, sub = split_subcommand(binary, words[1:])
        assert isinstance(index, int) and isinstance(sub, str)
    except ValueError:
        pass
    try:
        found = path_candidates(binary, words[1:])
    except ValueError:
        return
    assert all(isinstance(w, str) and isinstance(v, str) for w, v in found)


def main() -> None:
    import atheris

    with atheris.instrument_imports():
        import kazma_core.safety.shell_arguments  # noqa: F401
        import kazma_core.safety.shell_paths  # noqa: F401
    atheris.Setup(sys.argv, check)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
