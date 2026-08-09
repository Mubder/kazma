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
