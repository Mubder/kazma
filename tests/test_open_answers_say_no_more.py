"""What Kazma answers before sign-in says no more than it must (2026-11).

- FA-130: the GitHub OAuth result page put its message into HTML as text;
  a token-exchange failure quotes GitHub's own answer there.
- FA-131: ``/health/ready`` and ``/health/deep`` answer without sign-in, and
  three of their checks returned the exception's text (a DSN host, a path),
  five more ``Type: text``. A failed check now shows the exception's type, a
  stable code and a reference, and the text is in the log under it.
- FA-132: the Telegram webhook mount is open to anyone, and logged a WARNING
  for every refused POST; a stranger filled the log and the daily digest.

Each with its negative control: escaping off, the old ``"error": str(e)``
shape read by the gate, the throttle window at zero.
"""

from __future__ import annotations

import ast
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
HEALTH = ROOT / "kazma-ui" / "kazma_ui" / "health.py"

# ── FA-130 ───────────────────────────────────────────────────────────────────

_HOSTILE = '<img src=x onerror="alert(1)">'


def test_the_oauth_result_page_shows_its_message_as_text():
    from kazma_gateway.routers.github import _oauth_result_page

    body = _oauth_result_page(False, f"Token exchange failed: {_HOSTILE}").body.decode()
    assert "<img" not in body
    assert "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;" in body


def test_negative_control_unescaped_the_markup_reaches_the_page(monkeypatch):
    import html

    from kazma_gateway.routers.github import _oauth_result_page

    monkeypatch.setattr(html, "escape", lambda s, quote=True: s)
    body = _oauth_result_page(False, _HOSTILE).body.decode()
    assert "<img src=x" in body


# ── FA-131 ───────────────────────────────────────────────────────────────────

_DSN = "postgresql://kazma:hunter2@db.internal.example:5432/kazma"


def test_a_failed_database_check_shows_no_exception_text(monkeypatch, caplog):
    from kazma_core.db import backend, postgres_pool

    from kazma_ui import health

    class _Pool:
        def execute_one(self, _sql):
            raise RuntimeError(f"connection to {_DSN} failed: C:\\kazma\\secret.db")

    monkeypatch.setattr(backend, "is_postgres", lambda: True)
    monkeypatch.setattr(postgres_pool, "get_postgres_pool", lambda: _Pool())
    monkeypatch.delenv("KAZMA_VERBOSE_ERRORS", raising=False)
    with caplog.at_level(logging.ERROR, logger="kazma_ui.health"):
        result = health.check_database()
    assert result["status"] == "failed"
    error = result["error"]
    for leaked in ("hunter2", "db.internal", "secret.db", "kazma:"):
        assert leaked not in error, error
    assert error.startswith("RuntimeError: ") and "(ref: " in error
    ref = error.split("(ref: ")[1].rstrip(")")
    # The operator still finds the real failure, under the same reference.
    assert any(ref in r.getMessage() and "hunter2" in r.getMessage() for r in caplog.records)


def _uses_exception_text(value: ast.AST, name: str) -> bool:
    """True when *value* reads the exception *name* other than as
    ``type(name)`` -- its type's name says nothing about the install."""
    typed = {id(call.args[0]) for call in ast.walk(value)
             if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
             and call.func.id == "type" and len(call.args) == 1}
    return any(isinstance(n, ast.Name) and n.id == name and id(n) not in typed
               for n in ast.walk(value))


def _raw_error_values(source: str) -> list[int]:
    """Lines where an ``except ... as NAME`` handler puts NAME's text into an
    ``"error"`` value of an answer (directly, ``str(NAME)`` or an f-string)."""
    found: list[int] = []
    for handler in ast.walk(ast.parse(source)):
        if not (isinstance(handler, ast.ExceptHandler) and handler.name):
            continue
        for node in ast.walk(handler):
            if not isinstance(node, ast.Dict):
                continue
            for key, value in zip(node.keys, node.values, strict=False):
                if (isinstance(key, ast.Constant) and key.value == "error"
                        and _uses_exception_text(value, handler.name)):
                    found.append(node.lineno)
    return found


def test_no_health_answer_carries_a_caught_exception():
    assert _raw_error_values(HEALTH.read_text(encoding="utf-8")) == []


def test_negative_control_the_old_shape_is_found():
    old = (
        "def check():\n"
        "    try:\n        pass\n"
        "    except Exception as e:\n"
        "        return {'status': 'failed', 'error': str(e)}\n"
        "def deep():\n"
        "    try:\n        pass\n"
        "    except Exception as e:\n"
        "        return {'error': f'{type(e).__name__}: {e}'[:300]}\n"
    )
    assert _raw_error_values(old) == [5, 10]


# ── FA-132 ───────────────────────────────────────────────────────────────────


@pytest.fixture
def webhook(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from kazma_gateway.adapters import telegram

    monkeypatch.setattr(telegram, "_webhook_warnings", {})
    adapter = SimpleNamespace(_webhook_secret="right-secret", _queue=None)
    app = FastAPI()
    app.include_router(telegram.telegram_webhook_router(lambda: adapter), prefix="/hook")
    return TestClient(app), telegram


def _refused_warnings(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records
            if r.levelno == logging.WARNING and "telegram-webhook" in r.getMessage()]


def test_refused_webhook_posts_warn_once_per_window(webhook, caplog, monkeypatch):
    client, telegram = webhook
    with caplog.at_level(logging.DEBUG, logger=telegram.__name__):
        for _ in range(5):
            assert client.post("/hook", headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"}).status_code == 401
        assert len(_refused_warnings(caplog)) == 1
        # The window passes: the next refusal says how many were held back.
        last, held = telegram._webhook_warnings["bad_secret"]
        telegram._webhook_warnings["bad_secret"] = (last - telegram._WEBHOOK_WARN_EVERY_S - 1, held)
        client.post("/hook", headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"})
    warnings = _refused_warnings(caplog)
    assert len(warnings) == 2 and "(4 more refused since the last warning)" in warnings[1]


def test_negative_control_no_window_warns_every_post(webhook, caplog, monkeypatch):
    client, telegram = webhook
    monkeypatch.setattr(telegram, "_WEBHOOK_WARN_EVERY_S", 0.0)
    with caplog.at_level(logging.WARNING, logger=telegram.__name__):
        for _ in range(5):
            client.post("/hook", headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"})
    assert len(_refused_warnings(caplog)) == 5


# ── FA-219 ───────────────────────────────────────────────────────────────────


@pytest.fixture
def auth_status(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from kazma_ui.routes_direct.auth import register_auth_routes

    monkeypatch.setenv("KAZMA_SECRET", "s" * 40)
    monkeypatch.setenv("KAZMA_TRUST_LAN", "0")
    monkeypatch.setenv("KAZMA_TRUSTED_PROXIES", "10.9.8.7")
    app = FastAPI()
    register_auth_routes(SimpleNamespace(app=app))
    return TestClient(app)


def test_before_sign_in_the_status_names_no_proxy(auth_status):
    body = auth_status.get("/api/auth/status").json()
    assert body == {"auth_enabled": True, "authenticated": False, "mode": "secret", "oidc": False}
    assert "10.9.8.7" not in repr(body)


def test_negative_control_signed_in_the_status_shows_the_proxy_check(auth_status):
    body = auth_status.get("/api/auth/status", headers={"X-Kazma-Secret": "s" * 40}).json()
    assert body["authenticated"] is True and "10.9.8.7" in body["proxy"]["trusted_proxies"]
