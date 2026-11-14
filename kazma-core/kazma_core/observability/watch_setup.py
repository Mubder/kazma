"""``kazma observer setup``: your own Kazma Watch, one guided command (REMAINING 3.2).

Kazma Watch is the Worker in ``deploy/observer``: it watches an install from
outside the machine (a stopped heartbeat is a powered-off, asleep or cut-off
machine nothing on it can report). This command puts it on the person's own
Cloudflare account -- free plan is enough -- with Cloudflare's own tool
(wrangler, run through ``npx``: Node is a part Kazma installs), then pairs
this install with it. Each step is said before it runs:

1. Node's ``npx`` is there (else: ``kazma setup install --with node``).
2. Signed in to Cloudflare (``wrangler whoami``; else ``wrangler login``
   opens the browser and the person signs in there: Kazma never sees it).
3. The watch bot: a Telegram bot the person makes with @BotFather (a bot of
   its own, so a fault in Kazma's bot cannot silence the watch), checked with
   ``getMe``; the person sends it a message and the chat is read from
   ``getUpdates``.
4. Where to look from outside: ``KAZMA_PUBLIC_URL`` (asked), checked from
   here. None, or one Cloudflare Access turns away and no service token
   given: heartbeats only (the Worker then judges by the heartbeat alone,
   ``heartbeat_silent`` when it stops).
5. The database (D1 ``<name>``): found, else made; its tables made.
6. The Worker deployed to ``<name>.<subdomain>.workers.dev``; its secrets --
   the pairing token generated here and kept in Kazma's vault
   (``observer.pairing_token``, install-scoped), the bot, the Access token --
   reach wrangler on stdin (``secret bulk``), never on a command line.
7. This install paired (``outside_watch.pair_with_observer``), Kazma Watch
   told the time zone, a test message sent.

A second run keeps the database, the pairing token and the bot (``--new-bot``
asks again) and deploys the Worker of this build; ``--update`` asks nothing.
An install paired with another watch is left alone unless ``--move`` (it
leaves that watch first: left paired, it would page "machine unreachable").

The work folder ``<data dir>/kazma-watch`` holds the Worker's code and its
configuration (database id, address) and ``setup.json``: no secret.

The pairing is the contract a watch Kazma hosts for everyone will use too:
``kazma observer pair --url <its address>`` with the code it gives you;
nothing else on the install changes.
"""

from __future__ import annotations

import getpass
import json
import logging
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import time
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_NAME",
    "PAIRING_TOKEN_NAME",
    "WRANGLER_VERSION",
    "SetupIo",
    "SetupStopped",
    "WatchSetup",
    "stored_pairing_token",
    "worker_source",
]

#: The wrangler release this setup was tested with (a release of its own: npx
#: would otherwise run whatever npm serves that day).
WRANGLER_VERSION = "4.158.0"
#: Kazma's vault name for the watch's pairing token (install-scoped: the CLI
#: reads it with no tenant, ``vault.INSTALL_SCOPED_SECRETS``).
PAIRING_TOKEN_NAME = "observer.pairing_token"
DEFAULT_NAME = "kazma-watch"

_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{1,38}[a-z0-9]$")
_WORKERS_DEV = re.compile(r"https://[a-z0-9][a-z0-9.-]*\.workers\.dev\b")
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_BOT_TOKEN = re.compile(r"^\d{5,15}:[A-Za-z0-9_-]{30,60}$")
_TELEGRAM = "https://api.telegram.org"
_WAIT_FOR_MESSAGE_S = 180.0
_PAIR_WAIT_S = 90.0
_TIMEOUT_S = 15.0


class SetupStopped(Exception):
    """The setup stopped at a step; ``fix`` says what to do (empty: nothing to add)."""

    def __init__(self, message: str, fix: str = "") -> None:
        super().__init__(message)
        self.fix = fix


@dataclass
class SetupIo:
    """How the setup talks to the person (tests answer for them)."""

    say: Callable[[str], None] = print
    ask: Callable[[str], str] = input
    ask_secret: Callable[[str], str] = getpass.getpass
    sleep: Callable[[float], None] = time.sleep


def worker_source() -> Path | None:
    """The Worker's code: shipped in the wheel (``watch_worker``), else the
    checkout's ``deploy/observer``; None when neither is there."""
    here = Path(__file__).resolve()
    packaged = here.parent / "watch_worker"
    if (packaged / "src" / "index.js").is_file():
        return packaged
    for parent in here.parents:
        candidate = parent / "deploy" / "observer"
        if (candidate / "src" / "index.js").is_file() and (candidate / "schema.sql").is_file():
            return candidate
    return None


def stored_pairing_token() -> str:
    """The pairing token ``kazma observer setup`` made, from the vault ('' when none)."""
    try:
        from kazma_core.security.vault import get_vault

        vault = get_vault()
        return (vault.retrieve(PAIRING_TOKEN_NAME) or "") if vault is not None else ""
    except (OSError, ValueError, RuntimeError, sqlite3.Error):  # a vault that cannot answer: ask
        logger.warning("[watch_setup] the vault could not be read for the pairing token", exc_info=True)
        return ""


def _compatibility_date(source: Path) -> str:
    """The Worker's compatibility date, from its own ``wrangler.jsonc``."""
    try:
        text = (source / "wrangler.jsonc").read_text(encoding="utf-8")
    except OSError:
        return "2026-10-01"
    found = re.search(r'"compatibility_date"\s*:\s*"(\d{4}-\d{2}-\d{2})"', text)
    return found.group(1) if found else "2026-10-01"


def _wrangler_env(account_id: str = "") -> dict[str, str]:
    """The person's environment for wrangler, without Kazma's own variables
    (wrangler needs none of them), wrangler's usage reports off, and the
    Cloudflare account when the person named one."""
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("KAZMA_")}
    env["WRANGLER_SEND_METRICS"] = "false"
    env["NO_COLOR"] = "1"
    if account_id:
        env["CLOUDFLARE_ACCOUNT_ID"] = account_id
    return env


def _run_program(argv: list[str], *, cwd: Path, input_text: str | None = None, interactive: bool = False,
                 timeout: float = 900.0, account_id: str = "") -> subprocess.CompletedProcess[str]:
    """Run one wrangler command. Interactive (the sign-in) uses the terminal;
    every other gets *input_text* on stdin (empty: it never waits for input)."""
    env = _wrangler_env(account_id)
    if interactive:
        return subprocess.run(argv, cwd=cwd, env=env, timeout=timeout, check=False, text=True)
    return subprocess.run(argv, cwd=cwd, env=env, input=input_text or "", capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout, check=False)


def _plain(text: str | None) -> str:
    return _ANSI.sub("", text or "")


@dataclass
class _State:
    """``setup.json``: what the last run made (nothing secret)."""

    name: str = ""
    database_id: str = ""
    url: str = ""
    address: str = ""
    telegram: bool = False
    access_token: bool = False
    at: float = 0.0

    @classmethod
    def load(cls, path: Path) -> _State:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        if not isinstance(data, dict):
            return cls()
        known = {k: data[k] for k in cls.__dataclass_fields__ if k in data}
        try:
            return cls(**known)
        except TypeError:
            return cls()

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(self.__dict__, indent=2), encoding="utf-8", newline="\n")


@dataclass
class WatchSetup:
    """One run of ``kazma observer setup`` (see the module docstring).

    *run*, *http* and *pair* are the outside world (tests replace them):
    the wrangler runner, an httpx client for Telegram and the address check,
    and ``outside_watch.pair_with_observer``.
    """

    name: str = DEFAULT_NAME
    address: str | None = None          # None: ask (or KAZMA_PUBLIC_URL with yes); "": none
    telegram: bool = True
    yes: bool = False
    update: bool = False
    move: bool = False
    new_bot: bool = False
    account_id: str = ""
    io: SetupIo = field(default_factory=SetupIo)
    run: Callable[..., subprocess.CompletedProcess[str]] = _run_program
    http: httpx.Client | None = None
    pair: Callable[[str, str], dict[str, str]] | None = None
    work_dir: Path | None = None
    npx: str | None = None
    source: Path | None = None

    # -- the run --------------------------------------------------------------------

    def setup(self) -> dict[str, Any]:
        """Run every step; returns what was done. Raises :class:`SetupStopped`."""
        from kazma_core.observability import outside_watch

        if not _NAME_RE.fullmatch(self.name):
            raise SetupStopped(f"{self.name!r} cannot be a Worker's name",
                               "use lower-case letters, digits and dashes (3-40), e.g. kazma-watch")
        work = self.work_dir or self._default_work_dir()
        work.mkdir(parents=True, exist_ok=True)
        state = _State.load(work / "setup.json")
        if state.name and state.name != self.name:
            state = _State()
        pairing = outside_watch._read_pairing()
        if pairing and pairing["url"] != state.url and not self.move:
            raise SetupStopped(
                f"This install is paired with {pairing['url']}.",
                "to move it to your own Kazma Watch, run: kazma observer setup --move "
                "(it leaves that watch first)")
        vault = self._vault()
        npx = self.npx or shutil.which("npx")
        if not npx:
            raise SetupStopped("Node's npx is not installed (Cloudflare's tool runs on it).",
                               "kazma setup install --with node, then run this again")
        self._npx = npx
        source = self.source or worker_source()
        if source is None:
            raise SetupStopped("This Kazma has no Kazma Watch code.", "update Kazma: kazma update")
        self._work = work

        self.io.say("1/6 Cloudflare: checking you are signed in")
        self._signed_in()
        token = stored_pairing_token() if vault is not None else ""
        bot: tuple[str, str] | None = None
        access: tuple[str, str] | None = None
        address = state.address
        if not self.update:
            if self.telegram and (self.new_bot or not state.telegram):
                self.io.say("2/6 The watch bot (Telegram)")
                bot = self._ask_bot()
            elif self.telegram:
                self.io.say("2/6 The watch bot: kept (--new-bot to change it)")
            else:
                self.io.say("2/6 The watch bot: none (--no-telegram); Kazma Watch will page nobody")
            self.io.say("3/6 Where Kazma Watch looks from outside")
            address, access = self._ask_address(state.address)
        self.io.say(f"4/6 The database ({self.name})")
        state.database_id = self._database()
        self._write_worker(source, work, state.database_id, address)
        self._wrangler("d1", "execute", self.name, "--remote", "--file", "schema.sql", "-y",
                       "--config", "wrangler.jsonc", what="make the database's tables", timeout=600)
        self.io.say(f"5/6 The Worker ({self.name})")
        url = self._deploy()
        made = not token
        if made:
            token = secrets.token_hex(32)
            vault.store(PAIRING_TOKEN_NAME, token)
        if not self.update or made or not state.url:
            # A bot dropped by --no-telegram is taken off the Worker too (null deletes).
            drop_bot = not self.update and not self.telegram and state.telegram
            self._secrets(token, bot, access, drop_bot=drop_bot)
        state.name, state.url, state.address = self.name, url, address
        if bot is not None:
            state.telegram = True
        elif not self.telegram and not self.update:
            state.telegram = False
        if access is not None:
            state.access_token = True
        state.at = time.time()
        state.save(work / "setup.json")

        self.io.say("6/6 Pairing this install")
        paired = pairing is not None and pairing["url"] == url
        if not paired:
            if pairing is not None:
                left = outside_watch.leave_observer(forget=False)
                if not left.get("ok"):
                    raise SetupStopped(f"{pairing['url']} did not let this install go ({left.get('error')}).",
                                       "unpair it first: kazma observer unpair, then run this again")
                self.io.say(f"  left {pairing['url']}")
            self._pair(url, token)
        told = outside_watch.push_watch_settings()
        test = outside_watch.watch_test_page() if state.telegram and not self.update else None
        if test is not None:
            if test.get("ok"):
                self.io.say("  Kazma Watch sent you a test message on Telegram.")
            else:
                self.io.say(f"  The test message did not go ({test.get('error') or test.get('channels')}).")
        logger.info("[watch_setup] Kazma Watch %s at %s (address: %s)", "updated" if self.update else "set up",
                    url, address or "none")
        return {"url": url, "address": address, "paired": True, "telegram": state.telegram,
                "zone_told": bool(told.get("ok")), "test": test}

    # -- the steps ------------------------------------------------------------------------

    @staticmethod
    def _default_work_dir() -> Path:
        from kazma_core.paths import data_dir

        return data_dir() / "kazma-watch"

    @staticmethod
    def _vault() -> Any:
        from kazma_core.security.vault import get_vault

        vault = get_vault()
        if vault is None:
            raise SetupStopped("Kazma's vault is not available, so the pairing token could not be kept.",
                               "run this from the folder Kazma is installed in (its .env holds the vault key)")
        return vault

    def _wrangler(self, *args: str, what: str, input_text: str | None = None, interactive: bool = False,
                  timeout: float = 900.0, check: bool = True) -> str:
        argv = [self._npx, "--yes", f"wrangler@{WRANGLER_VERSION}", *args]
        try:
            done = self.run(argv, cwd=self._work, input_text=input_text, interactive=interactive, timeout=timeout,
                            account_id=self.account_id)
        except (OSError, subprocess.SubprocessError) as exc:
            raise SetupStopped(f"Cloudflare's tool could not {what} ({type(exc).__name__}).") from exc
        out = _plain((done.stdout or "") + "\n" + (done.stderr or ""))
        if check and done.returncode != 0:
            raise SetupStopped(f"Cloudflare's tool could not {what}: {self._last_lines(out)}", self._fix_for(out))
        return out

    @staticmethod
    def _last_lines(text: str) -> str:
        lines = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("npm notice")]
        errors = [ln for ln in lines if "ERROR" in ln or "rror:" in ln]
        return " ".join((errors or lines)[-3:])[:600] or "no answer"

    @staticmethod
    def _fix_for(text: str) -> str:
        low = text.lower()
        if "more than one account" in low:
            return ("you have several Cloudflare accounts: run again with --account-id <id> "
                    "(the ids: npx wrangler whoami)")
        if "workers.dev" in low and "subdomain" in low:
            return ("open dash.cloudflare.com -> Workers & Pages once to choose your workers.dev subdomain, "
                    "then run this again")
        if "not logged in" in low or "authentication" in low:
            return "sign in again: npx wrangler login, then run this again"
        return ""

    def _signed_in(self) -> None:
        out = self._wrangler("whoami", what="say who you are signed in as", check=False, timeout=180)
        if "You are logged in" in out:
            return
        if self.update:
            raise SetupStopped("You are not signed in to Cloudflare.", "run kazma observer setup (without --update)")
        self.io.say("  Sign in to Cloudflare in the browser that opens (a free account is enough).")
        self._wrangler("login", what="sign you in", interactive=True, timeout=900, check=False)
        if "You are logged in" not in self._wrangler("whoami", what="say who you are signed in as",
                                                       check=False, timeout=180):
            raise SetupStopped("You are not signed in to Cloudflare.", "run this again and sign in in the browser")

    def _telegram(self, token: str, method: str, **params: Any) -> dict[str, Any]:
        client = self.http or httpx.Client(timeout=_TIMEOUT_S, trust_env=False, follow_redirects=False)
        try:
            res = client.post(f"{_TELEGRAM}/bot{token}/{method}", json=params)
            data = res.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise SetupStopped(f"Telegram did not answer ({type(exc).__name__}).",
                               "check the connection and run this again") from exc
        finally:
            if self.http is None:
                client.close()
        if not isinstance(data, dict) or not data.get("ok"):
            description = str((data or {}).get("description") or "") if isinstance(data, dict) else ""
            raise SetupStopped(f"Telegram refused: {description[:200] or 'no reason'}",
                               "check the bot's token in @BotFather (/mybots -> API Token)")
        return data

    def _ask_bot(self) -> tuple[str, str]:
        self.io.say("  In Telegram, open @BotFather, send /newbot and name it (for example: My Kazma Watch).")
        self.io.say("  It answers with a token. A bot of its own keeps the watch working when Kazma's bot does not.")
        token = self.io.ask_secret("  The watch bot's token (not shown): ").strip()
        if not _BOT_TOKEN.fullmatch(token):
            raise SetupStopped("That is not a Telegram bot token (it looks like 123456789:ABC...).",
                               "copy the token @BotFather gave you and run this again")
        me = self._telegram(token, "getMe").get("result") or {}
        username = str(me.get("username") or "")
        self.io.say(f"  Now send @{username} any message in Telegram (for example: hello).")
        deadline = time.monotonic() + _WAIT_FOR_MESSAGE_S
        offset = 0
        while time.monotonic() < deadline:
            data = self._telegram(token, "getUpdates", offset=offset, timeout=0)
            for update in data.get("result") or []:
                offset = max(offset, int(update.get("update_id", 0)) + 1)
                message = update.get("message") or {}
                chat = message.get("chat") or {}
                if chat.get("type") == "private" and isinstance(chat.get("id"), int):
                    self.io.say("  Got it.")
                    return token, str(chat["id"])
            self.io.sleep(3.0)
        raise SetupStopped(f"No message reached @{username} in {int(_WAIT_FOR_MESSAGE_S / 60)} minutes.",
                           "send the bot a message, then run this again")

    def _ask_address(self, kept: str) -> tuple[str, tuple[str, str] | None]:
        default = kept or (os.environ.get("KAZMA_PUBLIC_URL") or "").strip()
        if self.address is not None:
            address = self.address.strip()
        elif self.yes:
            address = default
        else:
            shown = default or "none"
            answer = self.io.ask(f"  Kazma's address from the internet, if it has one [{shown}] "
                                 "(Enter keeps it, - for none): ").strip()
            address = "" if answer == "-" else (answer or default)
        address = address.rstrip("/")
        if not address:
            self.io.say("  None: Kazma Watch will judge by the heartbeats alone.")
            return "", None
        parsed = urllib.parse.urlparse(address)
        if parsed.scheme != "https" or not parsed.hostname:
            raise SetupStopped(f"{address} is not an https address.", "give one that starts with https://, or - for none")
        answer = self._probe(address)
        if answer == "ok":
            self.io.say(f"  {address} answers.")
            return address, None
        if answer == "access":
            self.io.say(f"  {address} is behind Cloudflare Access, which turns Kazma Watch away.")
            if self.yes:
                self.io.say("  Kazma Watch will judge by the heartbeats alone.")
                return "", None
            client_id = self.io.ask("  An Access service token's Client ID (Enter: heartbeats only): ").strip()
            if not client_id:
                return "", None
            secret = self.io.ask_secret("  Its Client Secret (not shown): ").strip()
            if not secret:
                return "", None
            return address, (client_id, secret)
        self.io.say(f"  {address} did not answer from here ({answer}); Kazma Watch will keep asking.")
        return address, None

    def _probe(self, address: str) -> str:
        client = self.http or httpx.Client(timeout=10.0, trust_env=False, follow_redirects=False)
        try:
            res = client.get(f"{address}/health/live")
        except httpx.HTTPError as exc:
            return type(exc).__name__
        finally:
            if self.http is None:
                client.close()
        location = res.headers.get("location", "")
        if res.status_code == 200:
            return "ok"
        if res.status_code in (401, 403) or (300 <= res.status_code < 400 and "cloudflareaccess" in location):
            return "access"
        return f"HTTP {res.status_code}"

    def _database(self) -> str:
        found = self._database_id()
        if found:
            self.io.say("  kept")
            return found
        self._wrangler("d1", "create", self.name, what="make the database", timeout=300)
        found = self._database_id()
        if not found:
            raise SetupStopped("The database was made, but Cloudflare does not list it.", "run this again")
        self.io.say("  made")
        return found

    def _database_id(self) -> str:
        out = self._wrangler("d1", "list", "--json", what="list your databases", timeout=300)
        start = out.find("[")
        try:
            rows = json.loads(out[start:out.rindex("]") + 1]) if start >= 0 else []
        except ValueError:
            rows = []
        for row in rows if isinstance(rows, list) else []:
            if isinstance(row, dict) and row.get("name") == self.name:
                return str(row.get("uuid") or row.get("database_id") or "")
        return ""

    def _write_worker(self, source: Path, work: Path, database_id: str, address: str) -> None:
        """The Worker's code of this build and its configuration (no secret)."""
        src = work / "src"
        if src.exists():
            shutil.rmtree(src)
        shutil.copytree(source / "src", src)
        shutil.copy2(source / "schema.sql", work / "schema.sql")
        config = {
            "name": self.name,
            "main": "src/index.js",
            "compatibility_date": _compatibility_date(source),
            "workers_dev": True,
            "triggers": {"crons": ["* * * * *"]},
            "d1_databases": [{"binding": "DB", "database_name": self.name, "database_id": database_id}],
            "vars": {"PULL_URL": address, "ACCESS_TEAM_DOMAIN": "", "ACCESS_AUD": "",
                     "EMAIL_FROM": "", "EMAIL_TO": ""},
            "observability": {"enabled": True},
        }
        (work / "wrangler.jsonc").write_text(json.dumps(config, indent=2), encoding="utf-8", newline="\n")

    def _deploy(self) -> str:
        out = self._wrangler("deploy", "--config", "wrangler.jsonc", what="deploy Kazma Watch", timeout=900)
        found = _WORKERS_DEV.search(out)
        if not found:
            raise SetupStopped("Kazma Watch was deployed, but Cloudflare named no workers.dev address.",
                               "open dash.cloudflare.com -> Workers & Pages, turn on its workers.dev route, "
                               "then run this again")
        url = found.group(0).rstrip("/.")
        self.io.say(f"  {url}")
        return url

    def _secrets(self, token: str, bot: tuple[str, str] | None, access: tuple[str, str] | None,
                 *, drop_bot: bool = False) -> None:
        values: dict[str, str | None] = {"PAIRING_TOKEN": token}
        if drop_bot:
            values.update(TELEGRAM_BOT_TOKEN=None, TELEGRAM_CHAT_ID=None)
        if bot is not None:
            values.update(TELEGRAM_BOT_TOKEN=bot[0], TELEGRAM_CHAT_ID=bot[1])
        if access is not None:
            values.update(ACCESS_CLIENT_ID=access[0], ACCESS_CLIENT_SECRET=access[1])
        self._wrangler("secret", "bulk", "--config", "wrangler.jsonc", what="give Kazma Watch its secrets",
                       input_text=json.dumps(values), timeout=300)

    def _pair(self, url: str, token: str) -> None:
        from kazma_core.observability import outside_watch

        pair = self.pair or outside_watch.pair_with_observer
        deadline = time.monotonic() + _PAIR_WAIT_S
        while True:
            try:
                done = pair(url, token)
                self.io.say(f"  paired as install {done.get('install')}")
                return
            except outside_watch.PairingError as exc:
                # A new Worker's secrets take a few seconds to reach it (503).
                if exc.code not in ("refused", "unreachable") or time.monotonic() >= deadline:
                    raise SetupStopped(f"Pairing failed: {exc}", "run this again in a minute") from exc
            self.io.sleep(5.0)
