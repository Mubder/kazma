"""The container keeps ClamAV's signatures current (FA-232, 2026-11).

The image fetches the signatures when it is built, and the Dockerfile said
"the entrypoint refreshes signatures at runtime anyway" -- it did not: a
container scanned with its build day's signatures for as long as it ran, and
since the document readiness check reads their age, every container would
have gone "degraded" a week after its build.

The real entrypoint is run by ``sh`` with a stand-in ``freshclam`` on PATH
that records how it was started: it must start freshclam's daemon before
Kazma, and Kazma must start whether freshclam starts, fails, or is missing.
The entrypoint before the fix is the negative control. A real run (Debian's
ClamAV, the daemon as uid 10001) was checked in a throwaway container on
2026-11-02; the Dockerfile half (the folders handed to that user) is read
here from the file.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = ROOT / "scripts" / "docker-entrypoint.sh"

#: The entrypoint before the fix (the negative control).
OLD_ENTRYPOINT = """#!/bin/sh
set -e
if [ "${KAZMA_AUTO_MIGRATE:-0}" = "1" ] && [ -n "${KAZMA_DATABASE_URL:-}" ]; then
  if [ -f /app/kazma-data/settings.db ] || [ -f /app/kazma-data/chat_sessions.db ]; then
    python /app/scripts/migrate_sqlite_to_postgres.py --data-dir /app/kazma-data
  fi
fi
exec "$@"
"""

SH = shutil.which("sh")
pytestmark = pytest.mark.skipif(SH is None, reason="needs a POSIX sh")


def _stand_in(folder: Path, exit_code: int) -> Path:
    """A ``freshclam`` that writes its arguments to a file and exits *exit_code*."""
    folder.mkdir()
    record = folder / "freshclam.args"
    script = folder / "freshclam"
    script.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$*" >> "{record.as_posix()}"\n'
        f"exit {exit_code}\n",
        encoding="utf-8",
        newline="\n",
    )
    script.chmod(0o755)
    return record


def _run(entrypoint: Path, bin_dir: Path) -> subprocess.CompletedProcess[str]:
    """Run *entrypoint* with a PATH of *bin_dir* and ``sh``'s own folder only:
    a real freshclam on the machine's PATH must never be the one started (on
    Windows its ``--daemon`` does not detach, and the run would never end)."""
    env = dict(os.environ)
    env["PATH"] = str(bin_dir) + os.pathsep + str(Path(SH).parent)
    return subprocess.run(
        [SH, entrypoint.as_posix(), "sh", "-c", "echo KAZMA_STARTED"],
        capture_output=True, text=True, env=env, timeout=60,
    )


def _entrypoint_copy(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "entrypoint.sh"
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def test_the_entrypoint_starts_freshclams_daemon_before_kazma(tmp_path):
    record = _stand_in(tmp_path / "bin", exit_code=0)
    done = _run(ENTRYPOINT, tmp_path / "bin")
    assert done.returncode == 0, done.stderr
    assert "KAZMA_STARTED" in done.stdout
    calls = record.read_text(encoding="utf-8").split("\n")
    assert any("--daemon" in c and "--checks=12" in c for c in calls), calls


def test_kazma_starts_when_freshclam_fails_and_says_so(tmp_path):
    record = _stand_in(tmp_path / "bin", exit_code=1)
    done = _run(ENTRYPOINT, tmp_path / "bin")
    assert done.returncode == 0 and "KAZMA_STARTED" in done.stdout
    assert record.exists()
    assert "freshclam did not start" in done.stderr


def test_kazma_starts_without_freshclam(tmp_path):
    empty = tmp_path / "bin"
    empty.mkdir()
    if any((Path(SH).parent / name).exists() for name in ("freshclam", "freshclam.exe")):
        pytest.skip("freshclam sits beside sh on this machine")
    done = _run(ENTRYPOINT, empty)
    assert done.returncode == 0 and "KAZMA_STARTED" in done.stdout


def test_negative_control_the_old_entrypoint_never_refreshed(tmp_path):
    record = _stand_in(tmp_path / "bin", exit_code=0)
    done = _run(_entrypoint_copy(tmp_path, OLD_ENTRYPOINT), tmp_path / "bin")
    assert done.returncode == 0 and "KAZMA_STARTED" in done.stdout
    assert not record.exists(), "the old entrypoint started freshclam after all"


def test_the_image_hands_the_signature_folders_to_kazmas_user():
    """freshclam runs as the user Kazma runs as (``USER kazma``), so that user
    must own the database and its log, or the daemon cannot write them."""
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    owned = next(line for line in text.splitlines() if "chown -R kazma:kazma" in line)
    assert "/var/lib/clamav" in owned and "/var/log/clamav" in owned, owned
    assert "USER kazma" in text
    assert "refreshes signatures at runtime anyway" not in text
