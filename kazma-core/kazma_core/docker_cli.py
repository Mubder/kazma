"""Find the docker CLI even when it is not on PATH.

Live 2026-09-25: Docker Desktop updated itself to 4.91.0 (``docker.exe``
rewritten at 01:35, reboot at 01:38) and the new version left
``C:\\Program Files\\Docker\\Docker\\resources\\bin`` off the system PATH.
Everything Kazma starts from the registry environment -- the ``KazmaAgent``
scheduled task, the guard, the server -- then failed ``shutil.which("docker")``,
and the six-hourly Postgres dump, which runs ``pg_dump`` inside the database
container through ``docker exec``, stopped: "native_pg_backup produced no
dump". The CLI was installed and the container was running the whole time;
only the lookup was wrong.

Lookup order: ``KAZMA_DOCKER_BIN`` (an explicit path), then PATH, then the
places Docker installs itself on each OS. A CLI found off PATH is used, and
said once, so the operator can put it back on PATH instead of depending on
the fallback without knowing it. A reload is then enough: the server adopts
PATH entries the OS settings gained since its supervisor started
(:mod:`kazma_core.path_refresh`).
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = ["find_docker_cli"]

_ENV = "KAZMA_DOCKER_BIN"
_reported: set[str] = set()
_reported_lock = threading.Lock()


def _candidates() -> list[Path]:
    """Where Docker installs its CLI, per OS. Order is preference."""
    if os.name == "nt":
        roots = [os.environ.get(v) for v in ("ProgramFiles", "ProgramW6432")] + [r"C:\Program Files"]
        out = [Path(r) / "Docker" / "Docker" / "resources" / "bin" / "docker.exe" for r in roots if r]
        data = os.environ.get("ProgramData") or r"C:\ProgramData"
        out.append(Path(data) / "DockerDesktop" / "version-bin" / "docker.exe")
        return list(dict.fromkeys(out))
    out = [Path("/usr/bin/docker"), Path("/usr/local/bin/docker"), Path("/snap/bin/docker")]
    if sys.platform == "darwin":
        out = [
            Path("/usr/local/bin/docker"),
            Path("/opt/homebrew/bin/docker"),
            Path("/Applications/Docker.app/Contents/Resources/bin/docker"),
            Path.home() / ".docker" / "bin" / "docker",
        ]
    return out


def _say_once(key: str, message: str, *args: object) -> None:
    with _reported_lock:
        if key in _reported:
            return
        _reported.add(key)
    logger.warning(message, *args)


def find_docker_cli() -> str | None:
    """Absolute path of a usable docker CLI, or ``None``."""
    explicit = (os.environ.get(_ENV) or "").strip().strip('"')
    if explicit:
        if Path(explicit).is_file():
            return explicit
        _say_once(
            f"env:{explicit}",
            "[docker] %s=%s does not exist -- falling back to PATH and the "
            "standard install locations", _ENV, explicit,
        )
    on_path = shutil.which("docker")
    if on_path:
        return on_path
    for candidate in _candidates():
        if candidate.is_file():
            _say_once(
                f"found:{candidate}",
                "[docker] the docker CLI is not on PATH; using %s. Docker Desktop "
                "updates can drop it from PATH -- add its folder back and reload "
                "Kazma (the server re-reads PATH from the OS settings), or set %s.",
                candidate, _ENV,
            )
            return str(candidate)
    return None
