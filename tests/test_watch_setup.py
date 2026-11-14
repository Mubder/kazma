"""``kazma observer setup`` (REMAINING 3.2): your own Kazma Watch, one guided
command. Cloudflare's tool (wrangler) is a stand-in that keeps the account's
state (signed in, databases, the Worker, its secrets); Telegram and the
install's address answer through httpx's MockTransport; pairing is recorded.
Nothing here reaches Cloudflare or Telegram.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import httpx
import pytest

from kazma_core.observability import outside_watch, watch_setup
from kazma_core.observability.watch_setup import SetupIo, SetupStopped, WatchSetup

ROOT = Path(__file__).resolve().parents[1]
BOT = "123456789:" + "A" * 35
URL = "https://kazma-watch.someone.workers.dev"


class Wrangler:
    """Cloudflare's tool, as far as the setup uses it."""

    def __init__(self) -> None:
        self.logged_in = False
        self.databases: dict[str, str] = {}
        self.deployed = False
        self.secrets: dict[str, str] = {}
        self.calls: list[dict[str, Any]] = []
        self.deploy_error = ""

    def __call__(self, argv: list[str], *, cwd: Path, input_text: str | None = None, interactive: bool = False,
                 timeout: float = 0, account_id: str = "") -> subprocess.CompletedProcess[str]:
        assert argv[1:3] == ["--yes", f"wrangler@{watch_setup.WRANGLER_VERSION}"], argv
        args = argv[3:]
        self.calls.append({"args": args, "input": input_text, "interactive": interactive, "cwd": cwd,
                           "account": account_id})
        out, code = "", 0
        if args == ["whoami"]:
            out = "You are logged in with an OAuth Token" if self.logged_in else "You are not authenticated."
        elif args == ["login"]:
            assert interactive, "the sign-in uses the terminal"
            self.logged_in = True
        elif args[:2] == ["d1", "list"]:
            out = "npm notice run\n" + json.dumps([{"uuid": u, "name": n} for n, u in self.databases.items()])
        elif args[:2] == ["d1", "create"]:
            self.databases[args[2]] = "db-" + args[2]
            out = "Successfully created DB"
        elif args[:2] == ["d1", "execute"]:
            assert (cwd / "schema.sql").is_file()
        elif args[0] == "deploy":
            if self.deploy_error:
                return subprocess.CompletedProcess(argv, 1, "", self.deploy_error)
            config = json.loads((cwd / "wrangler.jsonc").read_text(encoding="utf-8"))
            assert (cwd / config["main"]).is_file()
            self.deployed = True
            out = f"Uploaded {config['name']}\nDeployed {config['name']} triggers\n  {URL}\nCurrent Version ID: x"
        elif args[:2] == ["secret", "bulk"]:
            assert self.deployed, "secrets go to a Worker that exists"
            for k, v in json.loads(input_text or "{}").items():
                if v is None:
                    self.secrets.pop(k, None)
                else:
                    self.secrets[k] = v
        else:
            raise AssertionError(f"unexpected wrangler call: {args}")
        return subprocess.CompletedProcess(argv, code, out, "")


class Vault:
    def __init__(self) -> None:
        self.rows: dict[str, str] = {}

    def store(self, name: str, value: str) -> int:
        self.rows[name] = value
        return 1

    def retrieve(self, name: str) -> str | None:
        return self.rows.get(name)


class World:
    """Telegram and the install's address."""

    def __init__(self, address_status: int = 200, location: str = "") -> None:
        self.address_status = address_status
        self.location = location
        self.updates_asked = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.startswith("https://api.telegram.org/bot"):
            assert BOT in url
            if url.endswith("/getMe"):
                return httpx.Response(200, json={"ok": True, "result": {"username": "my_watch_bot"}})
            if url.endswith("/getUpdates"):
                self.updates_asked += 1
                if self.updates_asked == 1:
                    return httpx.Response(200, json={"ok": True, "result": []})
                return httpx.Response(200, json={"ok": True, "result": [
                    {"update_id": 7, "message": {"chat": {"id": 4242, "type": "private"}, "text": "hello"}}]})
        if url.endswith("/health/live"):
            headers = {"location": self.location} if self.location else {}
            return httpx.Response(self.address_status, headers=headers)
        raise AssertionError(f"unexpected request: {url}")


@pytest.fixture
def setup_env(monkeypatch, tmp_path):
    vault = Vault()
    monkeypatch.setattr("kazma_core.security.vault.get_vault", lambda: vault)
    state: dict[str, Any] = {"pairing": None, "paired": [], "left": 0, "told": 0, "tested": 0, "fail_pairs": 0}

    def pair(url: str, token: str) -> dict[str, str]:
        if state["fail_pairs"]:
            state["fail_pairs"] -= 1
            raise outside_watch.PairingError("the observer refused: HTTP 503 pairing_not_configured", "refused")
        state["paired"].append((url, token))
        state["pairing"] = {"url": url, "install": "a" * 16, "key": "k" * 40}
        return {"install": "a" * 16, "url": url, "path": "x"}

    def leave(forget: bool = False) -> dict[str, Any]:
        state["left"] += 1
        state["pairing"] = None
        return {"ok": True}

    monkeypatch.setattr(outside_watch, "_read_pairing", lambda: state["pairing"])
    monkeypatch.setattr(outside_watch, "leave_observer", leave)
    monkeypatch.setattr(outside_watch, "push_watch_settings",
                        lambda store=None: state.__setitem__("told", state["told"] + 1) or {"ok": True})
    monkeypatch.setattr(outside_watch, "watch_test_page",
                        lambda: state.__setitem__("tested", state["tested"] + 1) or {"ok": True})
    monkeypatch.delenv("KAZMA_PUBLIC_URL", raising=False)
    return {"vault": vault, "state": state, "pair": pair, "work": tmp_path / "kazma-watch"}


def _run(env, wrangler: Wrangler, world: World, *, answers=("",), secrets=(BOT,), **kw) -> tuple[dict, list[str]]:
    said: list[str] = []
    asks = list(answers)
    hidden = list(secrets)
    io = SetupIo(say=said.append, ask=lambda _p: asks.pop(0) if asks else "",
                 ask_secret=lambda _p: hidden.pop(0), sleep=lambda _s: None)
    run = WatchSetup(io=io, run=wrangler, http=httpx.Client(transport=httpx.MockTransport(world.handler)),
                     pair=env["pair"], work_dir=env["work"], npx="npx", **kw)
    return run.setup(), said


def test_a_first_setup_deploys_the_watch_and_pairs_this_install(setup_env, monkeypatch) -> None:
    monkeypatch.setenv("KAZMA_PUBLIC_URL", "https://my.example.org")
    wrangler, world = Wrangler(), World()
    done, said = _run(setup_env, wrangler, world)
    assert done["url"] == URL and done["address"] == "https://my.example.org"
    assert wrangler.logged_in and any(c["interactive"] for c in wrangler.calls), "the person signed in"
    assert list(wrangler.databases) == ["kazma-watch"]
    token = setup_env["vault"].rows[watch_setup.PAIRING_TOKEN_NAME]
    assert len(token) == 64
    assert wrangler.secrets == {"PAIRING_TOKEN": token, "TELEGRAM_BOT_TOKEN": BOT, "TELEGRAM_CHAT_ID": "4242"}
    assert setup_env["state"]["paired"] == [(URL, token)]
    assert setup_env["state"]["told"] == 1 and setup_env["state"]["tested"] == 1
    # No secret on any command line, nor in what is kept on disk or said.
    for call in wrangler.calls:
        assert BOT not in " ".join(call["args"]) and token not in " ".join(call["args"])
    work = setup_env["work"]
    for path in (work / "wrangler.jsonc", work / "setup.json"):
        text = path.read_text(encoding="utf-8")
        assert BOT not in text and token not in text and "4242" not in text
    config = json.loads((work / "wrangler.jsonc").read_text(encoding="utf-8"))
    assert config["vars"]["PULL_URL"] == "https://my.example.org"
    assert config["d1_databases"][0]["database_id"] == "db-kazma-watch"
    assert all(BOT not in line for line in said)


def test_a_second_run_keeps_the_database_the_token_and_the_bot(setup_env) -> None:
    wrangler = Wrangler()
    _run(setup_env, wrangler, World(), answers=("-",))
    token = setup_env["vault"].rows[watch_setup.PAIRING_TOKEN_NAME]
    created = [c for c in wrangler.calls if c["args"][:2] == ["d1", "create"]]
    wrangler.calls.clear()
    done, said = _run(setup_env, wrangler, World(), answers=("",), secrets=())
    assert not [c for c in wrangler.calls if c["args"][:2] == ["d1", "create"]] and len(created) == 1
    assert setup_env["vault"].rows[watch_setup.PAIRING_TOKEN_NAME] == token
    assert wrangler.secrets["TELEGRAM_BOT_TOKEN"] == BOT, "the bot is kept"
    assert len(setup_env["state"]["paired"]) == 1, "already paired with this watch: not paired again"
    assert any("kept" in line for line in said)


def test_update_asks_nothing_and_deploys_this_builds_watch(setup_env) -> None:
    wrangler = Wrangler()
    _run(setup_env, wrangler, World(), answers=("-",))
    wrangler.calls.clear()
    _run(setup_env, wrangler, World(), answers=(), secrets=(), update=True)
    kinds = [c["args"][0] if c["args"][0] != "d1" else " ".join(c["args"][:2]) for c in wrangler.calls]
    assert "deploy" in kinds and "secret" not in kinds and "login" not in kinds


def test_an_address_behind_access_with_yes_is_watched_by_heartbeats(setup_env) -> None:
    world = World(302, "https://team.cloudflareaccess.com/cdn-cgi/access/login/x")
    done, said = _run(setup_env, Wrangler(), world, address="https://behind.example.org", yes=True)
    assert done["address"] == ""
    config = json.loads((setup_env["work"] / "wrangler.jsonc").read_text(encoding="utf-8"))
    assert config["vars"]["PULL_URL"] == ""
    assert any("Cloudflare Access" in line for line in said)


def test_a_service_token_for_access_goes_in_as_a_secret(setup_env) -> None:
    world = World(302, "https://team.cloudflareaccess.com/cdn-cgi/access/login/x")
    wrangler = Wrangler()
    done, _ = _run(setup_env, wrangler, world, address="https://behind.example.org",
                   answers=("client.access",), secrets=(BOT, "access-secret-value"))
    assert done["address"] == "https://behind.example.org"
    assert wrangler.secrets["ACCESS_CLIENT_ID"] == "client.access"
    assert wrangler.secrets["ACCESS_CLIENT_SECRET"] == "access-secret-value"


def test_an_install_paired_elsewhere_is_left_alone_unless_moved(setup_env) -> None:
    setup_env["state"]["pairing"] = {"url": "https://status.kazma.ai", "install": "a" * 16, "key": "k" * 40}
    wrangler = Wrangler()
    with pytest.raises(SetupStopped) as stopped:
        _run(setup_env, wrangler, World(), answers=("-",))
    assert "--move" in stopped.value.fix and wrangler.calls == [], "nothing was done"
    _run(setup_env, wrangler, World(), answers=("-",), move=True)
    assert setup_env["state"]["left"] == 1 and setup_env["state"]["paired"][-1][0] == URL


def test_no_node_says_what_to_install(setup_env, monkeypatch) -> None:
    run = WatchSetup(io=SetupIo(say=lambda _s: None), run=Wrangler(), pair=setup_env["pair"],
                     work_dir=setup_env["work"], npx=None)
    monkeypatch.setattr(watch_setup.shutil, "which", lambda _n: None)
    with pytest.raises(SetupStopped) as stopped:
        run.setup()
    assert "kazma setup install --with node" in stopped.value.fix


def test_pairing_waits_for_the_new_workers_secrets(setup_env) -> None:
    setup_env["state"]["fail_pairs"] = 2
    done, _ = _run(setup_env, Wrangler(), World(), answers=("-",))
    assert done["paired"] and len(setup_env["state"]["paired"]) == 1


def test_several_accounts_names_the_option(setup_env) -> None:
    wrangler = Wrangler()
    wrangler.deploy_error = "X [ERROR] More than one account available but unable to select one in non-interactive mode."
    with pytest.raises(SetupStopped) as stopped:
        _run(setup_env, wrangler, World(), answers=("-",))
    assert "--account-id" in stopped.value.fix
    _run(setup_env, Wrangler(), World(), answers=("-",), account_id="acc123")


def test_the_account_reaches_wrangler(setup_env) -> None:
    wrangler = Wrangler()
    _run(setup_env, wrangler, World(), answers=("-",), account_id="acc123")
    assert {c["account"] for c in wrangler.calls} == {"acc123"}
    assert watch_setup._wrangler_env("acc123")["CLOUDFLARE_ACCOUNT_ID"] == "acc123"


def test_no_telegram_after_a_bot_takes_the_bot_off_the_worker(setup_env) -> None:
    wrangler = Wrangler()
    _run(setup_env, wrangler, World(), answers=("-",))
    assert "TELEGRAM_BOT_TOKEN" in wrangler.secrets
    _run(setup_env, wrangler, World(), answers=("",), secrets=(), telegram=False)
    assert "TELEGRAM_BOT_TOKEN" not in wrangler.secrets and "TELEGRAM_CHAT_ID" not in wrangler.secrets


def test_wrangler_gets_no_kazma_variable(monkeypatch) -> None:
    monkeypatch.setenv("KAZMA_VAULT_KEY", "secret-vault-key")
    env = watch_setup._wrangler_env()
    assert not [k for k in env if k.upper().startswith("KAZMA_")]
    assert env["WRANGLER_SEND_METRICS"] == "false"


def test_the_worker_ships_in_the_wheel_where_setup_looks() -> None:
    """pyproject's force-include puts deploy/observer where worker_source looks
    in an installed wheel; in this checkout it finds deploy/observer."""
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    for source, target in (("deploy/observer/src", "kazma_core/observability/watch_worker/src"),
                           ("deploy/observer/schema.sql", "kazma_core/observability/watch_worker/schema.sql")):
        assert f'"{source}" = "{target}"' in text
    found = watch_setup.worker_source()
    assert found is not None and (found / "src" / "index.js").is_file() and (found / "schema.sql").is_file()


def test_the_pairing_token_is_install_scoped_in_the_vault() -> None:
    from kazma_core.security.vault import is_install_scoped_secret

    assert is_install_scoped_secret(watch_setup.PAIRING_TOKEN_NAME)


def test_the_command_says_why_it_stopped(capsys, monkeypatch) -> None:
    from kazma_cli import observer

    def stop(self):
        raise SetupStopped("Node's npx is not installed.", "kazma setup install --with node")

    monkeypatch.setattr(WatchSetup, "setup", stop)
    assert observer.run(["setup", "-y"]) == 1
    out = capsys.readouterr().out
    assert "Stopped: Node's npx is not installed." in out and "What to do: kazma setup install --with node" in out
