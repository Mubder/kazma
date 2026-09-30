"""An MCP server's credentials live in the vault; its configuration holds pointers.

An MCP server gets its credentials from its configuration: an environment
variable (``BRAVE_API_KEY``), an ``auth`` value (an env var, a command-line
argument, a header), a bearer token, a header, a flag in its command
(``--api-key=...``, as Stripe documents it), a password in its URL. Until
2026-09-30 each was written as typed into ``kazma.yaml`` -- a tracked file in
the install's checkout -- and into the settings database, the MCP list APIs
returned them, and an argument-style key was logged with the command that
started the server.

* :func:`externalize` -- every write in :mod:`kazma_core.mcp_servers_store`
  runs it: a secret goes to the vault (``cfg:mcp.servers.<server>.<field>``,
  install-scoped because MCP servers connect at boot with no tenant) and the
  configuration keeps a ``vault://`` pointer. A masked value posted back
  (``****``) or an empty one keeps what is stored.
* :func:`resolve` -- both MCP clients run it right before they start or
  connect a server. A pointer the vault cannot answer stops that server with
  an error naming the field; a pointer is never handed on as the value.
* :func:`masked` -- what an API or a page shows.
* :func:`forget` -- a deleted server's secrets leave the vault.
* :func:`redacted_argv` / :func:`redacted_url` -- as a log line may print them.

A secret is an ``env`` entry whose name reads as a credential (the rule every
setting follows, :func:`~kazma_core.config_store.is_sensitive_config_key`) or
whose value is a URL with a password; ``auth.value`` and ``auth.token``; a
header whose name reads as one; the value of a command flag whose name reads
as one; a password in a URL. ``tests/test_mcp_secrets.py``.
"""

from __future__ import annotations

import copy
import logging
from typing import Any

__all__ = [
    "MASK",
    "MCPSecretUnavailable",
    "externalize",
    "forget",
    "has_plaintext_secret",
    "masked",
    "redacted_argv",
    "redacted_url",
    "resolve",
    "secret_values",
]

logger = logging.getLogger(__name__)

MASK = "****"
# Credential names the settings rule does not read as one on their own.
_EXTRA_SECRET_NAMES = frozenset({"key", "pat", "auth", "authorization", "proxy_authorization", "cookie"})
# The last part of a path to a ``--flag=value`` argument: its value.
_FLAG_VALUE = "="

FieldPath = tuple[str | int, ...]

_warned_no_vault = False


class MCPSecretUnavailable(RuntimeError):
    """A server's secret is a vault pointer the vault could not answer."""


def _secret_name(name: str) -> bool:
    from kazma_core.config_store import is_sensitive_config_key

    lowered = str(name).strip().lower().replace("-", "_")
    return bool(lowered) and (lowered in _EXTRA_SECRET_NAMES or is_sensitive_config_key(lowered))


def _url_has_password(value: Any) -> bool:
    from kazma_core.security.url_credentials import url_has_credentials

    return url_has_credentials(value)


def _is_ref(value: Any) -> bool:
    from kazma_core.config_store import is_vault_ref

    return is_vault_ref(value)


def _command_paths(command: list[Any]) -> list[FieldPath]:
    paths: list[FieldPath] = []
    value_next = False
    for i, arg in enumerate(command):
        if not isinstance(arg, str):
            value_next = False
            continue
        if value_next:
            value_next = False
            if not arg.startswith("-"):
                paths.append(("command", i))
                continue
        if arg.startswith("-"):
            flag, sep, _value = arg.partition("=")
            if _secret_name(flag.lstrip("-")):
                if sep:
                    paths.append(("command", i, _FLAG_VALUE))
                else:
                    value_next = True
        elif _is_ref(arg) or _url_has_password(arg):
            paths.append(("command", i))
    return paths


def _secret_paths(server: dict[str, Any]) -> list[FieldPath]:
    """Where *server* keeps a secret, or a pointer to one."""
    paths: list[FieldPath] = []
    env = server.get("env")
    if isinstance(env, dict):
        for key, value in env.items():
            if isinstance(key, str) and (_secret_name(key) or _url_has_password(value) or _is_ref(value)):
                paths.append(("env", key))
    auth = server.get("auth")
    if isinstance(auth, dict):
        paths.extend(("auth", field) for field in ("value", "token") if field in auth)
    headers = server.get("headers")
    if isinstance(headers, dict):
        for key, value in headers.items():
            if isinstance(key, str) and (_secret_name(key) or _is_ref(value)):
                paths.append(("headers", key))
    command = server.get("command")
    if isinstance(command, list):
        paths.extend(_command_paths(command))
    if _url_has_password(server.get("url")) or _is_ref(server.get("url")):
        paths.append(("url",))
    return paths


def _get(node: Any, path: FieldPath) -> Any:
    for part in path:
        if part == _FLAG_VALUE:
            return node.partition("=")[2] if isinstance(node, str) else None
        if isinstance(part, int):
            if not isinstance(node, list) or part >= len(node):
                return None
            node = node[part]
        else:
            if not isinstance(node, dict):
                return None
            node = node.get(part)
    return node


def _set(root: dict[str, Any], path: FieldPath, value: Any) -> None:
    if path[-1] == _FLAG_VALUE:
        container = _get(root, path[:-2])
        flag = str(container[path[-2]]).partition("=")[0]
        container[path[-2]] = f"{flag}={value}"
        return
    node: Any = root
    for part in path[:-1]:
        node = node[part]
    node[path[-1]] = value


def _setting_key(server_name: str, path: FieldPath) -> str:
    """The settings name a server's secret is kept under (``cfg:`` + this in the vault)."""
    return "mcp.servers." + server_name + "." + ".".join(str(p) for p in path if p != _FLAG_VALUE)


def _own_ref(server_name: str, path: FieldPath) -> str:
    from kazma_core.config_store import _vault_ref_for_key

    return _vault_ref_for_key(_setting_key(server_name, path))


def _vault_name_of(ref: str) -> str:
    from kazma_core.config_store import _VAULT_REF_PREFIX

    return ref[len(_VAULT_REF_PREFIX):]


def _read_ref(ref: str, vault: Any) -> str | None:
    from kazma_core.security.vault import retrieve_scoped

    return retrieve_scoped(_vault_name_of(ref), vault)


def _vault() -> Any:
    from kazma_core.config_store import _try_get_vault

    return _try_get_vault()


def externalize(server: dict[str, Any], previous: dict[str, Any] | None = None) -> dict[str, Any]:
    """*server* with its secrets in the vault and pointers in their place.

    *previous* is what is stored for the same server now (pointers
    unresolved): a masked (``****``) or empty secret keeps it. Without a vault
    a secret stays as it is, with one warning; a pointer to another server's
    secret (a copied configuration) is copied to this server's own name.
    """
    from kazma_core.config_store import _store_config_secret, _vault_secret_name, is_masked_secret_placeholder

    global _warned_no_vault
    out = copy.deepcopy(server)
    name = str(out.get("name") or "").strip()
    if not name:
        return out
    vault: Any = None
    for path in _secret_paths(out):
        value = _get(out, path)
        own = _own_ref(name, path)
        if value == own:
            continue
        if not isinstance(value, str) or not value or is_masked_secret_placeholder(value):
            kept = _get(previous, path) if previous else None
            if not (isinstance(kept, str) and kept and not is_masked_secret_placeholder(kept)):
                _set(out, path, "")  # never store the stars
                continue
            value = kept
            if value == own:
                _set(out, path, own)
                continue
        vault = vault if vault is not None else _vault()
        if vault is None:
            _set(out, path, value)
            if not _warned_no_vault:
                _warned_no_vault = True
                logger.warning(
                    "[MCP] The vault is unavailable, so MCP server secrets stay in "
                    "kazma.yaml and the settings database as typed. Set "
                    "KAZMA_VAULT_KEY to keep them in the vault."
                )
            continue
        if _is_ref(value):
            secret = _read_ref(value, vault)
            if secret is None:
                _set(out, path, value)  # connecting names the missing field
                continue
            value = secret
        _store_config_secret(vault, _vault_secret_name(_setting_key(name, path)), str(value))
        _set(out, path, own)
    return out


def has_plaintext_secret(server: dict[str, Any]) -> bool:
    """True when *server* holds a secret as typed rather than as a pointer."""
    from kazma_core.config_store import is_masked_secret_placeholder

    for path in _secret_paths(server):
        value = _get(server, path)
        if isinstance(value, str) and value and not _is_ref(value) and not is_masked_secret_placeholder(value):
            return True
    return False


def resolve(server: dict[str, Any]) -> dict[str, Any]:
    """*server* with every vault pointer replaced by its secret.

    Raises :class:`MCPSecretUnavailable`, naming the server and the field,
    when the vault cannot answer. A server with no pointer comes back as it is.
    """
    refs = [path for path in _secret_paths(server) if _is_ref(_get(server, path))]
    if not refs:
        return server
    out = copy.deepcopy(server)
    vault = _vault()
    name = str(out.get("name") or "unnamed")
    for path in refs:
        secret = _read_ref(_get(out, path), vault) if vault is not None else None
        if secret is None:
            field = ".".join(str(p) for p in path if p != _FLAG_VALUE)
            raise MCPSecretUnavailable(
                f"MCP server '{name}': its {field} is kept in Kazma's vault, which "
                "could not answer (is KAZMA_VAULT_KEY set for this server?)"
            )
        _set(out, path, secret)
    return out


def secret_values(server: dict[str, Any]) -> tuple[str, ...]:
    """The secret values a resolved *server* holds (what a log line must not print)."""
    values = (_get(server, path) for path in _secret_paths(server))
    return tuple(v for v in values if isinstance(v, str) and v and not _is_ref(v))


def masked(server: dict[str, Any]) -> dict[str, Any]:
    """*server* as a page or an API shows it: every secret ``****``."""
    from kazma_core.security.url_credentials import mask_url_credentials

    paths = _secret_paths(server)
    if not paths:
        return server
    out = copy.deepcopy(server)
    for path in paths:
        value = _get(out, path)
        if not value:
            continue
        if isinstance(value, str) and not _is_ref(value) and _url_has_password(value):
            _set(out, path, mask_url_credentials(value))
        else:
            _set(out, path, MASK)
    return out


def forget(server: dict[str, Any]) -> int:
    """Remove *server*'s own secrets from the vault; the number removed."""
    from kazma_core.config_store import _vault_secret_name

    name = str(server.get("name") or "").strip()
    vault = _vault() if name else None
    if vault is None:
        return 0
    removed = 0
    for path in _secret_paths(server):
        if _get(server, path) == _own_ref(name, path):
            try:
                removed += bool(vault.delete(_vault_secret_name(_setting_key(name, path))))
            except Exception:  # noqa: BLE001 -- a stale secret is not worth failing a delete
                logger.warning(
                    "[MCP] Could not remove a secret of deleted server '%s' from the vault",
                    name, exc_info=True,
                )
    return removed


def redacted_url(url: Any) -> Any:
    """*url* with any password masked."""
    from kazma_core.security.url_credentials import mask_url_credentials

    return mask_url_credentials(url)


def redacted_argv(argv: list[Any], secrets: tuple[str, ...] = ()) -> list[str]:
    """*argv* as a log line may print it.

    Masks each of *secrets* wherever it appears, the value of a flag named
    like a credential (``--api-key=...``, ``--token ...``), and URL passwords.
    """
    out: list[str] = []
    known = tuple(s for s in secrets if s)
    hidden = {path[1] for path in _command_paths([str(a) for a in argv]) if len(path) == 2}
    for i, raw in enumerate(argv):
        arg = str(raw)
        if i in hidden:
            out.append(MASK)
            continue
        for secret in known:
            arg = arg.replace(secret, MASK)
        if arg.startswith("-"):
            flag, sep, _value = arg.partition("=")
            if sep and _secret_name(flag.lstrip("-")):
                arg = f"{flag}={MASK}"
        out.append(str(redacted_url(arg)))
    return out
