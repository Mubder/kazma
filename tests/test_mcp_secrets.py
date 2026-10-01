"""An MCP server's credentials live in the vault; its configuration holds pointers.

Until 2026-09-30 every MCP server secret -- the ``BRAVE_API_KEY`` a preset asks
for, an ``auth`` token, a key typed into the command as Stripe documents it --
was written as typed into ``kazma.yaml`` (a tracked file in the install's
checkout) and into the settings database; ``GET /api/settings/mcp`` and
``/api/mcp/servers`` returned them; an argument-style key was logged with the
command that started the server; and the "Test" button started a server with
Kazma's whole environment -- the vault key, the database password -- because
``mcp_client`` never got audit H-4's allowlist.

Behaviour here runs the real store, a real vault on a temporary file, the real
manager and the real settings routes; the source gates below keep every write
on the vault step and every transport on the resolve step, each with a
negative control.
"""

from __future__ import annotations

import ast
import asyncio
import json
import logging
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml
from kazma_core.security import vault as vault_mod

REPO = Path(__file__).resolve().parents[1]
BRAVE = "demo-brave-api-key-0123456789"
TOKEN = "demo-bearer-token-abcdef"
STRIPE = "demo-stripe-secret-key-9876543210"


@pytest.fixture
def vault(tmp_path, monkeypatch):
    monkeypatch.setenv("KAZMA_VAULT_KEY", "k" * 64)
    v = vault_mod.SecretVault(db_path=str(tmp_path / "vault.db"))
    monkeypatch.setattr(vault_mod, "get_vault", lambda: v)
    yield v
    v.close()


@pytest.fixture
def yaml_path(tmp_path):
    path = tmp_path / "kazma.yaml"
    path.write_text(yaml.safe_dump({"agent": {"name": "t"}, "mcp": {"servers": []}}), encoding="utf-8")
    return path


def _server(**over):
    server = {
        "name": "brave_search",
        "transport": "stdio",
        "command": ["npx", "-y", "@stripe/mcp", f"--api-key={STRIPE}", "--verbose"],
        "env": {"BRAVE_API_KEY": BRAVE, "LOG_LEVEL": "debug"},
        "auth": {"type": "bearer", "token": TOKEN},
    }
    server.update(over)
    return server


def _stored_text(yaml_path) -> str:
    from kazma_core.config_store import get_config_store
    from kazma_core.mcp_servers_store import CONFIG_KEY

    return yaml_path.read_text(encoding="utf-8") + json.dumps(get_config_store().get(CONFIG_KEY))


def _stored_server(name="brave_search"):
    from kazma_core.mcp_servers_store import _cs_get

    return next(s for s in _cs_get() if s["name"] == name)


# ── storage ─────────────────────────────────────────────────────────────


def test_a_saved_key_is_a_pointer_everywhere(vault, yaml_path):
    from kazma_core.mcp_servers_store import upsert_mcp_server

    upsert_mcp_server(_server(), yaml_path=yaml_path)
    text = _stored_text(yaml_path)
    for secret in (BRAVE, TOKEN, STRIPE):
        assert secret not in text, secret
    assert "debug" in text, "a setting that is not a secret stays readable"
    stored = _stored_server()
    assert stored["env"]["BRAVE_API_KEY"] == "vault://cfg:mcp.servers.brave_search.env.BRAVE_API_KEY"
    assert stored["auth"]["token"] == "vault://cfg:mcp.servers.brave_search.auth.token"
    assert stored["command"][3] == "--api-key=vault://cfg:mcp.servers.brave_search.command.3"
    # Install scope: the manager connects at boot with no tenant bound.
    assert vault.retrieve("cfg:mcp.servers.brave_search.env.BRAVE_API_KEY") == BRAVE
    assert vault_mod.is_install_scoped_secret("cfg:mcp.servers.brave_search.auth.token")


def test_the_server_gets_the_real_values(vault, yaml_path):
    from kazma_core.mcp.manager import AsyncMCPManager
    from kazma_core.mcp_servers_store import upsert_mcp_server

    upsert_mcp_server(_server(), yaml_path=yaml_path)
    live = AsyncMCPManager._with_secrets("brave_search", _stored_server())
    assert live["env"]["BRAVE_API_KEY"] == BRAVE
    assert live["auth"]["token"] == TOKEN
    assert live["command"][3] == f"--api-key={STRIPE}"
    assert AsyncMCPManager._build_child_env("brave_search", live)["BRAVE_API_KEY"] == BRAVE


def test_a_masked_value_posted_back_keeps_the_key(vault, yaml_path):
    from kazma_core.mcp.secrets import masked
    from kazma_core.mcp_servers_store import upsert_mcp_server

    upsert_mcp_server(_server(), yaml_path=yaml_path)
    before = _stored_server()
    shown = masked(before)
    assert shown["env"]["BRAVE_API_KEY"] == "****" and shown["command"][3] == "--api-key=****"
    upsert_mcp_server(shown, yaml_path=yaml_path)  # what a form posts back
    assert _stored_server() == before
    assert vault.retrieve("cfg:mcp.servers.brave_search.env.BRAVE_API_KEY") == BRAVE

    upsert_mcp_server(_server(env={"BRAVE_API_KEY": "demo-brave-new-key"}), yaml_path=yaml_path)
    assert vault.retrieve("cfg:mcp.servers.brave_search.env.BRAVE_API_KEY") == "demo-brave-new-key"


def test_deleting_a_server_removes_its_secrets(vault, yaml_path):
    from kazma_core.mcp_servers_store import delete_mcp_server, upsert_mcp_server

    upsert_mcp_server(_server(), yaml_path=yaml_path)
    upsert_mcp_server(_server(name="other", env={"BRAVE_API_KEY": "demo-brave-other-key"}), yaml_path=yaml_path)
    delete_mcp_server("brave_search", yaml_path=yaml_path)
    assert vault.retrieve("cfg:mcp.servers.brave_search.env.BRAVE_API_KEY") is None
    assert vault.retrieve("cfg:mcp.servers.brave_search.auth.token") is None
    assert vault.retrieve("cfg:mcp.servers.other.env.BRAVE_API_KEY") == "demo-brave-other-key"


def test_secrets_stored_as_typed_move_to_the_vault(vault, yaml_path):
    """The shape every install has from before 2026-09-30."""
    from kazma_core.config_store import get_config_store
    from kazma_core.mcp_servers_store import CONFIG_KEY, move_plaintext_secrets

    old = _server()
    get_config_store().set(CONFIG_KEY, json.dumps([old]), category="mcp")
    yaml_path.write_text(yaml.safe_dump({"mcp": {"servers": [old]}}), encoding="utf-8")
    assert BRAVE in _stored_text(yaml_path), "the control: plaintext on both stores"

    assert move_plaintext_secrets(yaml_path=yaml_path) == 1
    assert BRAVE not in _stored_text(yaml_path) and TOKEN not in _stored_text(yaml_path)
    assert vault.retrieve("cfg:mcp.servers.brave_search.env.BRAVE_API_KEY") == BRAVE

    mtime = yaml_path.stat().st_mtime_ns
    assert move_plaintext_secrets(yaml_path=yaml_path) == 0
    assert yaml_path.stat().st_mtime_ns == mtime, "nothing left: nothing written"


def test_without_a_vault_the_servers_still_work(monkeypatch, yaml_path):
    from kazma_core.mcp.secrets import resolve
    from kazma_core.mcp_servers_store import move_plaintext_secrets, upsert_mcp_server

    monkeypatch.setattr(vault_mod, "get_vault", lambda: None)
    upsert_mcp_server(_server(), yaml_path=yaml_path)
    stored = _stored_server()
    assert stored["env"]["BRAVE_API_KEY"] == BRAVE, "kept as typed, with a warning"
    assert resolve(stored) is stored
    mtime = yaml_path.stat().st_mtime_ns
    assert move_plaintext_secrets(yaml_path=yaml_path) == 0
    assert yaml_path.stat().st_mtime_ns == mtime


def test_a_pointer_the_vault_cannot_answer_names_the_field(vault, yaml_path, monkeypatch):
    from kazma_core.mcp.manager import AsyncMCPManager, MCPBridgeError
    from kazma_core.mcp_servers_store import upsert_mcp_server

    upsert_mcp_server(_server(), yaml_path=yaml_path)
    stored = _stored_server()
    monkeypatch.setattr(vault_mod, "get_vault", lambda: None)
    with pytest.raises(MCPBridgeError, match="brave_search.*env.BRAVE_API_KEY.*vault"):
        AsyncMCPManager._with_secrets("brave_search", stored)


# ── what leaves the process ─────────────────────────────────────────────


def test_the_start_log_never_prints_a_key(vault, caplog):
    """The real stdio transport, up to the spawn (the command does not exist)."""
    from kazma_core.mcp.manager import AsyncMCPManager, MCPBridgeError

    cfg = {
        "name": "leaky",
        "transport": "stdio",
        "command": ["kazma-no-such-command-xyz", f"--api-key={STRIPE}", "--token", TOKEN],
        "auth": {"type": "arg", "name": "--secret", "value": BRAVE},
    }
    with caplog.at_level(logging.INFO, logger="kazma_core.mcp.manager"):
        with pytest.raises((MCPBridgeError, OSError)):
            asyncio.run(AsyncMCPManager()._connect_stdio("leaky", cfg))
    started = [r.getMessage() for r in caplog.records if "Starting stdio server" in r.getMessage()]
    assert started, "the start line was not logged; the test proves nothing"
    for secret in (STRIPE, TOKEN, BRAVE):
        assert secret not in "\n".join(started), secret


def test_redacted_argv_masks_every_shape():
    from kazma_core.mcp.secrets import redacted_argv

    argv = ["npx", "-y", "pkg", f"--api-key={STRIPE}", "--token", TOKEN, "https://u:pw@host/x", "plain"]
    out = redacted_argv(argv)
    assert out[:3] == ["npx", "-y", "pkg"] and out[-1] == "plain"
    assert out[3] == "--api-key=****" and out[5] == "****"
    assert "pw" not in out[6]
    assert redacted_argv(["x", BRAVE], secrets=(BRAVE,)) == ["x", "****"]


def test_test_client_starts_a_server_without_kazmas_secrets(monkeypatch):
    """The Test button's client: the manager's allowlist, the server's own env."""
    from kazma_core import mcp_client

    monkeypatch.setenv("KAZMA_VAULT_KEY", "vault-key-must-not-leak")
    monkeypatch.setenv("KAZMA_SECRET", "kazma-secret-must-not-leak")
    monkeypatch.setenv("OPENAI_API_KEY", "demo-openai-key-must-not-leak")
    seen: dict = {}

    def fake_popen(command, **kwargs):
        seen.update(kwargs)
        raise FileNotFoundError(command[0])

    monkeypatch.setattr(mcp_client.subprocess, "Popen", fake_popen)
    client = mcp_client.MCPClient()
    cfg = mcp_client.MCPServerConfig(name="probe", command=["probe"], env={"BRAVE_API_KEY": BRAVE})
    with pytest.raises(mcp_client.MCPConnectionError):
        asyncio.run(client.connect(cfg))
    env = seen["env"]
    assert env["BRAVE_API_KEY"] == BRAVE
    for leaked in ("KAZMA_VAULT_KEY", "KAZMA_SECRET", "OPENAI_API_KEY"):
        assert leaked not in env, leaked
    assert "PATH" in env or "Path" in env


def test_the_old_test_client_line_is_flagged():
    """Negative control: the gate sees the line mcp_client had until 2026-09-30."""
    from tests.test_child_env import _unsafe_spawns

    old = (
        "import os, subprocess\n"
        "async def _connect_stdio(self, cfg):\n"
        "    env = {**os.environ, **cfg.env}\n"
        "    self._process = subprocess.Popen(cfg.command, env=env)\n"
    )
    assert _unsafe_spawns(old)


@pytest.fixture
def settings_client(tmp_path):
    from fastapi import FastAPI
    from fastapi.templating import Jinja2Templates
    from fastapi.testclient import TestClient
    from kazma_core.config_store import ConfigStore
    from kazma_ui.settings import create_settings_router

    (tmp_path / "templates").mkdir()
    (tmp_path / "templates" / "settings.html").write_text("ok", encoding="utf-8")
    agent = MagicMock()
    app = FastAPI()
    app.include_router(create_settings_router(
        agent, ConfigStore(db_path=str(tmp_path / "s.db")), Jinja2Templates(directory=str(tmp_path / "templates")),
    ))
    return TestClient(app)


class _AgentView:
    """What ``KazmaAgent.get_mcp_servers`` reads: its config, its kazma.yaml
    and its tools -- with the agent's own two methods, so the listing is the
    product's."""

    def __init__(self) -> None:
        from types import SimpleNamespace

        self.config = SimpleNamespace(raw={})
        self.tools = SimpleNamespace(
            is_server_connected=lambda name: False,
            get_mcp_tools_for_server=lambda name: [],
            _mcp=None,
        )

    def _mcp_yaml_path(self):
        return None

    from kazma_core.agent_runner import KazmaAgent as _KA

    get_mcp_servers_config = _KA.get_mcp_servers_config
    get_mcp_servers = _KA.get_mcp_servers
    del _KA


def test_the_settings_api_never_returns_a_key(vault, settings_client, tmp_path):
    """Saved through Settings, listed by the route Settings and the MCP page
    read (GET /api/mcp/servers; Settings' own list was removed 2026-10-01)."""
    from fastapi import FastAPI
    from fastapi.templating import Jinja2Templates
    from fastapi.testclient import TestClient
    from kazma_ui.mcp_ui import create_mcp_router

    body = _server()
    added = settings_client.post("/api/settings/mcp", json=body)
    assert added.status_code == 200
    app = FastAPI()
    app.include_router(create_mcp_router(_AgentView(), Jinja2Templates(directory=str(tmp_path / "templates"))))
    listed = TestClient(app).get("/api/mcp/servers")
    assert listed.status_code == 200
    for response in (added, listed):
        for secret in (BRAVE, TOKEN, STRIPE):
            assert secret not in response.text, secret
    shown = next(s for s in listed.json() if s["name"] == "brave_search")
    assert shown["env"] == {"BRAVE_API_KEY": "****", "LOG_LEVEL": "debug"}


# ── the gates ───────────────────────────────────────────────────────────

_STORE = REPO / "kazma-core" / "kazma_core" / "mcp_servers_store.py"
_SINKS = {"_cs_set", "persist_mcp_yaml", "_sync_config_raw"}


def _sink_callers(source: str) -> dict[str, set[str]]:
    """``function -> sinks it calls`` for every function that calls a sink."""
    out: dict[str, set[str]] = {}
    for fn in ast.walk(ast.parse(source)):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            called = {
                n.func.id for n in ast.walk(fn)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in _SINKS
            }
            if called:
                out[fn.name] = called
    return out


def test_every_store_write_moves_secrets_to_the_vault_first():
    callers = _sink_callers(_STORE.read_text(encoding="utf-8"))
    assert set(callers) == {"_write_everywhere"}, callers
    # And nothing outside the module writes a store directly.
    outside = []
    for path in sorted((REPO).glob("kazma-*/kazma_*/**/*.py")):
        if path == _STORE:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if any(f"{sink}(" in text for sink in ("_cs_set", "persist_mcp_yaml")):
            outside.append(str(path.relative_to(REPO)))
    assert not outside, outside


def test_the_store_gate_sees_a_write_that_skips_the_vault():
    """Negative control: upsert as it was, writing both stores itself."""
    old = (
        "def upsert_mcp_server(data):\n"
        "    servers = list_mcp_servers()\n"
        "    _cs_set(servers)\n"
        "    persist_mcp_yaml(servers)\n"
        "def _write_everywhere(servers):\n"
        "    _cs_set(servers)\n"
    )
    assert set(_sink_callers(old)) == {"upsert_mcp_server", "_write_everywhere"}


_TRANSPORTS = ("_connect_stdio", "_connect_sse", "_connect_streamable_http")


def _transports_resolving(source: str) -> dict[str, bool]:
    """``transport -> its first statement (after the docstring) resolves secrets``."""
    out: dict[str, bool] = {}
    for fn in ast.walk(ast.parse(source)):
        if isinstance(fn, ast.AsyncFunctionDef) and fn.name in _TRANSPORTS:
            body = fn.body[1:] if isinstance(fn.body[0], ast.Expr) and isinstance(fn.body[0].value, ast.Constant) else fn.body
            first = ast.unparse(body[0]) if body else ""
            out[fn.name] = first.startswith("cfg = self._with_secrets(")
    return out


def test_every_transport_resolves_secrets_before_anything_else():
    manager = (REPO / "kazma-core" / "kazma_core" / "mcp" / "manager.py").read_text(encoding="utf-8")
    assert _transports_resolving(manager) == dict.fromkeys(_TRANSPORTS, True)
    client = ast.parse((REPO / "kazma-core" / "kazma_core" / "mcp_client.py").read_text(encoding="utf-8"))
    connect = next(n for n in ast.walk(client) if isinstance(n, ast.AsyncFunctionDef) and n.name == "connect")
    assert "cfg = _with_secrets(cfg)" in ast.unparse(connect)


def test_the_transport_gate_sees_a_transport_that_does_not_resolve():
    old = (
        "class M:\n"
        "    async def _connect_stdio(self, name, cfg):\n"
        "        '''Spawn.'''\n"
        "        env = self._build_child_env(name, cfg)\n"
        "    async def _connect_sse(self, name, cfg):\n"
        "        cfg = self._with_secrets(name, cfg)\n"
    )
    assert _transports_resolving(old) == {"_connect_stdio": False, "_connect_sse": True}


def test_no_subprocess_popen_on_the_test_client_loop():
    """Starting a process blocks: the Test client hands it to a thread (§23)."""
    source = (REPO / "kazma-core" / "kazma_core" / "mcp_client.py").read_text(encoding="utf-8")
    direct = [
        n.lineno for n in ast.walk(ast.parse(source))
        if isinstance(n, ast.Call) and ast.unparse(n.func) == "subprocess.Popen"
    ]
    assert direct == [], direct
    assert subprocess.Popen  # the module is used through to_thread
