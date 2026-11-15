"""A secret Kazma was given is compared as bytes, never as text (2026-11-15).

``hmac.compare_digest`` takes two ``str`` only when both are ASCII: anything
else raises ``TypeError``. Three open routes compared a value from the request
that way -- the Telegram webhook's secret header, WhatsApp's verify token (a
query value) and its signature header -- and answered a 500 with a traceback
to anyone who sent one non-ASCII character. ``secret_compare.same_secret`` is
the one comparison: UTF-8 bytes, constant time, a value that is neither text
nor bytes never equal. This file holds the routes to it and every product
module to the helper (found from the source), each with its control.
"""

from __future__ import annotations

import ast
import asyncio
import hmac
import subprocess
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from kazma_core.security.secret_compare import same_secret

ROOT = Path(__file__).resolve().parents[1]
HELPER = "kazma-core/kazma_core/security/secret_compare.py"

#: Modules that call ``compare_digest`` themselves, with why.
DECLARED = {
    "kazma-core/kazma_core/documents/worker_broker.py": (
        "standard library only (the guard loads it by path, so it imports nothing of Kazma's), and it "
        "already compares the token's UTF-8 bytes with the bytes it holds"),
}


def test_same_secret_compares_any_text_as_bytes() -> None:
    assert same_secret("right-secret", "right-secret")
    assert same_secret("سرّ", "سرّ") and same_secret(b"\xe9", b"\xe9")
    assert not same_secret("é", "e") and not same_secret("right-secret", "right-secreT")
    assert not same_secret("\ud800", "x"), "a lone surrogate is a character, not an exception"
    assert not same_secret(None, "x") and not same_secret("x", None) and not same_secret(7, "7")
    # The control: text compare_digest refuses a non-ASCII side outright.
    with pytest.raises(TypeError):
        hmac.compare_digest("é", "right-secret")


# ── the routes ───────────────────────────────────────────────────────────


def _telegram_client(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from kazma_gateway.adapters import telegram

    monkeypatch.setattr(telegram, "_webhook_warnings", {})
    adapter = SimpleNamespace(_webhook_secret="right-secret", _queue=None)
    app = FastAPI()
    app.include_router(telegram.telegram_webhook_router(lambda: adapter), prefix="/hook")
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize("header", [b"\xe9", "é".encode("utf-8"), "right-secret ".encode("latin-1")])
def test_a_non_ascii_telegram_secret_is_refused_not_a_500(monkeypatch, header) -> None:
    client = _telegram_client(monkeypatch)
    resp = client.post("/hook", headers={"X-Telegram-Bot-Api-Secret-Token": header}, json={})
    assert resp.status_code == 401
    assert "Traceback" not in resp.text


def test_whatsapp_verify_and_signature_refuse_non_ascii_not_a_500() -> None:
    from tests.test_whatsapp import SECRET, VERIFY, WEBHOOK_PATH, FakeMeta, _Rig, _payload, _text

    from kazma_gateway.adapters.whatsapp_parse import signature_ok

    assert not signature_ok(SECRET, b"{}", "sha256=" + "é" * 64)

    async def run() -> None:
        async with _Rig(FakeMeta()) as rig:
            # The app's own answer, a 500 included, instead of the exception.
            await rig.http.aclose()
            rig.http = httpx.AsyncClient(transport=httpx.ASGITransport(app=rig.app, raise_app_exceptions=False),
                                         base_url="http://kazma")
            bad = await rig.http.get(WEBHOOK_PATH, params={"hub.mode": "subscribe", "hub.verify_token": "تحقق",
                                                           "hub.challenge": "x"})
            assert bad.status_code == 403
            ok = await rig.http.get(WEBHOOK_PATH, params={"hub.mode": "subscribe", "hub.verify_token": VERIFY,
                                                          "hub.challenge": "1158"})
            assert ok.status_code == 200 and ok.text == "1158"
            body = b'{"object": "whatsapp_business_account", "entry": []}'
            forged = await rig.http.post(WEBHOOK_PATH, content=body,
                                         headers={"Content-Type": "application/json",
                                                  "X-Hub-Signature-256": "sha256=é".encode("latin-1")})
            assert forged.status_code == 401
            assert (await rig.post(_payload(_text("w1", "hello")))).status_code == 200

    asyncio.run(run())


def test_negative_control_the_text_compare_answered_500(monkeypatch) -> None:
    """The routes as they were: compare_digest on the text answers 500."""
    from kazma_core.security import secret_compare

    from kazma_gateway.adapters import whatsapp, whatsapp_parse

    monkeypatch.setattr(secret_compare, "same_secret", hmac.compare_digest)
    client = _telegram_client(monkeypatch)
    assert client.post("/hook", headers={"X-Telegram-Bot-Api-Secret-Token": b"\xe9"}, json={}).status_code == 500
    monkeypatch.setattr(whatsapp_parse, "same_secret", hmac.compare_digest)
    with pytest.raises(TypeError):
        whatsapp_parse.signature_ok("s", b"{}", "sha256=é")
    monkeypatch.setattr(whatsapp, "same_secret", hmac.compare_digest)
    adapter = SimpleNamespace(_verify_token="verify")
    with pytest.raises(TypeError):
        whatsapp.WhatsAppAdapter.verify_subscription(adapter, "subscribe", "é", "x")


def test_the_auth_secret_compare_takes_any_header() -> None:
    from kazma_ui.auth import verify_secret

    assert verify_secret("مفتاح", "مفتاح") and not verify_secret("é", "right-secret")


# ── the class: nothing else calls compare_digest ────────────────────────


def _product_files() -> list[str]:
    out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "*.py"], capture_output=True, text=True,
                         check=True).stdout.split()
    return [f for f in out if f.startswith(("kazma-", "scripts/", "deploy/")) and "/tests/" not in f]


def callers_of_compare_digest(source: str) -> list[int]:
    """Lines of every ``compare_digest(...)`` call in *source*, whatever it was imported as."""
    tree = ast.parse(source)
    aliases = {"compare_digest"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in ("hmac", "secrets"):
            aliases |= {a.asname or a.name for a in node.names if a.name == "compare_digest"}
    lines = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ""
            if name in aliases:
                lines.append(node.lineno)
    return lines


def test_nothing_but_the_helper_calls_compare_digest() -> None:
    found = {}
    for rel in _product_files():
        path = ROOT / rel
        lines = callers_of_compare_digest(path.read_text(encoding="utf-8"))
        if lines and rel != HELPER and rel not in DECLARED:
            found[rel] = lines
    assert not found, (
        "compare a secret with kazma_core.security.secret_compare.same_secret (bytes, never a "
        f"TypeError on a non-ASCII value), or declare the module in DECLARED with why: {found}")
    for rel in DECLARED:
        assert callers_of_compare_digest((ROOT / rel).read_text(encoding="utf-8")), f"{rel} no longer needs declaring"


def test_negative_control_the_gate_finds_a_planted_compare() -> None:
    planted = (
        "import hmac\nfrom secrets import compare_digest as cd\n"
        "def check(header, secret):\n    return hmac.compare_digest(header, secret) and cd(header, secret)\n"
    )
    assert callers_of_compare_digest(planted) == [4, 4]
    # The routes as they were, on the request this file sends.
    with pytest.raises(TypeError):
        hmac.compare_digest("é", "right-secret")
