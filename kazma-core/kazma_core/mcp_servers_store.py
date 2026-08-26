"""Single source of truth for MCP server configuration.

Historically two independent stores drifted apart:

* ``kazma.yaml`` ``mcp.servers`` — written by ``/mcp`` Add Server
  (``KazmaAgent.add_mcp_server`` → ``_persist_mcp_servers``)
* ConfigStore key ``mcp.servers`` — written by Settings
  (``MCPSettingsService``)

The Settings Test button only read ConfigStore, so servers added from
``/mcp`` reported "Server not found". Agent connect merged both, but
Settings list/test/toggle did not.

This module is the **only** place that reads/writes either store. All
mutators dual-write both backends so they stay in sync. Readers always
merge (ConfigStore wins on name conflict — runtime UI edits beat seed).
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
    "persist_mcp_yaml",
    "set_mcp_server_enabled",
    "sync_mcp_servers",
    "upsert_mcp_server",
]

logger = logging.getLogger(__name__)

CONFIG_KEY = "mcp.servers"
_DEFAULT_YAML = "kazma.yaml"


# ── ConfigStore helpers ───────────────────────────────────────────────────


def _config_key() -> str:
    """ConfigStore key — tenant-scoped when multi-user/production isolation is on."""
    try:
        from kazma_core.tenant_isolation import multi_user_or_production, tenant_key

        if multi_user_or_production():
            return tenant_key(CONFIG_KEY)
    except Exception:
        pass
    return CONFIG_KEY


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


def _cs_get() -> list[dict[str, Any]]:
    try:
        from kazma_core.config_store import get_config_store

        key = _config_key()
        servers = _normalize_list(get_config_store().get(key, []))
        # Legacy: if tenant key empty, also merge global mcp.servers once
        if key != CONFIG_KEY and not servers:
            servers = _normalize_list(get_config_store().get(CONFIG_KEY, []))
        return servers
    except Exception as exc:
        logger.debug("[mcp_servers_store] ConfigStore read failed: %s", exc)
        return []


def _cs_set(servers: list[dict[str, Any]]) -> None:
    try:
        from kazma_core.config_store import get_config_store

        get_config_store().set(
            _config_key(),
            json.dumps(servers, ensure_ascii=False),
            category="mcp",
        )
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
    3. ConfigStore (wins — Settings / dual-write runtime SoT)
    """
    by_name: dict[str, dict[str, Any]] = {}

    if include_disk_yaml:
        for s in _read_yaml_servers(yaml_path):
            by_name[str(s["name"])] = s

    if yaml_servers is not None:
        for s in _normalize_list(yaml_servers):
            by_name[str(s["name"])] = s

    for s in _cs_get():
        by_name[str(s["name"])] = s

    return list(by_name.values())


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


def sync_mcp_servers(
    servers: list[dict[str, Any]],
    *,
    config_raw: dict[str, Any] | None = None,
    yaml_path: str | Path | None = None,
) -> str | None:
    """Replace the full server list in ConfigStore + yaml (+ optional config.raw).

    Returns yaml error message or ``None`` when both stores accept the write.
    """
    normalized = _normalize_list(servers)
    _cs_set(normalized)
    _sync_config_raw(config_raw, normalized)
    return persist_mcp_yaml(
        normalized,
        yaml_path=yaml_path,
        mcp_section=config_raw.get("mcp") if config_raw else None,
    )


def upsert_mcp_server(
    data: dict[str, Any],
    *,
    config_raw: dict[str, Any] | None = None,
    yaml_path: str | Path | None = None,
    replace: bool = True,
) -> dict[str, Any]:
    """Add or update an MCP server in ConfigStore + yaml + optional config.raw.

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
        "connected": data.get("connected", False),
        "tool_count": data.get("tool_count", 0),
        "tools": data.get("tools", []) or [],
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

    _cs_set(servers)
    _sync_config_raw(config_raw, servers)
    err = persist_mcp_yaml(
        servers,
        yaml_path=yaml_path,
        mcp_section=config_raw.get("mcp") if config_raw else None,
    )
    if err:
        logger.warning("[mcp_servers_store] upsert ConfigStore ok, yaml failed: %s", err)
    return server


def delete_mcp_server(
    name: str,
    *,
    config_raw: dict[str, Any] | None = None,
    yaml_path: str | Path | None = None,
) -> None:
    """Remove *name* from ConfigStore + yaml + optional config.raw."""
    yaml_in_mem = None
    if config_raw is not None:
        yaml_in_mem = (config_raw.get("mcp") or {}).get("servers", [])

    servers = [
        s
        for s in list_mcp_servers(yaml_servers=yaml_in_mem, yaml_path=yaml_path)
        if s.get("name") != name
    ]
    _cs_set(servers)
    _sync_config_raw(config_raw, servers)
    err = persist_mcp_yaml(
        servers,
        yaml_path=yaml_path,
        mcp_section=config_raw.get("mcp") if config_raw else None,
    )
    if err:
        logger.warning("[mcp_servers_store] delete ConfigStore ok, yaml failed: %s", err)


def set_mcp_server_enabled(
    name: str,
    enabled: bool,
    *,
    config_raw: dict[str, Any] | None = None,
    yaml_path: str | Path | None = None,
) -> None:
    """Toggle *enabled* on a server (dual-write)."""
    yaml_in_mem = None
    if config_raw is not None:
        yaml_in_mem = (config_raw.get("mcp") or {}).get("servers", [])

    servers = list_mcp_servers(yaml_servers=yaml_in_mem, yaml_path=yaml_path)
    found = False
    for s in servers:
        if s.get("name") == name:
            s["enabled"] = enabled
            found = True
            break
    if not found:
        return
    _cs_set(servers)
    _sync_config_raw(config_raw, servers)
    err = persist_mcp_yaml(
        servers,
        yaml_path=yaml_path,
        mcp_section=config_raw.get("mcp") if config_raw else None,
    )
    if err:
        logger.warning("[mcp_servers_store] toggle ConfigStore ok, yaml failed: %s", err)
