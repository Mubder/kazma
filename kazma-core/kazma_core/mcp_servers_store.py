"""Single source of truth for MCP server configuration.

Historically two independent stores drifted apart:

* ``kazma.yaml`` ``mcp.servers`` — written by ``/mcp`` Add Server
  (``KazmaAgent.add_mcp_server`` → ``_persist_mcp_servers``)
* ConfigStore key ``mcp.servers`` — written by Settings
  (``MCPSettingsService``)

The Settings Test button only read ConfigStore, so servers added from
``/mcp`` reported "Server not found". Agent connect merged both, but
Settings list/test/toggle did not.

This module is the **only** place that reads/writes either store. Readers
merge kazma.yaml, the agent's in-memory copy of it and the settings store
(the settings store wins on a name conflict: runtime UI edits beat the seed).

**The running server never writes kazma.yaml** (2026-10-02). It is a
tracked file in the install's checkout: every MCP change from a page left
the checkout modified, and a later ``git pull`` of any change to kazma.yaml
refused to run. Writes go to the settings store (and the agent's in-memory
copy). A server removed from a page that kazma.yaml still lists is recorded
in ``mcp.removed_servers`` and left out of the merge; adding it again takes
it off. A server added to kazma.yaml by hand still appears. The one write to
kazma.yaml left is :func:`move_plaintext_secrets` replacing secrets typed
into it with vault pointers.

Every write goes through :func:`_write_everywhere`, which first moves each
server's secrets into the vault and leaves ``vault://`` pointers
(:mod:`kazma_core.mcp.secrets`); until 2026-09-30 they were written as typed
into ``kazma.yaml`` and the settings database. A deleted server's secrets
leave the vault. The MCP clients resolve the pointers when they connect.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

__all__ = [
    "CONFIG_KEY",
    "delete_mcp_server",
    "list_mcp_servers",
    "move_plaintext_secrets",
    "persist_mcp_yaml",
    "server_enabled",
    "servers_with_plaintext_secrets",
    "set_mcp_server_enabled",
    "upsert_mcp_server",
]

logger = logging.getLogger(__name__)

CONFIG_KEY = "mcp.servers"
#: Servers removed from a page that kazma.yaml still lists (module docstring).
REMOVED_KEY = "mcp.removed_servers"
_DEFAULT_YAML = "kazma.yaml"


# ── ConfigStore helpers ───────────────────────────────────────────────────


def _scoped(key: str) -> str:
    """ConfigStore *key* — tenant-scoped when multi-user/production isolation is on."""
    try:
        from kazma_core.tenant_isolation import multi_user_or_production, tenant_key

        if multi_user_or_production():
            return tenant_key(key)
    except Exception:
        pass
    return key


def _config_key() -> str:
    return _scoped(CONFIG_KEY)


def _normalize_list(raw: Any) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, dict) and item.get("name"):
            out.append(dict(item))
    return out


def _cs_read(key: str) -> Any:
    try:
        from kazma_core.config_store import get_config_store

        return get_config_store().get(key, None)
    except Exception as exc:
        logger.debug("[mcp_servers_store] ConfigStore read failed: %s", exc)
        return None


def _cs_get() -> list[dict[str, Any]]:
    key = _config_key()
    servers = _normalize_list(_cs_read(key))
    # Legacy: if tenant key empty, also merge global mcp.servers once
    if key != CONFIG_KEY and not servers:
        servers = _normalize_list(_cs_read(CONFIG_KEY))
    return servers


def _removed_names() -> set[str]:
    raw = _cs_read(_scoped(REMOVED_KEY))
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return set()
    return {str(n) for n in raw if isinstance(n, str) and n} if isinstance(raw, list) else set()


def _cs_set(servers: list[dict[str, Any]], removed: set[str] | None = None) -> None:
    """The server list, and the removed names when they change, in one write."""
    items: list[tuple[str, Any, str]] = [(_config_key(), json.dumps(servers, ensure_ascii=False), "mcp")]
    if removed is not None:
        items.append((_scoped(REMOVED_KEY), json.dumps(sorted(removed), ensure_ascii=False), "mcp"))
    try:
        from kazma_core.config_store import get_config_store

        get_config_store().batch_set(items)
    except Exception as exc:
        logger.warning("[mcp_servers_store] ConfigStore write failed: %s", exc)
        raise


# ── YAML helpers ──────────────────────────────────────────────────────────


def _resolve_yaml_path(yaml_path: str | Path | None) -> Path:
    if yaml_path is not None:
        return Path(yaml_path)
    try:
        from kazma_core.agent_runner import CONFIG_FILE

        return Path(CONFIG_FILE)
    except Exception:
        return Path(_DEFAULT_YAML)


def _read_yaml_servers(yaml_path: str | Path | None = None) -> list[dict[str, Any]]:
    path = _resolve_yaml_path(yaml_path)
    if not path.is_file():
        return []
    try:
        import yaml

        with open(path, encoding="utf-8") as f:
            on_disk = yaml.safe_load(f) or {}
        return _normalize_list((on_disk.get("mcp") or {}).get("servers"))
    except Exception as exc:
        logger.debug("[mcp_servers_store] yaml read failed: %s", exc)
        return []


def _splice_yaml_section(text: str, section: str, dumped_block: str) -> str:
    """Replace one top-level mapping ``section:`` with ``dumped_block``.

    ``yaml.safe_dump`` re-emits the WHOLE document and destroys every
    comment in it — round-tripping the operator's annotated kazma.yaml
    through it stripped all documentation comments (2026-08-26 audit
    follow-through). Splicing replaces only the target section's span and
    preserves every other byte, comments included.
    """
    import re as _re

    start_re = _re.compile(rf"^{_re.escape(section)}:[ \t]*(#.*)?$", _re.M)
    block = dumped_block.rstrip("\n") + "\n"
    m = start_re.search(text)
    if m is None:
        base = text.rstrip("\n")
        return (base + "\n\n" if base else "") + block
    rest = text[m.end():]
    # The section spans until the next top-level key (column 0).
    nxt = _re.search(r"^[A-Za-z_][\w.-]*[ \t]*:", rest, _re.M)
    stop = m.end() + (nxt.start() if nxt else len(rest))
    return text[: m.start()] + block + text[stop:]


def persist_mcp_yaml(
    servers: list[dict[str, Any]],
    *,
    yaml_path: str | Path | None = None,
    mcp_section: dict[str, Any] | None = None,
) -> str | None:
    """Write *servers* into ``kazma.yaml`` ``mcp.servers`` atomically.

    Only the ``mcp:`` section is rewritten (comment-preserving splice —
    see :func:`_splice_yaml_section`); the rest of the file is untouched.

    Returns ``None`` on success, or an error message string.
    """
    path = _resolve_yaml_path(yaml_path)
    if not path.is_file():
        return f"kazma.yaml not found at {path}"
    try:
        import yaml

        with open(path, encoding="utf-8") as f:
            on_disk_text = f.read()
        on_disk = yaml.safe_load(on_disk_text) or {}
        if not isinstance(on_disk, dict):
            on_disk = {}
        mcp = dict(mcp_section) if isinstance(mcp_section, dict) else dict(on_disk.get("mcp") or {})
        mcp["servers"] = servers
        block = yaml.safe_dump(
            {"mcp": mcp},
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
        )
        new_text = _splice_yaml_section(on_disk_text, "mcp", block)

        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(new_text)
        tmp.replace(path)
        logger.info(
            "[mcp_servers_store] Persisted mcp.servers to %s (%d server(s))",
            path,
            len(servers),
        )
        return None
    except Exception as exc:
        logger.warning("[mcp_servers_store] yaml write failed: %s", exc)
        return str(exc)


# ── Public API ────────────────────────────────────────────────────────────


def list_mcp_servers(
    *,
    yaml_servers: list[dict[str, Any]] | None = None,
    yaml_path: str | Path | None = None,
    include_disk_yaml: bool = True,
) -> list[dict[str, Any]]:
    """Return merged MCP server list (ConfigStore wins on name conflict).

    Merge order:

    1. On-disk ``kazma.yaml`` (optional seed)
    2. In-memory *yaml_servers* (agent ``config.raw`` — overlays disk)
    3. ConfigStore (wins — Settings / the MCP page)

    A kazma.yaml server removed from a page (``mcp.removed_servers``) is left
    out: kazma.yaml is never written, so it still lists it.
    """
    removed = _removed_names()
    by_name: dict[str, dict[str, Any]] = {}

    if include_disk_yaml:
        for s in _read_yaml_servers(yaml_path):
            if str(s["name"]) not in removed:
                by_name[str(s["name"])] = s

    if yaml_servers is not None:
        for s in _normalize_list(yaml_servers):
            if str(s["name"]) not in removed:
                by_name[str(s["name"])] = s

    for s in _cs_get():
        by_name[str(s["name"])] = s

    return [_canonical_server(s) for s in by_name.values()]


def server_enabled(server: dict[str, Any]) -> bool:
    """Whether *server* is switched on: started at boot and kept running.

    The one reading of ``enabled`` (absent = on): the boot connect, the list
    every page reads, Settings' switch and ``/config tools`` all ask it.
    """
    return bool(server.get("enabled", True))


#: What a page is told about a server while Kazma runs -- never configuration.
#: ``upsert_mcp_server`` stored three as defaults (``connected: false``,
#: ``tool_count: 0``, ``tools: []``), so kazma.yaml said every server was
#: down with no tools while it ran with fourteen (2026-10-02). Nothing that
#: connects a server reads them. ``_resolved_workspace`` is the folder a
#: connect pinned: stored, the manager would take it as pinned already and
#: start the server there after a repo switch.
_RUNTIME_FIELDS = frozenset({
    "status", "connected", "tool_count", "tools",
    "connection_error", "oauth_status", "oauth_required", "_resolved_workspace",
})


def _canonical_server(server: dict[str, Any]) -> dict[str, Any]:
    """*server* as it is stored and shown: its configuration only (the
    runtime fields above dropped), a workspace-bound one marked so, with an
    old sandbox path in its folder argument put back to the placeholder.

    The connect pins a workspace-bound server's folder to the active
    workspace whatever is stored (:mod:`kazma_core.workspace.mcp_rebind`), so
    a concrete sandbox path in storage only misleads. The live install's
    settings held a filesystem server written before the placeholder existed
    (``kazma-data/workspace``, no ``workspace_bound``); the settings copy wins
    over kazma.yaml's by name, the MCP page showed it, and the next MCP edit
    wrote it over kazma.yaml's ``${KAZMA_ACTIVE_WORKSPACE}`` (2026-10-02). A
    folder the operator chose stays as written.
    """
    from kazma_core.workspace.mcp_rebind import (
        ACTIVE_WORKSPACE_PLACEHOLDER,
        is_legacy_sandbox_arg,
        is_workspace_bound_server,
    )

    out = {k: v for k, v in server.items() if k not in _RUNTIME_FIELDS}
    if not is_workspace_bound_server(out):
        return out
    out["workspace_bound"] = True
    command = list(out.get("command") or [])
    if command and is_legacy_sandbox_arg(str(command[-1])):
        command[-1] = ACTIVE_WORKSPACE_PLACEHOLDER
        out["command"] = command
    return out


def _sync_config_raw(
    config_raw: dict[str, Any] | None,
    servers: list[dict[str, Any]],
) -> None:
    if config_raw is None:
        return
    mcp = config_raw.setdefault("mcp", {})
    if not isinstance(mcp, dict):
        config_raw["mcp"] = {"servers": servers}
    else:
        mcp["servers"] = servers


def _write_everywhere(
    servers: list[dict[str, Any]],
    *,
    before: list[dict[str, Any]],
    config_raw: dict[str, Any] | None,
    yaml_path: str | Path | None,
    removed: set[str] | None = None,
    rewrite_yaml: bool = False,
) -> tuple[list[dict[str, Any]], str | None]:
    """Write *servers* to the settings store and config.raw, secrets in the vault.

    The one way this module writes, and never to kazma.yaml (module
    docstring) but for *rewrite_yaml*: :func:`move_plaintext_secrets` swaps
    the secrets typed into kazma.yaml's own servers for vault pointers and
    keeps the file's list of servers as it was. *removed* is the new
    ``mcp.removed_servers``, written with the list (``None``: unchanged).
    *before* is the stored list (pointers unresolved): a secret posted back
    masked (``****``) or empty keeps what it holds. Returns what was written
    and the YAML error, or ``None``.
    """
    from kazma_core.mcp.secrets import externalize

    stored = {str(s.get("name")): s for s in before if isinstance(s, dict)}
    servers = [_canonical_server(externalize(s, stored.get(str(s.get("name"))))) for s in servers]
    _cs_set(servers, removed)
    _sync_config_raw(config_raw, servers)
    if not rewrite_yaml:
        return servers, None
    written = {str(s.get("name")): s for s in servers}
    on_disk = [
        written.get(str(s["name"])) or _canonical_server(externalize(s, None))
        for s in _read_yaml_servers(yaml_path)
    ]
    err = persist_mcp_yaml(
        on_disk,
        yaml_path=yaml_path,
        mcp_section=config_raw.get("mcp") if config_raw else None,
    )
    return servers, err


def servers_with_plaintext_secrets(
    *,
    config_raw: dict[str, Any] | None = None,
    yaml_path: str | Path | None = None,
) -> list[str]:
    """The MCP servers whose secrets are stored as typed, not as vault pointers.

    Read from the settings database, ``kazma.yaml`` and, when given, the
    agent's in-memory ``config.raw``. :func:`move_plaintext_secrets` empties
    this list; the security report shows what it still holds.
    """
    from kazma_core.mcp.secrets import has_plaintext_secret

    yaml_in_mem = (config_raw.get("mcp") or {}).get("servers", []) if config_raw else None
    sources = [*_cs_get(), *_read_yaml_servers(yaml_path), *_normalize_list(yaml_in_mem)]
    return sorted({str(s.get("name")) for s in sources if has_plaintext_secret(s)})


def move_plaintext_secrets(
    *,
    config_raw: dict[str, Any] | None = None,
    yaml_path: str | Path | None = None,
) -> int:
    """Move MCP secrets still stored as typed into the vault; how many servers held one.

    Stores written before 2026-09-30 hold them in kazma.yaml and the settings
    database. ``KazmaAgent.connect_mcp_servers`` runs this first. Nothing is
    written when no secret is left or when there is no vault to put it in.
    """
    from kazma_core.config_store import _try_get_vault

    yaml_in_mem = (config_raw.get("mcp") or {}).get("servers", []) if config_raw else None
    holders = servers_with_plaintext_secrets(config_raw=config_raw, yaml_path=yaml_path)
    if not holders:
        return 0
    if _try_get_vault() is None:
        logger.warning(
            "[mcp_servers_store] %d MCP server(s) keep secrets in kazma.yaml / the "
            "settings database, and there is no vault to move them to: %s",
            len(holders), ", ".join(holders),
        )
        return 0
    from kazma_core.mcp.secrets import has_plaintext_secret

    current = list_mcp_servers(yaml_servers=yaml_in_mem, yaml_path=yaml_path)
    in_yaml = any(has_plaintext_secret(s) for s in _read_yaml_servers(yaml_path))
    _written, err = _write_everywhere(
        current, before=current, config_raw=config_raw, yaml_path=yaml_path, rewrite_yaml=in_yaml,
    )
    left = servers_with_plaintext_secrets(yaml_path=yaml_path)
    logger.info(
        "[mcp_servers_store] Moved the secrets of %d MCP server(s) into the vault: %s%s",
        len(holders), ", ".join(holders),
        f" (still as typed: {', '.join(left)}; yaml: {err})" if left or err else "",
    )
    return len(holders)


def upsert_mcp_server(
    data: dict[str, Any],
    *,
    config_raw: dict[str, Any] | None = None,
    yaml_path: str | Path | None = None,
    replace: bool = True,
) -> dict[str, Any]:
    """Add or update an MCP server in the settings store + optional config.raw.

    A name removed before (``mcp.removed_servers``) is taken off that list.
    Returns the stored server dict. Raises ``ValueError`` on missing name or
    (when *replace* is False) duplicate name.
    """
    name = (data.get("name") or "").strip()
    if not name:
        raise ValueError("Server name is required")

    yaml_in_mem = None
    if config_raw is not None:
        yaml_in_mem = (config_raw.get("mcp") or {}).get("servers", [])

    servers = list_mcp_servers(yaml_servers=yaml_in_mem, yaml_path=yaml_path)
    before = [dict(s) for s in servers]
    existing_idx = next(
        (i for i, s in enumerate(servers) if s.get("name") == name),
        None,
    )
    if existing_idx is not None and not replace:
        raise ValueError(f"Server '{name}' already exists")

    server: dict[str, Any] = {
        "name": name,
        "transport": data.get("transport", "stdio"),
        "command": data.get("command", []) or [],
        "url": data.get("url", "") or "",
        "env": data.get("env", {}) or {},
        "enabled": data.get("enabled", True),
    }
    if data.get("working_dir"):
        server["working_dir"] = data["working_dir"]
    if data.get("trust"):
        server["trust"] = data["trust"]
    # Preserve extra keys callers may set (auth, etc.)
    for k, v in data.items():
        if k not in server and v is not None:
            server[k] = v

    if existing_idx is not None:
        # Merge: keep previous keys not present in payload
        merged = dict(servers[existing_idx])
        merged.update(server)
        servers[existing_idx] = merged
        server = merged
    else:
        servers.append(server)

    removed = _removed_names()
    written, _err = _write_everywhere(
        servers, before=before, config_raw=config_raw, yaml_path=yaml_path,
        removed=removed - {name} if name in removed else None,
    )
    return next((s for s in written if s.get("name") == name), server)


def delete_mcp_server(
    name: str,
    *,
    config_raw: dict[str, Any] | None = None,
    yaml_path: str | Path | None = None,
) -> None:
    """Remove *name* from the settings store + optional config.raw.

    kazma.yaml is not written: a server it lists is recorded in
    ``mcp.removed_servers`` and left out of the merge. Before 2026-10-02 the
    delete rewrote kazma.yaml, and a delete whose write failed came back on
    the next read.
    """
    yaml_in_mem = None
    if config_raw is not None:
        yaml_in_mem = (config_raw.get("mcp") or {}).get("servers", [])

    current = list_mcp_servers(yaml_servers=yaml_in_mem, yaml_path=yaml_path)
    servers = [s for s in current if s.get("name") != name]
    in_yaml = any(str(s.get("name")) == name for s in _read_yaml_servers(yaml_path))
    _write_everywhere(
        servers, before=current, config_raw=config_raw, yaml_path=yaml_path,
        removed=_removed_names() | {name} if in_yaml else None,
    )
    from kazma_core.mcp.secrets import forget

    for gone in (s for s in current if s.get("name") == name):
        forget(gone)


def set_mcp_server_enabled(
    name: str,
    enabled: bool,
    *,
    config_raw: dict[str, Any] | None = None,
    yaml_path: str | Path | None = None,
) -> bool:
    """Toggle *enabled* on a server (dual-write); False when there is no
    server *name* (nothing is written)."""
    yaml_in_mem = None
    if config_raw is not None:
        yaml_in_mem = (config_raw.get("mcp") or {}).get("servers", [])

    servers = list_mcp_servers(yaml_servers=yaml_in_mem, yaml_path=yaml_path)
    before = [dict(s) for s in servers]
    found = False
    for s in servers:
        if s.get("name") == name:
            s["enabled"] = enabled
            found = True
            break
    if not found:
        return False
    _write_everywhere(servers, before=before, config_raw=config_raw, yaml_path=yaml_path)
    return True
