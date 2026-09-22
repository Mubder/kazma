"""The one ``.env`` loader. Entry points call it; importing a module never does.

``cost_breaker`` used to run a bare ``load_dotenv()`` at import time, and
``kazma_core/__init__`` imports ``cost_breaker``, so the first ``import
kazma_core...`` in ANY process loaded a ``.env`` found by walking up from the
*package's* location — not the directory you launched from (audit 2026-09-22):

* The document sandbox scrubs its environment to a handful of variables before
  it parses an untrusted document. The parser worker imports ``kazma_core``,
  so the import put ``KAZMA_VAULT_KEY``, ``KAZMA_SECRET`` and every provider
  key straight back into the process parsing the hostile file.
* Under an editable install the package location is whichever clone was
  installed, so ``kazma migrate export`` — which reads ``KAZMA_VAULT_KEY`` from
  ``os.environ`` — could pair this installation's ``vault.db`` with another
  clone's key, the #1 silent migration breakage (AGENTS.md §18A).

So loading ``.env`` is now something a process decides to do, at its entry
point, from an explicit ladder. A sandboxed worker simply never calls this.
``tests/test_static_gates.py`` keeps ``load_dotenv`` out of every other product
module and keeps import-time environment writes on an allowlist.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = ["env_file_ladder", "load_env_files"]


def env_file_ladder() -> list[Path]:
    """``.env`` files to load, **lowest precedence first**.

    Replaces a ladder that hardcoded one developer's absolute path
    (``C:/Users/balfa/kazma``) as the default "user workspace", and loaded
    it *last with override* — so on that machine a clone at any other path
    silently inherited a different clone's secrets, database DSN included,
    and the repo you actually launched lost to one you did not.

    Three defects, all fixed here:

    1. No guessed paths. Nothing outside this installation is read unless
       the operator names it.
    2. ``KAZMA_WORKSPACE`` is the *agent's code workspace* (which repo it
       edits) and is switchable from the UI. Deriving config location from
       it meant "Switch Repo" could change which ``.env`` is authoritative.
       Config location is now its own variable, ``KAZMA_ENV_FILE``.
    3. Most specific wins last. The CWD you launched from beats the
       per-install default; an explicitly named file beats both.
    """
    from kazma_core.paths import user_home

    ladder: list[Path] = []

    # Per-install defaults (`<kazma home>/.env`), lowest precedence.
    try:
        ladder.append(Path(user_home()) / ".env")
    except Exception:
        pass

    # The directory you launched from — the repo actually being run.
    ladder.append(Path.cwd() / ".env")

    # Explicit override, highest precedence. Use this to point a dev clone
    # at a shared env file on purpose, instead of it happening by accident.
    explicit = (os.environ.get("KAZMA_ENV_FILE") or "").strip()
    if explicit:
        ladder.append(Path(explicit).expanduser())

    return ladder


def load_env_files(*, override: bool = True) -> list[str]:
    """Load the ladder into ``os.environ``; return the files loaded.

    ``override=True`` (the server and CLI) lets a file replace a variable that
    is already set: a stale empty shell value must not shadow a real one. The
    files then load lowest-first, so each overwrites the one before it.

    ``override=False`` (the TUI, a client of the server) keeps whatever the
    process already has. The files then load highest-first, so the more
    specific file still wins over the less specific one.
    """
    from dotenv import load_dotenv

    ladder = env_file_ladder()
    if not override:
        ladder.reverse()
    loaded: list[str] = []
    seen: set[str] = set()
    for path in ladder:
        try:
            if not path.is_file():
                continue
            key = str(path.resolve()).lower()
            if key in seen:
                continue
            seen.add(key)
            load_dotenv(dotenv_path=path, override=override)
            loaded.append(str(path))
        except OSError:
            continue

    if loaded:
        # Name the files. A .env loaded from somewhere unexpected used to be
        # completely invisible, which is how the cross-clone leak above went
        # unnoticed. ASCII arrow: this line is read on the Windows console,
        # where a U+2192 renders as an escape and buries the useful part.
        order = "lowest->highest" if override else "highest->lowest"
        logger.info("[env] Loaded (%s precedence): %s", order, " -> ".join(loaded))
    else:
        logger.debug("[env] No .env file found on the ladder")
    return loaded
