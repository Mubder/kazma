"""The four security switches have one spelling everywhere.

``KAZMA_PRODUCTION``, ``KAZMA_AUTH_DISABLED``, ``KAZMA_DEMO_MODE`` and
``KAZMA_DEV_WS_BYPASS`` decide how open Kazma is, and every place that read
one kept its own list of truthy spellings (security audit 2026-11-10). The
boot guard read ``1/true/yes``; the WebSocket check honoured ``on`` too, so
``KAZMA_DEV_WS_BYPASS=on`` passed the guard on a network bind and was
honoured; the vault and the docs read ``KAZMA_PRODUCTION=on`` as production
and the auth gate did not, so ``on`` + ``KAZMA_AUTH_DISABLED`` opened it.

Now ``boot_guard.env_flag`` is the one reader. The gate below fails on any
product module that reads one of the four itself.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest
from fastapi import FastAPI
from kazma_core.security import boot_guard
from kazma_core.security.boot_guard import SECURITY_SWITCHES, check_exposure_posture, env_flag
from kazma_ui import auth
from starlette.testclient import TestClient
from starlette.websockets import WebSocket

_ROOT = Path(__file__).resolve().parents[1]
_THE_READER = "kazma-core/kazma_core/security/boot_guard.py"


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (*SECURITY_SWITCHES, "KAZMA_SECRET", "KAZMA_TRUSTED_PROXIES"):
        monkeypatch.delenv(var, raising=False)
    auth.reset_proxy_detection()
    yield
    auth.reset_proxy_detection()


# ── the gate ────────────────────────────────────────────────────────────


def _switch_reads(source: str) -> list[tuple[int, str]]:
    """Lines where *source* reads a switch's value itself."""
    found: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(source)):
        name = None
        if isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant):
            fn = node.func
            if isinstance(fn, ast.Attribute) and (
                (fn.attr == "get" and isinstance(fn.value, ast.Attribute) and fn.value.attr == "environ")
                or fn.attr == "getenv"
            ):
                name = node.args[0].value
            elif isinstance(fn, ast.Name) and fn.id == "getenv":
                name = node.args[0].value
        elif (
            isinstance(node, ast.Subscript)
            and isinstance(node.ctx, ast.Load)
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == "environ"
            and isinstance(node.slice, ast.Constant)
        ):
            name = node.slice.value
        if name in SECURITY_SWITCHES:
            found.append((node.lineno, name))
    return found


def _product_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "kazma-*/kazma_*/*.py", "kazma-*/kazma_*/**/*.py", "serve.py"],
        cwd=_ROOT, capture_output=True, text=True, check=True,
    ).stdout.split()
    return sorted(set(out))


def test_only_env_flag_reads_a_security_switch() -> None:
    files = _product_files()
    assert len(files) > 500, "the file list is the product"
    offenders = []
    for rel in files:
        if rel == _THE_READER:
            continue
        source = (_ROOT / rel).read_text(encoding="utf-8")
        offenders += [f"{rel}:{line}: {name}" for line, name in _switch_reads(source)]
    assert not offenders, "read these through boot_guard.env_flag:\n" + "\n".join(offenders)


@pytest.mark.parametrize("planted", [
    'os.environ.get("KAZMA_PRODUCTION", "").lower() in ("1", "true", "yes")',
    '(_os.environ.get("KAZMA_DEV_WS_BYPASS") or "").strip() in ("1",)',
    'os.getenv("KAZMA_AUTH_DISABLED") == "1"',
    'os.environ["KAZMA_DEMO_MODE"]',
])
def test_the_gate_sees_a_planted_reader(planted: str) -> None:
    """Negative control: each shape the 2026-11-10 sites used is caught."""
    assert _switch_reads(f"import os\nx = {planted}\n")


def test_the_gate_ignores_writes_and_other_names() -> None:
    assert not _switch_reads('import os\nos.environ["KAZMA_DEMO_MODE"] = "1"\n')
    assert not _switch_reads('import os\nos.environ.get("KAZMA_SECRET")\n')


# ── behaviour ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("spelling", ["1", "true", "yes", "on", " ON "])
def test_every_truthy_spelling_is_on(monkeypatch: pytest.MonkeyPatch, spelling: str) -> None:
    monkeypatch.setenv("KAZMA_DEV_WS_BYPASS", spelling)
    assert env_flag("KAZMA_DEV_WS_BYPASS")


def test_ws_bypass_on_is_refused_on_a_network_bind(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAZMA_DEV_WS_BYPASS", "on")
    assert check_exposure_posture("0.0.0.0")[0] is False


def test_the_old_guard_spelling_let_on_through(monkeypatch: pytest.MonkeyPatch) -> None:
    """Negative control: the guard's old reader, while the socket check
    honoured ``on``."""
    monkeypatch.setenv("KAZMA_DEV_WS_BYPASS", "on")
    monkeypatch.setattr(
        boot_guard, "env_flag",
        lambda name: boot_guard.os.environ.get(name, "").strip().lower() in ("1", "true", "yes"),
    )
    assert check_exposure_posture("0.0.0.0") == (True, "")


def _probe_client() -> TestClient:
    app = FastAPI()

    @app.get("/api/settings/probe")
    def probe() -> dict[str, str]:
        return {"ok": "yes"}

    app.middleware("http")(auth.create_auth_middleware())
    # Opened as http://127.0.0.1: with sign-in off a loopback peer's Host
    # must name this machine (TestClient's default "testserver" does not).
    return TestClient(app, client=("127.0.0.1", 40000), base_url="http://127.0.0.1:9090")


def test_production_on_refuses_auth_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAZMA_AUTH_DISABLED", "1")
    client = _probe_client()
    assert client.get("/api/settings/probe").status_code == 200
    monkeypatch.setenv("KAZMA_PRODUCTION", "on")
    refused = client.get("/api/settings/probe")
    assert refused.status_code == 503
    assert "KAZMA_PRODUCTION" in refused.json()["detail"]


def _socket(headers: dict[str, str]) -> WebSocket:
    scope = {
        "type": "websocket",
        "path": "/ws/chat",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "client": ("127.0.0.1", 40001),
        "query_string": b"",
    }

    async def _receive():  # pragma: no cover - never awaited
        return {}

    async def _send(_message):  # pragma: no cover - never awaited
        return None

    return WebSocket(scope, _receive, _send)


def test_production_on_refuses_the_ws_bypass(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAZMA_DEV_WS_BYPASS", "on")
    socket = _socket({"host": "127.0.0.1:9090"})
    assert auth.websocket_is_authenticated(socket, expected_secret="s" * 32)
    monkeypatch.setenv("KAZMA_PRODUCTION", "on")
    # A remote-looking socket (no peer trust) now needs a credential.
    remote = _socket({"host": "127.0.0.1:9090", "origin": "https://elsewhere.example"})
    assert not auth.websocket_is_authenticated(remote, expected_secret="s" * 32)
