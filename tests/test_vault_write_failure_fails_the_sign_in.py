"""A sign-in the vault cannot keep is a failed sign-in (FA-104).

The 2026-11 functional audit: ``email_manager.credentials.vault_store``
returned False when the vault could not write, and almost every caller --
the Gmail and Microsoft sign-ins, the app-password and IMAP connects, the
calendar copies, the token refreshes -- dropped the answer. The account then
showed "connected" and the next read found no token.

``vault_store`` now raises ``VaultWriteFailed``. The flows say it: the routes
answer 503 ``vault_unavailable`` with the reason, the OAuth finishes answer
``ok: false``, and the Graph backend's refresh (which goes on with the tokens
it holds) raises an ops alert. The class gate: no handler around a vault
write swallows the failure. The negative controls are the old ``vault_store``
(False, dropped) and the old calendar-probe handler.
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import pytest

from kazma_skills.native.email_manager import credentials

REPO = Path(__file__).resolve().parents[1]


class _BrokenVault:
    def store(self, *args, **kwargs):
        raise OSError("disk is read-only")

    def retrieve(self, *args, **kwargs):
        return None


@pytest.fixture()
def broken_vault(monkeypatch):
    from kazma_core.security import vault as vault_mod

    monkeypatch.setattr(vault_mod, "get_vault", lambda: _BrokenVault())
    for name in ("EMAIL_GMAIL_ACCESS_TOKEN", "EMAIL_GMAIL_REFRESH_TOKEN", "EMAIL_GMAIL_ADDRESS",
                 "EMAIL_GMAIL_SCOPES", "EMAIL_GMAIL_AUTH", "EMAIL_GMAIL_APP_PASSWORD",
                 "EMAIL_MS_ACCESS_TOKEN", "EMAIL_MS_REFRESH_TOKEN", "EMAIL_MS_AUTH",
                 "EMAIL_MS_CLIENT_ID", "EMAIL_MS_TENANT_ID", "EMAIL_IMAP_HOST", "EMAIL_SMTP_HOST"):
        monkeypatch.delenv(name, raising=False)


def test_a_refused_write_raises(broken_vault):
    with pytest.raises(credentials.VaultWriteFailed) as raised:
        credentials.vault_store("email.gmail.access_token", "secret-value-fa104")
    assert "not kept" in str(raised.value) and "secret-value-fa104" not in str(raised.value)
    assert credentials.vault_store("email.gmail.access_token", "") is False  # nothing to keep


@pytest.mark.parametrize("writer", [
    "gmail", "microsoft", "google_calendar", "outlook_calendar", "extra_account",
])
def test_every_token_writer_fails(broken_vault, writer):
    from kazma_skills.native.calendar import credentials as cal
    from kazma_skills.native.email_manager import accounts, oauth_gmail, oauth_ms

    calls = {
        "gmail": lambda: oauth_gmail.persist_gmail_tokens("a", "r", "x@example.com", "s"),
        "microsoft": lambda: oauth_ms.store_microsoft_tokens({"access_token": "a", "refresh_token": "r"}, "cid"),
        "google_calendar": lambda: cal.persist_google_tokens("a", "r", "x@example.com", "s"),
        "outlook_calendar": lambda: cal.persist_microsoft_tokens("a", "r", "s", "x@example.com"),
        "extra_account": lambda: accounts._vault_put("work", "access_token", "a"),
    }
    with pytest.raises(credentials.VaultWriteFailed):
        calls[writer]()


def test_the_gmail_sign_in_finishes_as_failed(broken_vault, monkeypatch):
    from kazma_skills.native.email_manager import oauth_gmail

    async def finish(code, state):
        oauth_gmail.persist_gmail_tokens("a", "r", "x@example.com", "s")
        return {"ok": True, "email": "x@example.com"}

    monkeypatch.setattr(oauth_gmail, "_finish_gmail_oauth", finish)
    result = asyncio.run(oauth_gmail.finish_gmail_oauth("code", "state"))
    assert result["ok"] is False and result["code"] == "vault_unavailable"


@pytest.mark.parametrize(("path", "body"), [
    ("/api/email/gmail/connect", {"address": "x@example.com", "app_password": "abcd efgh"}),
    ("/api/email/oauth/microsoft/client", {"client_id": "00000000-fa10-4000-8000-000000000104", "tenant_id": "common"}),
])
def test_the_routes_answer_503_with_the_reason(broken_vault, path, body):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from kazma_ui import email_api

    app = FastAPI()
    app.include_router(email_api.router)
    app.include_router(email_api.protected_router)
    resp = TestClient(app).post(path, json=body, headers={"origin": "http://testserver", "x-requested-with": "XMLHttpRequest"})
    assert resp.status_code == 503, resp.text
    assert resp.json()["error"] == "vault_unavailable" and "not kept" in resp.json()["message"]


def test_a_graph_refresh_not_kept_is_alerted(broken_vault, monkeypatch):
    from kazma_core.observability import ops_alerts
    from kazma_skills.native.email_manager.backends.microsoft_graph import MicrosoftGraphBackend

    raised: list[str] = []
    monkeypatch.setattr(ops_alerts, "alert", lambda key, *a, **k: raised.append(key) or True)
    backend = MicrosoftGraphBackend.__new__(MicrosoftGraphBackend)
    backend.account_alias = ""
    backend.access_token, backend.refresh_token = "new-a", "new-r"
    monkeypatch.setattr("kazma_skills.native.email_manager.refreshed_grants.keep_refreshed_microsoft_mail",
                        lambda *a, **k: credentials.vault_store("email.microsoft.access_token", "new-a"))
    backend._persist_tokens("old-r")
    assert raised == ["email.token_not_kept"]


def test_the_old_vault_store_was_dropped(broken_vault, monkeypatch):
    """Negative control: the old answer (False) left every writer quiet."""
    monkeypatch.setattr(credentials, "vault_store", lambda *a, **k: False)
    from kazma_skills.native.email_manager import oauth_gmail

    monkeypatch.setattr(oauth_gmail, "vault_store", lambda *a, **k: False)
    oauth_gmail.persist_gmail_tokens("a", "r", "x@example.com", "s")  # no error: "connected"


# ── The class gate ──────────────────────────────────────────────────────

#: Calls that write a sign-in to the vault.
WRITERS = {
    "vault_store", "_vault_store", "_vault_put", "persist_gmail_tokens", "persist_google_tokens",
    "persist_microsoft_tokens", "store_microsoft_tokens", "store_microsoft_calendar_tokens",
    "store_microsoft_account_tokens", "persist_account_tokens", "keep_refreshed_microsoft_grant",
    "keep_refreshed_google_grant", "keep_refreshed_microsoft_mail", "turn_calendar_on",
    "turn_calendar_off", "_keep_extra_account",
}
SCANNED = [
    "kazma-skills/kazma_skills/native/email_manager",
    "kazma-skills/kazma_skills/native/calendar",
    "kazma-ui/kazma_ui/email_api.py",
    "kazma-ui/kazma_ui/calendar_api.py",
]
#: Handlers that log a failed write and go on, with why that is right.
DECLARED = {
    ("email_manager/backends/microsoft_graph.py", "_persist_tokens"):
        "the request goes on with the tokens it holds; the owner gets an ops alert",
}


def _name(call: ast.Call) -> str:
    return getattr(call.func, "id", None) or getattr(call.func, "attr", None) or ""


def _writes(stmts: list[ast.stmt]) -> bool:
    for stmt in stmts:
        stack = [stmt]
        while stack:
            node = stack.pop()
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue  # a nested function is its own scope
            if isinstance(node, ast.Call) and _name(node) in WRITERS:
                return True
            stack.extend(ast.iter_child_nodes(node))
    return False


def swallowing_handlers(source: str, label: str) -> list[str]:
    """Handlers around a vault write that neither answer nor raise, with no
    earlier handler that passes ``VaultWriteFailed`` on."""
    out = []
    tree = ast.parse(source)
    for func in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        for node in ast.walk(func):
            if not isinstance(node, ast.Try) or not _writes(node.body):
                continue
            passed_on = False
            for handler in node.handlers:
                kind = ast.unparse(handler.type) if handler.type else "BaseException"
                answers = any(isinstance(n, (ast.Return, ast.Raise)) for n in ast.walk(handler))
                if "VaultWriteFailed" in kind and answers:
                    passed_on = True
                    continue
                broad = kind in ("Exception", "BaseException") or "RuntimeError" in kind
                if broad and not answers and not passed_on:
                    out.append(f"{label}:{func.name}:{handler.lineno} except {kind}")
    return out


def _files():
    for entry in SCANNED:
        path = REPO / entry
        yield from ([path] if path.is_file() else sorted(path.rglob("*.py")))


def test_no_handler_swallows_a_failed_vault_write():
    found = []
    for path in _files():
        label = path.relative_to(REPO / "kazma-skills/kazma_skills/native").as_posix() \
            if "native" in path.parts else path.name
        for hit in swallowing_handlers(path.read_text(encoding="utf-8"), label):
            label_key = (hit.split(":")[0], hit.split(":")[1])
            if label_key not in DECLARED:
                found.append(hit)
    assert not found, "a vault write's failure is swallowed (FA-104): " + "; ".join(found)


def test_the_gate_catches_the_old_calendar_probe():
    """Negative control: the Gmail sign-in's calendar probe before FA-104."""
    old = '''
async def finish():
    try:
        persist_google_tokens(access, refresh, email, scopes, probe_ok="ok")
        vault_store("calendar.google.ok", "missing", category="calendar")
    except Exception as exc:
        logger.debug("[email.oauth] calendar probe: %s", exc)
        calendar_ok = False
'''
    found = swallowing_handlers(old, "old")
    assert len(found) == 1 and found[0].startswith("old:finish:") and found[0].endswith("except Exception")
    fixed = old.replace("    except Exception as exc:", "    except VaultWriteFailed:\n        raise\n    except Exception as exc:")
    assert swallowing_handlers(fixed, "fixed") == []
