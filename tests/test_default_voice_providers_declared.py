"""The default voice providers' packages are declared, and a failed voice
reply says so (FA-248).

The 2026-11 functional audit: read aloud and voice-note replies failed,
because Microsoft refused the edge-tts release live ran (7.2.8). edge-tts was
not declared at all -- the default text-to-speech provider reached a package
nothing installed or kept current -- and the Telegram path logged the
failure at DEBUG, so it failed silently there.

The default providers are read from where the gateway resolves them
(``voice_helpers.live_voice_settings``'s defaults); their factories are read
from the provider registries' registration lines. Every third-party import
inside a default provider must be a base dependency with a floor. The
negative control is the pyproject.toml before.
"""

from __future__ import annotations

import ast
import asyncio
import importlib.metadata
import logging
import re
import subprocess
import sys
from pathlib import Path

import pytest
from packaging.requirements import Requirement

REPO = Path(__file__).resolve().parents[1]
VOICE = REPO / "kazma-core/kazma_core/voice"


def _defaults() -> dict[str, str]:
    source = (REPO / "kazma-gateway/kazma_gateway/adapters/voice_helpers.py").read_text(encoding="utf-8")
    return dict(re.findall(r'"(tts_provider|stt_provider)":\s*"([a-z0-9_-]+)"', source))


def _factory(module: Path, name: str) -> ast.FunctionDef:
    tree = ast.parse(module.read_text(encoding="utf-8"))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "id", "").startswith("register_")
             and n.args and isinstance(n.args[0], ast.Constant) and n.args[0].value == name]
    assert calls, f"{module.name} registers no provider {name!r}"
    factory = calls[0].args[1].func.id
    return next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == factory)


def _third_party(func: ast.AST) -> set[str]:
    names = set()
    for node in ast.walk(func):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    return {n for n in names if n not in sys.stdlib_module_names and not n.startswith("kazma_")}


def _base_floors(pyproject: str) -> dict[str, bool]:
    block = re.search(r"^dependencies = \[(.*?)^\]", pyproject, flags=re.S | re.M).group(1)
    out = {}
    for spec in re.findall(r'^\s*"([^"]+)"', block, flags=re.M):
        req = Requirement(spec)
        out[re.sub(r"[-_.]+", "-", req.name).lower()] = any(s.operator in (">=", "==") for s in req.specifier)
    return out


def _undeclared(pyproject: str) -> list[str]:
    floors = _base_floors(pyproject)
    dists = importlib.metadata.packages_distributions()
    defaults = _defaults()
    missing = []
    for module, key in ((VOICE / "tts.py", "tts_provider"), (VOICE / "stt.py", "stt_provider")):
        for top in sorted(_third_party(_factory(module, defaults[key]))):
            for dist in dists.get(top, [top]):
                name = re.sub(r"[-_.]+", "-", dist).lower()
                if not floors.get(name):
                    missing.append(f"{defaults[key]} imports {top} ({name}): no base dependency with a floor")
    return missing


def test_the_defaults_are_the_ones_read():
    assert _defaults() == {"tts_provider": "edgetts", "stt_provider": "openai"}


def test_every_package_a_default_voice_provider_imports_is_declared():
    assert not _undeclared((REPO / "pyproject.toml").read_text(encoding="utf-8"))


def test_edge_tts_was_undeclared_before():
    """Negative control: the pyproject.toml of the audit."""
    old = subprocess.run(["git", "show", "938b325c:pyproject.toml"], cwd=REPO, capture_output=True,
                         text=True, check=True).stdout
    assert any("edge-tts" in m for m in _undeclared(old))


def test_a_failed_voice_reply_on_telegram_is_a_warning(monkeypatch, caplog):
    """The text reply went out; the voice did not, and the log says so."""
    from kazma_gateway.adapters.telegram import TelegramAdapter

    adapter = TelegramAdapter.__new__(TelegramAdapter)
    monkeypatch.setattr(adapter, "_live_voice_settings",
                        lambda: {"enabled": True, "tts_reply": True}, raising=False)

    async def no_audio(text):
        return None

    sent = []

    async def send_voice(*a, **k):
        sent.append(a)
        return True

    monkeypatch.setattr(adapter, "synthesize_reply", no_audio, raising=False)
    monkeypatch.setattr(adapter, "send_voice_reply", send_voice, raising=False)
    with caplog.at_level(logging.WARNING, logger="kazma_gateway.adapters.telegram"):
        asyncio.run(adapter._send_tts_reply(1, "The answer to the question is three."))
    assert sent == []
    assert any("voice reply not sent" in r.getMessage() and r.levelno == logging.WARNING
               for r in caplog.records)


@pytest.mark.parametrize("module", ["tts", "stt"])
def test_the_registries_are_read_from_source(module):
    assert _factory(VOICE / f"{module}.py", _defaults()[f"{module}_provider"]).name.startswith("_")
