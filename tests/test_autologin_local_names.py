"""Only names that cannot point elsewhere count as this machine (2026-11-15).

With sign-in off, a loopback request is refused unless its ``Host`` cannot be
a DNS-rebinding page's (``auth._host_cannot_be_rebound``), and loopback auto
sign-in mints an admin cookie only for a local ``Host``
(``auth._host_is_local_name``). Both trusted any ``*.test`` name and this
machine's name with ``.local`` or ``.lan``: the LAN's DNS answers ``.test``
and ``.lan`` names, and mDNS ``.local`` ones, for other machines too, so a
page served under one and rebound to 127.0.0.1 passed as this machine. Now a
``.test``, ``.local`` or ``.lan`` name counts only when the owner names it in
``KAZMA_AUTOLOGIN_HOSTS``. The old names and suffixes are the negative control.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from kazma_core.security import boot_guard
from kazma_ui import auth
from kazma_ui.csrf import create_csrf_middleware
from starlette.requests import Request
from starlette.testclient import TestClient

LOOPBACK = ("127.0.0.1", 40124)
NOT_THIS_MACHINE = ["kazma-box.local", "kazma-box.lan:9090", "dev.test", "kazma.test:9090"]


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("KAZMA_AUTH_DISABLED", "KAZMA_DEMO_MODE", "KAZMA_PRODUCTION", "KAZMA_SECRET",
                "KAZMA_TRUSTED_PROXIES", "KAZMA_AUTOLOGIN_HOSTS", "KAZMA_PUBLIC_URL",
                "KAZMA_CORS_ORIGINS"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(boot_guard, "_launcher_checked", [None])
    monkeypatch.setattr(auth.socket, "gethostname", lambda: "kazma-box")
    monkeypatch.setenv("KAZMA_AUTH_DISABLED", "1")


def _client() -> TestClient:
    app = FastAPI()

    @app.get("/api/settings/probe")
    def probe() -> dict[str, str]:
        return {"ok": "yes"}

    app.middleware("http")(create_csrf_middleware())
    app.middleware("http")(auth.create_auth_middleware())
    return TestClient(app, client=LOOPBACK)


def _status(host: str) -> int:
    return _client().get("/api/settings/probe", headers={"host": host}).status_code


@pytest.mark.parametrize("host", NOT_THIS_MACHINE)
def test_a_lan_or_test_name_is_refused_while_sign_in_is_off(host: str) -> None:
    assert _status(host) == 421


@pytest.mark.parametrize("host", ["localhost:9090", "kazma.localhost", "kazma-box", "127.0.0.1"])
def test_this_machine_still_works(host: str) -> None:
    assert _status(host) == 200


def test_a_name_the_owner_declares_works(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAZMA_AUTOLOGIN_HOSTS", "dev.test, kazma-box.local")
    assert _status("dev.test:9090") == 200
    assert _status("kazma-box.local") == 200
    assert _status("other.test") == 421


def _request(host: str) -> Request:
    return Request({"type": "http", "method": "GET", "path": "/", "query_string": b"",
                    "headers": [(b"host", host.encode())], "client": LOOPBACK})


def test_loopback_auto_sign_in_needs_this_machines_own_name() -> None:
    assert auth._machine_names() == {"kazma-box"}
    for host in NOT_THIS_MACHINE:
        assert auth._host_is_local_name(_request(host)) is False, host
        assert auth._should_auto_issue_cookie(_request(host), "a-secret") is False, host
    assert auth._should_auto_issue_cookie(_request("kazma-box:9090"), "a-secret") is True


def _old_machine_names() -> set[str]:
    return {"kazma-box", "kazma-box.local", "kazma-box.lan"}


def test_negative_control_the_old_names_let_them_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth, "_machine_names", _old_machine_names)
    monkeypatch.setattr(auth, "_RESERVED_LOCAL_SUFFIXES", (".localhost", ".test"))
    for host in NOT_THIS_MACHINE:
        assert _status(host) == 200, host
    assert auth._should_auto_issue_cookie(_request("kazma-box.local"), "a-secret") is True
