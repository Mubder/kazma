"""Every MCP server Kazma ships runs an exact version (2026-11, supply chain).

kazma.yaml shipped ``npx -y @modelcontextprotocol/server-filesystem`` with no
version while the preset catalog was pinned (2026-11-10): every start of the
default filesystem server ran whatever npm served that day, with the owner's
rights over the workspace. kazma.yaml now names the catalog's version
(``mcp_servers_store.SHIPPED_PACKAGE_VERSIONS``), and installs that booted
before follow:

* an install whose settings still hold the list its first boot copied from
  kazma.yaml takes today's at the next boot (``config_defaults``, a
  ``RetiredDefault`` of ``mcp.servers``);
* a stored list the owner changed -- the live install's -- has the bare
  shipped package pinned where every reader and writer passes it
  (``mcp_servers_store._canonical_server``): the connect, the pages, the next
  write. A version the owner wrote is left as written.

Each with its negative control: the canonicalizer without the pins, and the
boot without the declaration. The gate: every npx/uvx command in a config
Kazma ships names an exact version; the old kazma.yaml line fails it.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import yaml

from kazma_core import config_defaults
from kazma_core.config_store import ConfigStore
from kazma_core.mcp_servers_store import (
    CONFIG_KEY,
    SHIPPED_PACKAGE_VERSIONS,
    list_mcp_servers,
    upsert_mcp_server,
)
from tests.test_mcp_catalog import RECORD, pinned_version, run_target

REPO = Path(__file__).resolve().parents[1]
CATALOG = REPO / "kazma-skills" / "kazma_skills" / "certified_servers.yaml"
FS = "@modelcontextprotocol/server-filesystem"
PLACEHOLDER = "${KAZMA_ACTIVE_WORKSPACE}"

#: The filesystem server as kazma.yaml shipped it before the pin.
OLD_SHIPPED = {
    "name": "filesystem", "transport": "stdio", "workspace_bound": True,
    "command": ["npx", "-y", FS, PLACEHOLDER],
    "enabled": True, "tool_count": 0, "tools": [], "trust": "approval_required", "auth": {},
}


def _commands(node: Any) -> list[list[str]]:
    """Every npx/uvx command list in a parsed config (any depth)."""
    found: list[list[str]] = []
    if isinstance(node, dict):
        for value in node.values():
            found += _commands(value)
    elif isinstance(node, list):
        if node and all(isinstance(x, (str, int, float)) for x in node) and run_target([str(x) for x in node]):
            found.append([str(x) for x in node])
        else:
            for value in node:
                found += _commands(value)
    return found


def unpinned_commands(data: Any) -> list[list[str]]:
    return [c for c in _commands(data) if pinned_version(c) is None]



def _shipped_configs() -> list[str]:
    tracked = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=REPO, capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    return [
        rel for rel in tracked
        if (rel.endswith((".yaml", ".yml", ".json"))
            and (("/" not in rel) or rel.startswith(("kazma-", "deploy/")))
            and "/tests/" not in rel and "_tests/" not in rel
            and "node_modules" not in rel)
    ]


def test_every_package_kazma_ships_names_an_exact_version():
    offenders: list[str] = []
    seen = 0
    for rel in _shipped_configs():
        text = (REPO / rel).read_text(encoding="utf-8", errors="replace")
        try:
            data = json.loads(text) if rel.endswith(".json") else yaml.safe_load(text)
        except (ValueError, yaml.YAMLError):
            continue
        commands = _commands(data)
        seen += len(commands)
        offenders += [f"{rel}: {' '.join(c)}" for c in commands if pinned_version(c) is None]
    assert seen >= 2, "the gate found no shipped npx/uvx commands; it would be decoration"
    assert not offenders, (
        "An npx/uvx command Kazma ships runs whatever the registry serves that day. "
        "Name an exact version (and keep kazma.yaml, the catalog and "
        "mcp_servers_store.SHIPPED_PACKAGE_VERSIONS equal):\n  " + "\n  ".join(offenders)
    )


def test_negative_control_the_old_kazma_yaml_line_fails_the_gate():
    shipped = (REPO / "kazma.yaml").read_text(encoding="utf-8")
    old = shipped.replace(f"'{FS}@{SHIPPED_PACKAGE_VERSIONS[FS]}'", f"'{FS}'")
    assert old != shipped
    assert unpinned_commands(yaml.safe_load(old)) == [["npx", "-y", FS, PLACEHOLDER]]
    assert unpinned_commands(yaml.safe_load(shipped)) == []
    assert unpinned_commands({"servers": [{"command": ["uvx", "mcp-server-time"]}]})


def test_kazma_yaml_the_catalog_and_the_store_name_one_version():
    shipped = yaml.safe_load((REPO / "kazma.yaml").read_text(encoding="utf-8"))
    catalog = yaml.safe_load(CATALOG.read_text(encoding="utf-8"))["servers"]
    for package, version in SHIPPED_PACKAGE_VERSIONS.items():
        in_yaml = [c for c in _commands(shipped) if run_target(c) == ("npm", package)]
        in_catalog = [c for c in _commands(catalog) if run_target(c) == ("npm", package)]
        assert in_yaml and in_catalog, package
        assert {pinned_version(c) for c in in_yaml + in_catalog} == {version}, package
        assert RECORD["packages"][f"npm:{package}"]["latest"] == version, package


# ── installs that booted before the pin follow ───────────────────────────


@pytest.fixture
def store(tmp_path):
    (tmp_path / "kazma.yaml").write_text(yaml.safe_dump({"mcp": {"servers": []}}), encoding="utf-8")
    real = ConfigStore(db_path=str(tmp_path / "settings.db"), yaml_path=str(tmp_path / "kazma.yaml"))
    with patch("kazma_core.config_store.get_config_store", return_value=real):
        yield real, tmp_path / "kazma.yaml"
    real.close()


def _stored_filesystem(real: ConfigStore) -> dict:
    raw = real._db_get_raw(CONFIG_KEY)
    servers = json.loads(raw) if isinstance(raw, str) else raw
    return next(s for s in servers if s["name"] == "filesystem")


def test_a_stored_unpinned_server_runs_pinned_and_is_written_pinned(store):
    real, yaml_path = store
    real.set(CONFIG_KEY, json.dumps([OLD_SHIPPED]), category="mcp")

    (listed,) = list_mcp_servers(yaml_path=yaml_path)   # the connect and every page read this
    assert listed["command"] == ["npx", "-y", f"{FS}@{SHIPPED_PACKAGE_VERSIONS[FS]}", PLACEHOLDER]

    upsert_mcp_server({"name": "time", "command": ["uvx", "mcp-server-time@2026.8.18"]}, yaml_path=yaml_path)
    assert _stored_filesystem(real)["command"][2] == f"{FS}@{SHIPPED_PACKAGE_VERSIONS[FS]}"


def test_negative_control_the_old_canonicalizer_keeps_it_unpinned(store, monkeypatch):
    import kazma_core.mcp_servers_store as mcp_store

    monkeypatch.setattr(mcp_store, "SHIPPED_PACKAGE_VERSIONS", {})
    real, yaml_path = store
    real.set(CONFIG_KEY, json.dumps([OLD_SHIPPED]), category="mcp")
    (listed,) = list_mcp_servers(yaml_path=yaml_path)
    assert listed["command"][2] == FS


def test_a_version_the_owner_wrote_is_theirs(store):
    real, yaml_path = store
    mine = [dict(OLD_SHIPPED, name="photos", command=["npx", "-y", f"{FS}@1.0.0", "D:/photos"]),
            dict(OLD_SHIPPED, name="latest", command=["npx", "-y", f"{FS}@latest", "D:/x"]),
            dict(OLD_SHIPPED, name="other", command=["npx", "-y", "@scope/other", "D:/y"])]
    real.set(CONFIG_KEY, json.dumps(mine), category="mcp")
    listed = {s["name"]: s["command"][2] for s in list_mcp_servers(yaml_path=yaml_path)}
    assert listed == {"photos": f"{FS}@1.0.0", "latest": f"{FS}@latest", "other": "@scope/other"}


def _boot_on(tmp_path: Path, yaml_text: str) -> ConfigStore:
    (tmp_path / "kazma.yaml").write_text(yaml_text, encoding="utf-8")
    real = ConfigStore(db_path=str(tmp_path / "settings.db"), yaml_path=str(tmp_path / "kazma.yaml"))
    real.reconcile_from_yaml()
    return real


def _old_yaml() -> str:
    shipped = (REPO / "kazma.yaml").read_text(encoding="utf-8")
    return shipped.replace(f"'{FS}@{SHIPPED_PACKAGE_VERSIONS[FS]}'", f"'{FS}'")


def _first_boot_on_the_old_build(tmp_path: Path, monkeypatch) -> None:
    """The first boot ran a build without this declaration: it neither
    retired the list nor recorded the entry as applied."""
    with monkeypatch.context() as m:
        m.setattr(config_defaults, "RETIRED_DEFAULTS", tuple(
            e for e in config_defaults.RETIRED_DEFAULTS if e.key != CONFIG_KEY))
        first = _boot_on(tmp_path, _old_yaml())
        assert _stored_filesystem(first)["command"][2] == FS
        first.close()


def test_an_install_still_holding_the_first_boot_copy_follows_at_boot(tmp_path, monkeypatch):
    _first_boot_on_the_old_build(tmp_path, monkeypatch)
    upgraded = _boot_on(tmp_path, (REPO / "kazma.yaml").read_text(encoding="utf-8"))
    try:
        assert _stored_filesystem(upgraded)["command"][2] == f"{FS}@{SHIPPED_PACKAGE_VERSIONS[FS]}"
    finally:
        upgraded.close()


def test_negative_control_without_the_declaration_the_first_boot_copy_stays(tmp_path, monkeypatch):
    _first_boot_on_the_old_build(tmp_path, monkeypatch)
    monkeypatch.setattr(config_defaults, "RETIRED_DEFAULTS", tuple(
        e for e in config_defaults.RETIRED_DEFAULTS if e.key != CONFIG_KEY))
    upgraded = _boot_on(tmp_path, (REPO / "kazma.yaml").read_text(encoding="utf-8"))
    try:
        assert _stored_filesystem(upgraded)["command"][2] == FS
    finally:
        upgraded.close()
