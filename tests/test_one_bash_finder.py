"""The tests find bash in one place, ``tests/_bash.py`` (2026-11-19).

Three test files kept their own finder. On the owner's freshly reset PC the
``bash`` first on the path was WSL's launcher in ``WindowsApps``; the one
finder that skipped only System32's copy handed the engine's scripts to WSL,
and ten tests failed with "No such file or directory". No other test file
may look bash up itself.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_LOOKS_UP_BASH = re.compile(r"""which\(\s*["']bash["']|["']Git["']\s*\)?\s*/\s*["']bin["']\s*/\s*["']bash\.exe""")


def _finders(sources: dict[str, str]) -> list[str]:
    return sorted(name for name, text in sources.items()
                  if name not in _ALLOWED and _LOOKS_UP_BASH.search(text))


#: The finder, and this gate (its planted examples).
_ALLOWED = {"tests/_bash.py", "tests/test_one_bash_finder.py"}


def _test_sources() -> dict[str, str]:
    listed = subprocess.run(["git", "ls-files", "-co", "--exclude-standard", "--", "tests/*.py"], cwd=ROOT,
                            capture_output=True, text=True, check=True).stdout.split()
    return {name: (ROOT / name).read_text(encoding="utf-8") for name in listed if (ROOT / name).is_file()}


def test_no_test_looks_bash_up_itself() -> None:
    assert _finders(_test_sources()) == []


def test_control_a_planted_finder_is_caught() -> None:
    planted = {
        "tests/test_a.py": 'found = shutil.which("bash")\n',
        "tests/test_b.py": 'Path(os.environ["ProgramFiles"]) / "Git" / "bin" / "bash.exe"\n',
        "tests/_bash.py": 'shutil.which("bash")\n',
        "tests/test_c.py": "from tests._bash import find_bash\n",
    }
    assert _finders(planted) == ["tests/test_a.py", "tests/test_b.py"]
