"""MCP servers: what the pages show is what runs, and a switch switches (2026-10-02).

Found through Settings -> MCP on the live install:

* The switch read every server as on. The list it reads (``GET
  /api/mcp/servers``) carried no ``enabled``, so "off, then on" sent off twice
  and left the server off for the next start.
* Off changed nothing until a restart: the route saved the flag and the
  server kept serving its tools. Settings' delete left it running too.
* The state dot read ``connected``, which no row carries: always red.
* Storage held runtime state: ``upsert_mcp_server`` wrote ``connected:
  false``, ``tool_count: 0`` and ``tools: []`` into kazma.yaml for a server
  running with fourteen tools.
* Settings' own Test started a workspace-bound server on the literal
  ``${KAZMA_ACTIVE_WORKSPACE}`` (Settings now calls the MCP page's Test).

Under them, in the manager:

* Every ``connect_from_config`` call cleared every server's recorded failure.
  Boot makes one call per server, so only the last failure survived, and the
  reconnect sweeper -- which retries the servers named there -- never retried
  the others.
* The sweeper passed the stored config, so a filesystem server it retried
  started on the placeholder.
* A failure's text, shown on the MCP pages and logged, could quote a URL key.
"""

from __future__ import annotations

import asyncio
import re
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml
from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient

from kazma_core.mcp.manager import AsyncMCPManager, MCPBridgeError, MCPServerHandle, UnifiedToolExecutor

ROOT = Path(__file__).resolve().parents[1]
SETTINGS_HTML = ROOT / "kazma-ui" / "kazma_ui" / "templates" / "settings.html"
FILESYSTEM = ["npx", "-y", "@modelcontextprotocol/server-filesystem", "${KAZMA_ACTIVE_WORKSPACE}"]


@pytest.fixture()
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The MCP store on a temp kazma.yaml and a dict for the settings store;
    the Settings service pointed at them (it would find the install's own
    kazma.yaml otherwise)."""
    yaml_path = tmp_path / "kazma.yaml"
    yaml_path.write_text(yaml.safe_dump({"agent": {"name": "t"}, "mcp": {"servers": []}}), encoding="utf-8")
    data: dict[str, object] = {}
    cs = MagicMock()
    cs.get.side_effect = lambda key, default=None: data.get(key, default)
    cs.set.side_effect = lambda key, value, category="general": data.__setitem__(key, value)
    cs.batch_set.side_effect = lambda items: [data.__setitem__(k, v) for k, v, _c in items]
    import kazma_core.settings_mcp as settings_mcp

    monkeypatch.setattr(settings_mcp, "_agent_yaml_path", lambda: str(yaml_path))
    monkeypatch.setattr(settings_mcp, "_agent_config_raw", lambda: None)
    with patch("kazma_core.config_store.get_config_store", return_value=cs):
        yield SimpleNamespace(yaml_path=yaml_path, data=data)


def _on_disk(yaml_path: Path) -> list[dict]:
    return (yaml.safe_load(yaml_path.read_text(encoding="utf-8")).get("mcp") or {}).get("servers") or []


# ── storage holds configuration, never runtime state ────────────────────


RUNTIME = {"status": "running", "connected": True, "tool_count": 14, "tools": [{"name": "read_file"}],
           "connection_error": "x", "oauth_status": "none", "oauth_required": True,
           "_resolved_workspace": "C:/old/root"}


def test_runtime_fields_are_never_stored(store) -> None:
    from kazma_core.mcp_servers_store import list_mcp_servers, upsert_mcp_server

    upsert_mcp_server({"name": "time", "command": ["uvx", "mcp-server-time"], **RUNTIME}, yaml_path=store.yaml_path)
    import json

    for stored in (json.loads(store.data["mcp.servers"]), list_mcp_servers(yaml_path=store.yaml_path)):
        (row,) = stored
        assert not set(row) & set(RUNTIME), row
        assert row["command"] == ["uvx", "mcp-server-time"] and row["enabled"] is True
    assert _on_disk(store.yaml_path) == []  # kazma.yaml is never written


def test_an_old_row_reads_without_them(store) -> None:
    """kazma.yaml on the live install held ``connected: false`` etc."""
    from kazma_core.mcp_servers_store import list_mcp_servers

    store.yaml_path.write_text(yaml.safe_dump({"mcp": {"servers": [
        {"name": "seq", "command": ["npx", "-y", "@modelcontextprotocol/server-sequential-thinking"],
         "enabled": True, "connected": False, "tool_count": 0, "tools": []},
    ]}}), encoding="utf-8")
    (row,) = list_mcp_servers(yaml_path=store.yaml_path)
    assert {"connected", "tool_count", "tools"}.isdisjoint(row)


def test_negative_control_without_the_rule_they_are_stored(store, monkeypatch) -> None:
    import kazma_core.mcp_servers_store as mss

    import json

    monkeypatch.setattr(mss, "_RUNTIME_FIELDS", frozenset())
    mss.upsert_mcp_server({"name": "time", "command": ["uvx", "mcp-server-time"], **RUNTIME}, yaml_path=store.yaml_path)
    (row,) = json.loads(store.data["mcp.servers"])
    assert row["connected"] is True and row["tool_count"] == 14


# ── the list every page reads carries the switch ────────────────────────


class _AgentView:
    """The agent's own listing over a fake connection state."""

    def __init__(self, configs: list[dict], running: set[str]) -> None:
        self._configs = configs
        self.tools = SimpleNamespace(
            is_server_connected=lambda name: name in running,
            get_mcp_tools_for_server=lambda name: [{"name": "t", "description": ""}] if name in running else [],
            _mcp=None,
        )

    def get_mcp_servers_config(self) -> list[dict]:
        return [dict(c) for c in self._configs]

    def _mcp_yaml_path(self):
        return None

    from kazma_core.agent_runner import KazmaAgent as _KA

    get_mcp_servers = _KA.get_mcp_servers
    del _KA


def test_the_list_says_which_servers_are_switched_on() -> None:
    view = _AgentView([{"name": "on", "command": ["a"]}, {"name": "off", "command": ["b"], "enabled": False}], {"on"})
    rows = {r["name"]: r for r in view.get_mcp_servers()}
    assert rows["on"]["enabled"] is True and rows["on"]["status"] == "running"
    assert rows["off"]["enabled"] is False and rows["off"]["status"] == "stopped"


def _mcp_row_fields() -> set[str]:
    html = SETTINGS_HTML.read_text(encoding="utf-8")
    start = html.index('<template x-for="s in mcpServers"')
    end = html.index("</template>\n\n            <div x-show=\"mcpServers.length === 0", start)
    return set(re.findall(r"\bs\.(\w+)", html[start:end]))


def unsent_fields(read: set[str], row: dict) -> list[str]:
    return sorted(read - set(row))


def test_the_settings_row_reads_only_what_every_row_carries(tmp_path) -> None:
    """Through the real route the Settings tab calls. The state dot read
    ``s.connected``: no row carries it (one with a connection error gets
    ``connected: false``), so every dot was red."""
    from kazma_ui.mcp_ui import create_mcp_router

    app = FastAPI()
    view = _AgentView([{"name": "fs", "command": ["a"]}], {"fs"})
    app.include_router(create_mcp_router(view, Jinja2Templates(directory=str(tmp_path))))
    (row,) = TestClient(app).get("/api/mcp/servers").json()
    read = _mcp_row_fields()
    assert {"status", "enabled", "tool_count", "name"} <= read, read
    assert unsent_fields(read, row) == []


def test_negative_control_the_old_state_dot_is_caught() -> None:
    row = {"name": "fs", "status": "running", "tool_count": 1, "enabled": True}
    assert unsent_fields({"connected", "name"}, row) == ["connected"]


# ── the switch applies now ──────────────────────────────────────────────


def _settings_client(tmp_path: Path, agent: MagicMock) -> TestClient:
    from kazma_core.config_store import ConfigStore
    from kazma_ui.settings import create_settings_router

    app = FastAPI()
    app.include_router(create_settings_router(
        agent, ConfigStore(db_path=str(tmp_path / "settings.db")), Jinja2Templates(directory=str(tmp_path)),
    ))
    return TestClient(app)


def _agent(running: set[str], *, start_error: str = "") -> MagicMock:
    agent = MagicMock()
    calls: list[tuple[str, str]] = []

    async def start(name: str) -> dict:
        calls.append(("start", name))
        if start_error:
            return {"status": "error", "error": start_error}
        running.add(name)
        return {"status": "ok", "tool_count": 1}

    async def stop(name: str) -> dict:
        calls.append(("stop", name))
        running.discard(name)
        return {"status": "ok"}

    agent.start_mcp_server = start
    agent.stop_mcp_server = stop
    agent.tools.is_server_connected = lambda name: name in running
    agent.calls = calls
    return agent


def _seed(store, *servers: dict) -> None:
    from kazma_core.mcp_servers_store import upsert_mcp_server

    for server in servers:
        upsert_mcp_server(server, yaml_path=store.yaml_path)


def _stored(store, name: str) -> dict:
    from kazma_core.mcp_servers_store import list_mcp_servers

    return next(s for s in list_mcp_servers(yaml_path=store.yaml_path) if s["name"] == name)


def test_off_stops_the_server_and_on_starts_it(store, tmp_path) -> None:
    _seed(store, {"name": "seq", "command": ["npx", "-y", "x"]})
    running = {"seq"}
    agent = _agent(running)
    client = _settings_client(tmp_path, agent)

    off = client.put("/api/settings/mcp/seq/toggle", json={"enabled": False})
    assert off.status_code == 200
    assert off.json() == {"status": "ok", "enabled": False, "running": False}
    assert _stored(store, "seq")["enabled"] is False and "seq" not in running

    on = client.put("/api/settings/mcp/seq/toggle", json={"enabled": True})
    assert on.json() == {"status": "ok", "enabled": True, "running": True}
    assert _stored(store, "seq")["enabled"] is True
    assert agent.calls == [("stop", "seq"), ("start", "seq")]


def test_on_that_cannot_start_is_saved_and_says_why(store, tmp_path) -> None:
    _seed(store, {"name": "seq", "command": ["npx", "-y", "x"], "enabled": False})
    agent = _agent(set(), start_error="Failed to start server: npx not found")
    body = _settings_client(tmp_path, agent).put("/api/settings/mcp/seq/toggle", json={"enabled": True}).json()
    assert body == {"status": "ok", "enabled": True, "running": False,
                    "error": "Failed to start server: npx not found"}
    assert _stored(store, "seq")["enabled"] is True  # it applies at the next start


def test_an_unknown_server_is_404_and_nothing_runs(store, tmp_path) -> None:
    agent = _agent(set())
    resp = _settings_client(tmp_path, agent).put("/api/settings/mcp/nope/toggle", json={"enabled": True})
    assert resp.status_code == 404
    assert agent.calls == []


def test_settings_delete_stops_a_running_server(store, tmp_path) -> None:
    _seed(store, {"name": "seq", "command": ["npx", "-y", "x"]})
    running = {"seq"}
    agent = _agent(running)
    resp = _settings_client(tmp_path, agent).delete("/api/settings/mcp/seq")
    assert resp.status_code == 200 and resp.json() == {"status": "ok"}
    assert agent.calls == [("stop", "seq")] and running == set()


def test_settings_test_is_the_mcp_pages_test() -> None:
    """One Test: the Settings copy pinned nothing (the MCP page's pins)."""
    from kazma_ui.settings import create_settings_router

    js = (ROOT / "kazma-ui" / "kazma_ui" / "static" / "js" / "settings_integrations.js").read_text(encoding="utf-8")
    assert "fetch(`/api/mcp/servers/${encodeURIComponent(name)}/test`" in js
    paths = {getattr(r, "path", "") for r in create_settings_router(MagicMock(), MagicMock(), MagicMock()).routes}
    assert "/api/settings/mcp/{name}/test" not in paths


# ── Start and Stop: one copy, the error the connection recorded ─────────


async def test_start_reports_the_recorded_failure() -> None:
    from kazma_core.agent_runner import KazmaAgent

    fake = SimpleNamespace(
        get_mcp_servers_config=lambda: [{"name": "fs", "command": ["npx"]}],
        tools=SimpleNamespace(
            connect_server=AsyncMock(return_value=0),
            is_server_connected=lambda name: False,
            _mcp=SimpleNamespace(connection_errors={"fs": "spawn npx ENOENT"}),
        ),
    )
    result = await KazmaAgent.start_mcp_server(fake, "fs")
    assert result == {"status": "error", "error": "Failed to start server: spawn npx ENOENT"}
    assert await KazmaAgent.start_mcp_server(fake, "nope") == {
        "status": "error", "error": "Server 'nope' not found in config"}


def test_the_mcp_pages_start_and_stop_are_the_agents(tmp_path) -> None:
    from kazma_ui.mcp_ui import create_mcp_router

    agent = _agent(set())
    app = FastAPI()
    app.include_router(create_mcp_router(agent, Jinja2Templates(directory=str(tmp_path))))
    client = TestClient(app)
    assert client.post("/api/mcp/servers/fs/start").json() == {"status": "ok", "tool_count": 1}
    assert client.post("/api/mcp/servers/fs/stop").json() == {"status": "ok"}
    assert agent.calls == [("start", "fs"), ("stop", "fs")]


# ── the manager keeps each server's failure ─────────────────────────────


def _manager(fail: set[str], seen: dict | None = None) -> AsyncMCPManager:
    manager = AsyncMCPManager()

    async def connect(name: str, cfg: dict) -> int:
        if seen is not None:
            seen[name] = list(cfg.get("command") or [])
        if name in fail:
            raise MCPBridgeError(f"{name} is broken")
        manager._servers[name] = MCPServerHandle(name=name, transport="stdio", connected=True, tools=[{"name": "t"}])
        return 1

    manager._connect_stdio = connect  # type: ignore[method-assign]
    return manager


@pytest.fixture()
def alerts(monkeypatch):
    sent: list[tuple] = []
    import kazma_core.observability.ops_alerts as ops_alerts

    monkeypatch.setattr(ops_alerts, "alert", lambda key, title, detail="", **kw: sent.append((key, title, detail)))
    return sent


async def test_a_failure_survives_the_next_servers_connect(alerts) -> None:
    """Boot's shape: one call per server, the first fails."""
    manager = _manager({"a"})
    await manager.connect_from_config([{"name": "a", "transport": "stdio", "command": ["x"]}])
    await manager.connect_from_config([{"name": "b", "transport": "stdio", "command": ["y"]}])
    assert manager.connection_errors == {"a": "a is broken"}


async def test_a_strict_call_judges_only_its_own_servers(alerts) -> None:
    manager = _manager({"a"})
    await manager.connect_from_config([{"name": "a", "transport": "stdio", "command": ["x"]}])
    assert await manager.connect_from_config(
        [{"name": "b", "transport": "stdio", "command": ["y"]}], raise_on_error=True) == 1
    with pytest.raises(MCPBridgeError, match="a is broken"):
        await manager.connect_from_config([{"name": "a", "transport": "stdio", "command": ["x"]}], raise_on_error=True)


async def test_connecting_or_stopping_clears_only_that_server(alerts) -> None:
    failing = {"a", "b"}
    manager = _manager(failing)
    await manager.connect_from_config([{"name": "a", "transport": "stdio", "command": ["x"]}])
    await manager.connect_from_config([{"name": "b", "transport": "stdio", "command": ["y"]}])
    await manager.disconnect_server("a")  # someone stopped it: not down
    assert manager.connection_errors == {"b": "b is broken"}
    failing.discard("b")
    await manager.connect_from_config([{"name": "b", "transport": "stdio", "command": ["y"]}])
    assert manager.connection_errors == {}


async def test_the_alert_names_every_server_that_is_down(alerts) -> None:
    manager = _manager({"a", "b"})
    await manager.connect_from_config([{"name": "a", "transport": "stdio", "command": ["x"]}])
    await manager.connect_from_config([{"name": "b", "transport": "stdio", "command": ["y"]}])
    key, title, detail = alerts[-1]
    assert key == "mcp.servers_unavailable" and title.startswith("2 MCP server(s)")
    assert "a: a is broken" in detail and "b: b is broken" in detail


async def test_the_sweeper_retries_a_server_that_failed_early(alerts) -> None:
    """End to end: boot fails 'a', connects 'b'; the sweeper brings 'a' back."""
    from kazma_core.mcp.reconnect import MCPReconnector

    attempts = {"a": 0}
    manager = AsyncMCPManager()

    async def connect(name: str, cfg: dict) -> int:
        if name == "a":
            attempts["a"] += 1
            if attempts["a"] == 1:
                raise MCPBridgeError("a is not up yet")
        manager._servers[name] = MCPServerHandle(name=name, transport="stdio", connected=True, tools=[{"name": "t"}])
        return 1

    manager._connect_stdio = connect  # type: ignore[method-assign]
    configs = [{"name": "a", "transport": "stdio", "command": ["x"]},
               {"name": "b", "transport": "stdio", "command": ["y"]}]
    for cfg in configs:  # boot: one call per server
        await manager.connect_from_config([cfg])
    recovered = await MCPReconnector(manager, lambda: configs).sweep_once()
    assert recovered == 1 and attempts["a"] == 2
    assert manager.connection_errors == {}


# ── a workspace-bound server starts on the active workspace ─────────────


async def test_an_unpinned_filesystem_server_starts_on_the_active_workspace(tmp_path, monkeypatch, alerts) -> None:
    """The sweeper's and Settings' Test's shape: the stored config. The root
    is a workspace-store read, made in a worker thread."""
    from kazma_core.workspace import binding

    reads: list[int] = []

    def active_root() -> Path:
        reads.append(threading.get_ident())
        return tmp_path

    monkeypatch.setattr(binding, "resolve_active_root", active_root)
    seen: dict = {}
    manager = _manager(set(), seen)
    await manager.connect_from_config([{"name": "filesystem", "transport": "stdio", "command": FILESYSTEM}])
    assert seen["filesystem"][-1] == str(tmp_path.resolve())
    assert reads and reads[0] != threading.get_ident()


async def test_a_pinned_config_keeps_its_folder(tmp_path, monkeypatch, alerts) -> None:
    """A per-task scope or a rebind pins its own folder: never re-pinned."""
    from kazma_core.workspace import binding

    monkeypatch.setattr(binding, "resolve_active_root", lambda: pytest.fail("read the active root"))
    seen: dict = {}
    manager = _manager(set(), seen)
    scoped = str(tmp_path / "scoped")
    await manager.connect_from_config([{
        "name": "filesystem", "transport": "stdio", "command": [*FILESYSTEM[:-1], scoped],
        "_resolved_workspace": scoped,
    }])
    assert seen["filesystem"][-1] == scoped


async def test_negative_control_without_the_pin_the_placeholder_is_spawned(monkeypatch, alerts) -> None:
    import kazma_core.mcp.manager as manager_mod

    monkeypatch.setattr(manager_mod, "_needs_workspace_pin", lambda cfg: False)
    seen: dict = {}
    manager = _manager(set(), seen)
    await manager.connect_from_config([{"name": "filesystem", "transport": "stdio", "command": FILESYSTEM}])
    assert seen["filesystem"][-1] == "${KAZMA_ACTIVE_WORKSPACE}"


async def test_the_executor_installs_the_rebind_off_the_loop(monkeypatch) -> None:
    """It read the active root on the loop on every connect."""
    import kazma_core.workspace.mcp_rebind as rebind

    threads: list[int] = []
    monkeypatch.setattr(rebind, "install_mcp_workspace_rebind", lambda ex: threads.append(threading.get_ident()))
    mcp = SimpleNamespace(connect_from_config=AsyncMock(return_value=3))
    executor = UnifiedToolExecutor(local=None, mcp=mcp)
    cfg = {"name": "filesystem", "transport": "stdio", "command": FILESYSTEM}
    assert await executor.connect_server(cfg) == 3
    assert threads and threads[0] != threading.get_ident()
    mcp.connect_from_config.assert_awaited_once_with([cfg])  # the manager pins it


# ── a failure's text never carries a key ────────────────────────────────


async def test_a_recorded_failure_has_no_key(alerts) -> None:
    manager = AsyncMCPManager()

    async def connect(name: str, cfg: dict) -> int:
        raise MCPBridgeError("Client error '401' for url 'https://h.example/mcp?api_key=sk-live-abcdef123456'")

    manager._connect_streamable_http = connect  # type: ignore[method-assign]
    await manager.connect_from_config([{"name": "remote", "transport": "http", "url": "https://h.example/mcp"}])
    message = manager.connection_errors["remote"]
    assert "sk-live-abcdef123456" not in message and "401" in message


def test_redact_secrets_keeps_the_path() -> None:
    """The operator's answer stays: which file was not found."""
    from kazma_core.errors import redact_secrets

    text = r"[WinError 2] not found: C:\Program Files\nodejs\npx.cmd; token=abc123; https://u:pw@db.example/x"
    out = redact_secrets(text)
    assert r"C:\Program Files\nodejs\npx.cmd" in out
    assert "abc123" not in out and "pw@" not in out


_OLD_REDACT = re.compile(
    r"""(
        \b[A-Za-z]:[\\/](?![\\/])[^\s'"]*   # Windows absolute paths (not the "s://" of a URL)
      | /(?:home|Users|root|etc|var|opt)/[^\s'"]*   # POSIX absolute paths
      | (?:password|secret|token|api[_-]?key)\s*[=:]\s*\S+
      | \b(?:sk|xox[baprs]|ghp|gho|ghu|ghs|AIza)[-_][A-Za-z0-9_-]{8,}
      | [a-z][a-z0-9+.-]*://[^/\s:@'"]+:[^@\s/'"]+@  # URL userinfo (a DSN's password)
    )""",
    re.IGNORECASE | re.VERBOSE,
)


@pytest.mark.parametrize("text", [
    r"open C:\Users\me\x.db failed", "/home/me/.ssh/id_rsa", "password = hunter2", "api-key: abc",
    "ghp_abcdefghijklmnop", "postgres://kazma:secret@db:5432/k", "plain message", "https://h/x?token=t",
])
def test_the_api_redactor_is_unchanged(text) -> None:
    """Splitting the pattern in two changed nothing the API returns."""
    from kazma_core.errors import _REDACT

    assert _REDACT.sub("<redacted>", text) == _OLD_REDACT.sub("<redacted>", text)


# ── /config tools toggle applies now too ────────────────────────────────


def _toggle(reply_ctx: dict, monkeypatch, *, enabled: bool = True) -> str:
    import kazma_core.mcp_servers_store as mss
    from kazma_gateway import slash_commands

    monkeypatch.setattr(slash_commands, "_mcp_servers", lambda: [{"name": "seq", "enabled": enabled}])
    monkeypatch.setattr(mss, "set_mcp_server_enabled", lambda name, on: True)
    return slash_commands.resolve_slash_command("/config tools toggle seq", context=reply_ctx)


def test_the_chat_toggle_stops_the_server_now(monkeypatch) -> None:
    calls: list = []
    reply = _toggle({"apply_mcp_server": lambda name, on: calls.append((name, on)) or {"status": "ok"}}, monkeypatch)
    assert calls == [("seq", False)]
    assert "disabled" in reply and "stopped now" in reply


def test_the_chat_toggle_says_when_it_could_not_apply(monkeypatch) -> None:
    reply = _toggle({"apply_mcp_server": lambda name, on: {"status": "error", "error": "npx not found"}},
                    monkeypatch, enabled=False)
    assert "enabled" in reply and "could not start now: npx not found" in reply
    assert "next starts" in reply


def test_the_chat_toggle_without_a_running_agent_applies_at_the_next_start(monkeypatch) -> None:
    assert "It applies when Kazma next starts." in _toggle({}, monkeypatch)


async def test_the_gateway_runs_the_switch_on_its_loop(monkeypatch) -> None:
    """The resolver runs in a thread; start and stop run on the server loop."""
    from kazma_core.agent_runner import KazmaAgent
    from kazma_core.service_container import get_container
    from kazma_gateway.agent_handler import commands

    loop_thread = threading.get_ident()
    ran_on: list[int] = []

    async def stop(name: str) -> dict:
        ran_on.append(threading.get_ident())
        return {"status": "ok"}

    agent = SimpleNamespace(stop_mcp_server=stop, start_mcp_server=AsyncMock())

    def get(key):
        if key is KazmaAgent:
            return agent
        raise KeyError(key)

    monkeypatch.setattr(get_container(), "get", get)
    switch = commands._mcp_switcher(asyncio.get_running_loop())
    result = await asyncio.to_thread(switch, "seq", False)
    assert result == {"status": "ok"} and ran_on == [loop_thread]
