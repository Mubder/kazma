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
import re
from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = ["CHAT_TOKEN_VARIABLES", "env_credential", "env_file_ladder",
           "load_env_files", "persist_generated_secret"]

#: The chat apps' token variables: read only through :func:`env_credential`
#: (``tests/test_env_placeholders.py``).
CHAT_TOKEN_VARIABLES = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_TOKEN", "DISCORD_BOT_TOKEN",
                        "SLACK_BOT_TOKEN", "SLACK_APP_TOKEN")

#: A value that is plainly an example's placeholder, never a credential.
_PLACEHOLDER = re.compile(r"(?i)^(your[-_].*|.*[-_]here|<[^>]*>|changeme|placeholder|x{4,})$")
_PLACEHOLDERS_SAID: set[str] = set()


def env_credential(name: str) -> str:
    """The credential variable *name* from the environment, stripped; "" when
    unset or when it holds an example's placeholder (said once, at INFO).

    setup copies ``.env.example`` to ``.env``, and the example shipped
    ``TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here``: every fresh install
    started the Telegram adapter with it and logged "Bot token validation
    failed" at every start (the drill VM, 2026-11-07). The example is fixed;
    installs made from it already hold the placeholder."""
    value = (os.environ.get(name) or "").strip()
    if value and _PLACEHOLDER.match(value):
        if name not in _PLACEHOLDERS_SAID:
            _PLACEHOLDERS_SAID.add(name)
            logger.info("[env] %s holds an example placeholder, not a credential: treated as not set", name)
        return ""
    return value


def env_file_ladder(*, include_cwd: bool = True) -> list[Path]:
    """``.env`` files to load, **lowest precedence first**.

    ``include_cwd=False`` is for commands that other programs start in their
    own folders (``kazma mcp`` from an editor, ``kazma ask`` inside a
    workspace): their launch folder's ``.env`` is that folder's, and is not
    read; the checkout this package runs from (``paths.installed_project_root``,
    which ``kazma mcp`` also pins as its project root) stands in for it (audit
    2026-10-26 AUD-084). Loaded with override, a ``.env`` in a cloned
    repository replaced the operator's real environment --
    ``KAZMA_MCP_ALLOW_UNGATED=1``, ``KAZMA_HITL_CANONICAL_FLOOR=0``,
    ``KAZMA_DATA_DIR`` -- and the danger tools were served with no approval
    path. Every other command keeps the launch folder (rule 3 below): its
    data directory is found from there too, and a key from elsewhere would
    not open that vault.

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
    from kazma_core.paths import installed_project_root, user_home

    ladder: list[Path] = []

    # Per-install defaults (`<kazma home>/.env`), lowest precedence.
    try:
        ladder.append(Path(user_home()) / ".env")
    except Exception:
        pass

    if include_cwd:
        # The directory you launched from -- the repo actually being run.
        ladder.append(Path.cwd() / ".env")
    else:
        # Started in someone else's folder: this installation's checkout.
        install = installed_project_root()
        if install is not None:
            ladder.append(install / ".env")

    # Explicit override, highest precedence. Use this to point a dev clone
    # at a shared env file on purpose, instead of it happening by accident.
    explicit = (os.environ.get("KAZMA_ENV_FILE") or "").strip()
    if explicit:
        ladder.append(Path(explicit).expanduser())

    return ladder


def _install_env_file() -> Path:
    """The env file a first boot keeps the keys it generates in.

    The most specific file of the server's ladder that is Kazma's own
    (``store_registry.is_kazma_secret_file``: the file ``KAZMA_ENV_FILE``
    names, a ``.env`` directly in the install, the project root or Kazma's
    home): an existing one first, else the most specific to create. Started
    in the install folder, that is the ``.env`` beside it, as before. A
    server started anywhere else wrote ``Path(".env")`` -- the vault key in
    whatever folder it was launched from (audit 2026-11-10); now it writes
    ``<kazma home>/.env``, which the ladder reads at every boot.
    """
    from kazma_core.paths import user_home
    from kazma_core.store_registry import is_kazma_secret_file

    own = [path for path in reversed(env_file_ladder()) if is_kazma_secret_file(path)]
    for path in own:
        if path.is_file():
            return path
    return own[0] if own else Path(user_home()) / ".env"


def persist_generated_secret(name: str, value: str, *, path: Path | None = None) -> Path:
    """Keep ``name=value`` in :func:`_install_env_file` (or *path*); returns the file.

    The file is made empty and restricted to this account
    (``security.file_access.restrict_to_owner``) BEFORE the value is written;
    an existing file is restricted first too. A restriction that fails is a
    WARNING and the value is still written: a vault key that is not kept
    leaves every stored secret unreadable after a restart. An existing
    ``name=`` line (or its commented example) is replaced, else one is
    appended. Raises ``OSError`` when the file cannot be written. *path* is
    for setup, which keeps a key in the ``.env`` docker compose reads
    (``security.searxng_secret``).
    """
    from kazma_core.security.file_access import restrict_to_owner

    path = Path(path) if path is not None else _install_env_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
    if not restrict_to_owner(path):
        logger.warning("[env] could not restrict %s to this account; %s is kept there anyway",
                       path, name)
    lines = path.read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith((f"{name}=", f"# {name}=")):
            lines[i] = f"{name}={value}"
            break
    else:
        lines.append(f"{name}={value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def load_env_files(*, override: bool = True, include_cwd: bool = True) -> list[str]:
    """Load the ladder into ``os.environ``; return the files loaded.

    ``override=True`` (the server and CLI) lets a file replace a variable that
    is already set: a stale empty shell value must not shadow a real one. The
    files then load lowest-first, so each overwrites the one before it.

    ``override=False`` (the TUI, a client of the server) keeps whatever the
    process already has. The files then load highest-first, so the more
    specific file still wins over the less specific one.
    """
    from dotenv import load_dotenv

    ladder = env_file_ladder(include_cwd=include_cwd)
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
