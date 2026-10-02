"""Unified MCP server dual-store (ConfigStore + kazma.yaml)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import yaml

from kazma_core.mcp_servers_store import (
    CONFIG_KEY,
    delete_mcp_server,
    list_mcp_servers,
    set_mcp_server_enabled,
    upsert_mcp_server,
)


@pytest.fixture()
def dual_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Isolated ConfigStore mock + temp kazma.yaml."""
    yaml_path = tmp_path / "kazma.yaml"
    yaml_path.write_text(
        yaml.safe_dump({"agent": {"name": "test"}, "mcp": {"servers": []}}),
        encoding="utf-8",
    )

    store_data: dict[str, object] = {}

    mock_cs = MagicMock()

    def _get(key, default=None):
        return store_data.get(key, default)

    def _set(key, value, category="general"):
        store_data[key] = value

    mock_cs.get.side_effect = _get
    mock_cs.set.side_effect = _set

    with patch(
        "kazma_core.config_store.get_config_store",
        return_value=mock_cs,
    ):
        yield {
            "yaml_path": yaml_path,
            "store_data": store_data,
            "mock_cs": mock_cs,
        }


def test_upsert_dual_writes_configstore_and_yaml(dual_env):
    yaml_path = dual_env["yaml_path"]
    store_data = dual_env["store_data"]

    server = upsert_mcp_server(
        {
            "name": "Playwright",
            "transport": "stdio",
            "command": ["npx", "@playwright/mcp@latest"],
        },
        yaml_path=yaml_path,
        replace=False,
    )
    assert server["name"] == "Playwright"

    # ConfigStore has it
    raw = store_data.get(CONFIG_KEY)
    assert raw is not None
    parsed = json.loads(raw) if isinstance(raw, str) else raw
    assert any(s.get("name") == "Playwright" for s in parsed)

    # YAML has it
    on_disk = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    names = [s.get("name") for s in on_disk["mcp"]["servers"]]
    assert "Playwright" in names


def test_upsert_preserves_sse_bearer_auth_and_trust(dual_env):
    """SSE credentials and the explicit trust policy survive both stores."""
    yaml_path = dual_env["yaml_path"]
    store_data = dual_env["store_data"]

    server = upsert_mcp_server(
        {
            "name": "remote",
            "transport": "sse",
            "url": "https://mcp.example.test/sse",
            "auth": {"type": "bearer", "token": "test-token"},
            "trust": "trusted",
        },
        yaml_path=yaml_path,
    )

    assert server["auth"] == {"type": "bearer", "token": "test-token"}
    assert server["trust"] == "trusted"
    stored = json.loads(store_data[CONFIG_KEY])
    assert stored[0]["auth"] == {"type": "bearer", "token": "test-token"}
    on_disk = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    assert on_disk["mcp"]["servers"][0]["trust"] == "trusted"


def test_agent_add_forwards_sse_bearer_auth_and_trust() -> None:
    """The agent facade must not discard security settings before persistence."""
    from kazma_core.agent_runner import KazmaAgent

    captured: dict[str, object] = {}
    agent = SimpleNamespace(
        config=SimpleNamespace(raw={"mcp": {"servers": []}}),
        _mcp_yaml_path=lambda: "unused.yaml",
    )

    def capture_upsert(data, **_kwargs):
        captured.update(data)

    with patch(
        "kazma_core.mcp_servers_store.list_mcp_servers",
        return_value=[],
    ), patch(
        "kazma_core.mcp_servers_store.upsert_mcp_server",
        side_effect=capture_upsert,
    ):
        result = KazmaAgent.add_mcp_server(
            agent,
            name="remote",
            transport="sse",
            url="https://mcp.example.test/sse",
            auth={"type": "bearer", "token": "test-token"},
            trust="trusted",
        )

    assert result == {"status": "ok"}
    assert captured["auth"] == {"type": "bearer", "token": "test-token"}
    assert captured["trust"] == "trusted"


def test_list_merges_yaml_only_server_into_settings_view(dual_env):
    """Settings used to miss servers that only lived in kazma.yaml."""
    yaml_path = dual_env["yaml_path"]

    # Seed YAML only (simulate pre-unification /mcp Add Server without CS)
    yaml_path.write_text(
        yaml.safe_dump(
            {
                "mcp": {
                    "servers": [
                        {
                            "name": "Playwright",
                            "transport": "stdio",
                            "command": ["npx", "playwright"],
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    servers = list_mcp_servers(yaml_path=yaml_path)
    assert any(s.get("name") == "Playwright" for s in servers)


def test_configstore_wins_on_name_conflict(dual_env):
    yaml_path = dual_env["yaml_path"]
    store_data = dual_env["store_data"]

    yaml_path.write_text(
        yaml.safe_dump(
            {
                "mcp": {
                    "servers": [
                        {"name": "fs", "transport": "stdio", "command": ["old"]}
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    store_data[CONFIG_KEY] = json.dumps(
        [{"name": "fs", "transport": "stdio", "command": ["new"]}]
    )

    servers = list_mcp_servers(yaml_path=yaml_path)
    match = [s for s in servers if s["name"] == "fs"]
    assert len(match) == 1
    assert match[0]["command"] == ["new"]


def test_delete_removes_from_both(dual_env):
    yaml_path = dual_env["yaml_path"]

    upsert_mcp_server(
        {"name": "tmp", "transport": "stdio", "command": ["echo"]},
        yaml_path=yaml_path,
    )
    delete_mcp_server("tmp", yaml_path=yaml_path)

    servers = list_mcp_servers(yaml_path=yaml_path)
    assert not any(s.get("name") == "tmp" for s in servers)
    on_disk = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    assert not any(
        s.get("name") == "tmp" for s in on_disk.get("mcp", {}).get("servers", [])
    )


def test_toggle_enabled(dual_env):
    yaml_path = dual_env["yaml_path"]
    upsert_mcp_server(
        {"name": "tog", "transport": "stdio", "command": ["echo"], "enabled": True},
        yaml_path=yaml_path,
    )
    set_mcp_server_enabled("tog", False, yaml_path=yaml_path)
    servers = list_mcp_servers(yaml_path=yaml_path)
    tog = next(s for s in servers if s["name"] == "tog")
    assert tog["enabled"] is False


def test_upsert_syncs_config_raw(dual_env):
    yaml_path = dual_env["yaml_path"]
    config_raw: dict = {"mcp": {"servers": []}}
    upsert_mcp_server(
        {"name": "inmem", "transport": "sse", "url": "http://x"},
        config_raw=config_raw,
        yaml_path=yaml_path,
    )
    assert any(s["name"] == "inmem" for s in config_raw["mcp"]["servers"])


def test_settings_service_sees_yaml_seeded_server(dual_env):
    """MCPSettingsService.get_mcp_servers must not be ConfigStore-only."""
    from kazma_core.settings_mcp import MCPSettingsService

    yaml_path = dual_env["yaml_path"]
    yaml_path.write_text(
        yaml.safe_dump(
            {
                "mcp": {
                    "servers": [
                        {
                            "name": "Playwright",
                            "transport": "stdio",
                            "command": ["npx", "playwright"],
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    with patch(
        "kazma_core.settings_mcp._agent_yaml_path",
        return_value=str(yaml_path),
    ), patch(
        "kazma_core.settings_mcp._agent_config_raw",
        return_value=None,
    ):
        svc = MCPSettingsService(dual_env["mock_cs"])
        names = [s.get("name") for s in svc.get_mcp_servers()]
        assert "Playwright" in names


# ── a workspace-bound server is stored and shown as it runs (2026-10-02) ──

_PLACEHOLDER = "${KAZMA_ACTIVE_WORKSPACE}"
#: The live install's settings copy: written before the placeholder existed.
_STALE_FILESYSTEM = {
    "name": "filesystem",
    "transport": "stdio",
    "command": ["npx", "-y", "@modelcontextprotocol/server-filesystem", "kazma-data/workspace"],
}


def _seed(dual_env) -> None:
    dual_env["yaml_path"].write_text(yaml.safe_dump({"mcp": {"servers": [{
        "name": "filesystem", "transport": "stdio", "workspace_bound": True,
        "command": ["npx", "-y", "@modelcontextprotocol/server-filesystem", _PLACEHOLDER],
        "enabled": True, "trust": "approval_required",
    }]}}), encoding="utf-8")
    dual_env["store_data"][CONFIG_KEY] = json.dumps([_STALE_FILESYSTEM])


def test_a_stale_settings_copy_is_read_as_the_workspace_bound_server(dual_env):
    _seed(dual_env)
    (fs,) = list_mcp_servers(yaml_path=dual_env["yaml_path"])
    assert fs["workspace_bound"] is True
    assert fs["command"][-1] == _PLACEHOLDER


def test_an_mcp_edit_never_writes_the_stale_copy_over_kazma_yaml(dual_env):
    """Live 2026-10-02: replacing the sequential-thinking server wrote the
    settings copy's `kazma-data/workspace` (and no workspace_bound) over
    kazma.yaml's placeholder."""
    _seed(dual_env)
    upsert_mcp_server(
        {"name": "sequential-thinking", "command": ["npx", "-y", "@modelcontextprotocol/server-sequential-thinking"]},
        yaml_path=dual_env["yaml_path"],
    )
    written = yaml.safe_load(dual_env["yaml_path"].read_text(encoding="utf-8"))["mcp"]["servers"]
    fs = next(s for s in written if s["name"] == "filesystem")
    assert fs["workspace_bound"] is True and fs["command"][-1] == _PLACEHOLDER
    stored = next(s for s in json.loads(dual_env["store_data"][CONFIG_KEY]) if s["name"] == "filesystem")
    assert stored["command"][-1] == _PLACEHOLDER


def test_negative_control_without_the_canonical_form_the_fossil_is_written(dual_env, monkeypatch):
    import kazma_core.mcp_servers_store as store

    monkeypatch.setattr(store, "_canonical_server", lambda server: server)
    _seed(dual_env)
    upsert_mcp_server({"name": "x", "command": ["x"]}, yaml_path=dual_env["yaml_path"])
    written = yaml.safe_load(dual_env["yaml_path"].read_text(encoding="utf-8"))["mcp"]["servers"]
    fs = next(s for s in written if s["name"] == "filesystem")
    assert fs["command"][-1] == "kazma-data/workspace" and "workspace_bound" not in fs


def test_a_folder_the_operator_chose_stays_and_other_servers_are_untouched() -> None:
    from kazma_core.mcp_servers_store import _canonical_server

    chosen = {"name": "photos", "command": ["npx", "-y", "@modelcontextprotocol/server-filesystem", "D:/photos"]}
    assert _canonical_server(chosen)["command"][-1] == "D:/photos"
    assert _canonical_server(chosen)["workspace_bound"] is True
    other = {"name": "time", "command": ["uvx", "mcp-server-time", "kazma-data/workspace"]}
    assert _canonical_server(other) == other


@pytest.mark.parametrize("arg, old", [
    ("kazma-data/workspace", True),
    ("./kazma-data/workspace", True),
    ("kazma-data\workspace\\", True),
    ("data/workspace", True),
    ("D:/photos", False),
    ("kazma-data/workspace/sub", False),
])
def test_old_sandbox_spellings(arg, old) -> None:
    from kazma_core.workspace.mcp_rebind import is_legacy_sandbox_arg

    assert is_legacy_sandbox_arg(arg) is old


def test_the_install_s_own_sandbox_matches_in_any_spelling() -> None:
    """The absolute <data dir>/workspace was compared as typed against a
    lowercased, slash-normalized argument: on Windows it never matched."""
    from kazma_core.paths import data_dir
    from kazma_core.workspace.mcp_rebind import is_legacy_sandbox_arg

    sandbox = str(data_dir() / "workspace")
    assert is_legacy_sandbox_arg(sandbox)
    assert is_legacy_sandbox_arg(sandbox.upper().replace("\\", "/") + "/")


def test_the_mcp_page_shows_the_folder_a_bound_server_runs_on(tmp_path, monkeypatch) -> None:
    """The page showed the stored `kazma-data/workspace` over a server the
    connect had pinned to the install folder."""
    import kazma_core.workspace.mcp_rebind as rebind
    from kazma_core.agent_runner import KazmaAgent

    monkeypatch.setattr(rebind, "resolve_active_root", lambda: tmp_path)
    fake = SimpleNamespace(
        get_mcp_servers_config=lambda: [dict(_STALE_FILESYSTEM)],
        tools=SimpleNamespace(is_server_connected=lambda name: False),
    )
    (shown,) = KazmaAgent.get_mcp_servers(fake)
    assert shown["command"][-1] == str(tmp_path.resolve())
