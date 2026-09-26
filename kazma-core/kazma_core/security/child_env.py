"""The environment a tool's subprocess gets: the server's, minus its secrets.

``shell_exec`` and ``python_exec`` start their children from a minimal
environment (``safety.post_hitl.restricted_child_env``, ``code_exec``'s own).
The other tools that start a process -- pytest (``run_unit_tests``,
``file_apply_patch_set``'s verify run), ``pip`` / ``npm`` installs, ``ruff``,
git -- handed each child the server's whole environment: the vault key that
decrypts every stored credential, the database URL with its password, the
provider API keys. Every one of those children runs code nobody reviewed: a
repository's ``conftest.py`` and tests, a package's install script, a
clone's git hooks or ``core.fsmonitor``.

Those tools do need the user's environment (git's HOME and credential
helper, SSH_AUTH_SOCK, npm's registry config), so the answer is not the
minimal environment but the full one with every secret taken out:

* every ``KAZMA_*`` variable -- Kazma's own configuration and keys, which no
  child has a use for;
* names that say they hold a credential (``*_API_KEY``, ``*_TOKEN``,
  ``*_SECRET``, ``*_PASSWORD``, ``*_CREDENTIALS``, ``DATABASE_URL``, ...);
* any value carrying a password in a URL (a proxy, a DSN under any name).

An operator who needs one of them in a child (a private npm registry's
``NPM_TOKEN``) names it in ``KAZMA_CHILD_ENV_ALLOW`` (comma-separated). That
variable only ever lets names through; it cannot let a ``KAZMA_*`` one through.

Gate: ``tests/test_child_env.py`` -- every process a tool starts gets its
environment from here or from one of the minimal builders.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping

__all__ = ["ALLOW_ENV", "tool_child_env"]

ALLOW_ENV = "KAZMA_CHILD_ENV_ALLOW"

_SECRET_NAME = re.compile(
    r"(?:^|_)(?:"
    r"API_?KEY|APIKEY|KEY|SECRET|SECRET_KEY|SECRET_ACCESS_KEY|ACCESS_KEY|ACCESS_KEY_ID|"
    r"PRIVATE_KEY|TOKEN|AUTH_TOKEN|SESSION_TOKEN|PAT|PASSWORD|PASSWD|PASS|PWD|"
    r"CREDENTIAL|CREDENTIALS|DSN|DATABASE_URL|COOKIE|WEBHOOK|WEBHOOK_URL"
    r")$"
    r"|^PGPASSWORD$|^PGPASSFILE$"
)
# Not secrets, whatever their names end with.
_NOT_SECRET = frozenset({"PWD", "OLDPWD"})


def _allowed_names() -> frozenset[str]:
    raw = os.environ.get(ALLOW_ENV) or ""
    return frozenset(
        name.strip().upper()
        for name in raw.split(",")
        if name.strip() and not name.strip().upper().startswith("KAZMA_")
    )


def _is_secret_env(name: str, value: str) -> bool:
    """Whether a child process must not inherit ``name=value``."""
    upper = name.upper()
    if upper.startswith("KAZMA_"):
        return True
    if upper in _NOT_SECRET:
        return False
    if _SECRET_NAME.search(upper):
        return True
    from kazma_core.security.url_credentials import mask_url_credentials

    return mask_url_credentials(value) != value


def tool_child_env(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """``os.environ`` without its secrets, plus the caller's *extra* entries.

    *extra* is what the calling tool deliberately hands its child (a git
    identity, a credential header for one push); it is applied last and is
    not filtered.
    """
    allowed = _allowed_names()
    env = {
        name: value
        for name, value in os.environ.items()
        if name.upper() in allowed or not _is_secret_env(name, value)
    }
    if extra:
        env.update({str(k): str(v) for k, v in extra.items()})
    return env
