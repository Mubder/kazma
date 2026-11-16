"""shell_exec finds a system program a tool-path folder links to elsewhere.

CI's runner moved to Ubuntu 26.04 on 2026-11-16, where every coreutils
program in /usr/bin is a link into /usr/lib/cargo/bin/coreutils/. The
resolver accepted only a program that resolved inside a tool-path folder, so
shell_exec ran no `ls`, `cat` or `sleep` on 26.04. It now also accepts a
target that root owns and no one else may write, all the way up: a program
only the system could have put there. A link to a file anyone else could
change (the /tmp/evil/git case) is still refused.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from kazma_core.safety.post_hitl import resolve_shell_binary

posix_only = pytest.mark.skipif(os.name == "nt", reason="POSIX ownership and links")


def _system_program() -> Path:
    found = shutil.which("sleep") or shutil.which("true")
    if not found:
        pytest.skip("no sleep or true on PATH")
    real = Path(found).resolve()
    if real.stat().st_uid != 0:
        pytest.skip("this machine's system programs are not root's")
    return real


@posix_only
def test_a_system_program_linked_from_a_tool_folder_resolves(tmp_path):
    real = _system_program()
    tools = tmp_path / "bin"
    tools.mkdir()
    (tools / real.name).symlink_to(real)
    assert resolve_shell_binary(real.name, restricted_path=str(tools)) == str(real)
    # The rule before: the target is outside the tool folder, so refused.
    assert not real.is_relative_to(tools.resolve())


@posix_only
def test_a_link_to_a_file_someone_else_could_change_is_refused(tmp_path):
    if os.geteuid() == 0:
        pytest.skip("as root every file is root's")
    evil = tmp_path / "evil" / "git"
    evil.parent.mkdir()
    evil.write_text("#!/bin/sh\necho owned\n", encoding="utf-8")
    evil.chmod(0o755)
    tools = tmp_path / "bin"
    tools.mkdir()
    (tools / "git").symlink_to(evil)
    assert resolve_shell_binary("git", restricted_path=str(tools)) is None


@posix_only
def test_a_program_inside_the_tool_folder_still_resolves(tmp_path):
    tools = tmp_path / "bin"
    tools.mkdir()
    own = tools / "mytool"
    own.write_text("#!/bin/sh\n", encoding="utf-8")
    own.chmod(0o755)
    assert resolve_shell_binary("mytool", restricted_path=str(tools)) == str(own.resolve())
