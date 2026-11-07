"""No example placeholder is ever taken for a credential.

setup copies ``.env.example`` to ``.env``, and the example shipped
``TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here``. Every fresh install
started the Telegram adapter with it and logged "Bot token validation
failed: Not Found" at every start (the drill VM, 2026-11-07); the chaos
drill's only ERROR lines besides a missing npx were these.

Three holds: every credential the example sets is empty; the chat apps'
token variables are read only through ``env_files.env_credential``, which
treats a placeholder as unset (an install made from the old example still
holds one); and the chat adapters then build no Telegram adapter from it.
"""

from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

import pytest

from kazma_core import env_files

ROOT = Path(__file__).resolve().parents[1]
_CREDENTIAL_NAME = re.compile(r"(TOKEN|KEY|SECRET|PASSWORD|PASS)$")


def example_placeholders(text: str) -> list[str]:
    """Credential-named settings the example file gives a value."""
    found = []
    for line in text.splitlines():
        match = re.match(r"^\s*([A-Z0-9_]+)=(.*)$", line)
        if match and _CREDENTIAL_NAME.search(match.group(1)) and match.group(2).split("#", 1)[0].strip():
            found.append(match.group(1))
    return found


def test_the_example_sets_no_credential():
    assert example_placeholders((ROOT / ".env.example").read_text(encoding="utf-8")) == []
    # Negative control: the line it shipped.
    assert example_placeholders("TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here\n") == ["TELEGRAM_BOT_TOKEN"]


@pytest.mark.parametrize("value", ["your_telegram_bot_token_here", "YOUR-KEY", "slack_token_here",
                                   "<your token>", "changeme", "xxxxxxxx"])
def test_a_placeholder_is_no_credential(value, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", value)
    assert env_files.env_credential("TELEGRAM_BOT_TOKEN") == ""


@pytest.mark.parametrize("value", ["123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw", "xoxb-1-2-abc",
                                   "MTA4.demo.discord-token"])
def test_a_real_looking_token_passes(value, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", f"  {value}  ")
    assert env_files.env_credential("TELEGRAM_BOT_TOKEN") == value


def _direct_reads(source: str) -> list[int]:
    """Lines reading a chat token variable from the environment directly."""
    lines = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant):
            func = ast.unparse(node.func)
            if func in ("os.environ.get", "os.getenv", "environ.get", "getenv") \
                    and node.args[0].value in env_files.CHAT_TOKEN_VARIABLES:
                lines.append(node.lineno)
        elif isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) \
                and ast.unparse(node.value) in ("os.environ", "environ") \
                and node.slice.value in env_files.CHAT_TOKEN_VARIABLES:
            lines.append(node.lineno)
    return lines


def test_chat_tokens_are_read_through_the_helper():
    listed = subprocess.run(["git", "ls-files", "kazma-*/*.py"], cwd=ROOT, capture_output=True, text=True,
                            timeout=60, check=True, stdin=subprocess.DEVNULL).stdout.split()
    offenders = []
    for name in listed:
        if "/tests/" in name or "_tests/" in name or name.endswith("env_files.py"):
            continue
        source = (ROOT / name).read_text(encoding="utf-8", errors="replace")
        if any(var in source for var in env_files.CHAT_TOKEN_VARIABLES):
            offenders += [f"{name}:{line}" for line in _direct_reads(source)]
    assert offenders == [], offenders
    # Negative control: the reads of before.
    assert _direct_reads('token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()\n') == [1]
    assert _direct_reads('x = os.environ["SLACK_APP_TOKEN"]\n') == [1]


def test_the_chat_adapters_build_no_telegram_from_a_placeholder(monkeypatch):
    from kazma_gateway import chat_adapters

    class Store:
        def get(self, key, default=None):
            return default

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "your_telegram_bot_token_here")
    assert chat_adapters._read_platform_settings(Store(), "telegram").token == ""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw")
    assert chat_adapters._read_platform_settings(Store(), "telegram").token.startswith("123456789:")
