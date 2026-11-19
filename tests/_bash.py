"""The bash the tests run Kazma's shell scripts with: one answer.

On Windows that is Git for Windows' bash. Any other ``bash.exe`` there is
WSL's launcher, which starts a Linux distro instead of running the script
on this machine: Windows puts it in System32, and since the November 2026
reset of the owner's PC also as an app alias in ``WindowsApps`` ahead of Git
on the path. Three tests kept their own finder and disagreed; one skipped
only System32's copy, so on the fresh machine the engine's script tests
handed their scripts to WSL and failed with "No such file or directory".
Elsewhere it is the ``bash`` on the path.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path


def find_bash() -> str | None:
    if os.name == "nt":
        git_bash = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git" / "bin" / "bash.exe"
        return str(git_bash) if git_bash.is_file() else None
    return shutil.which("bash")
