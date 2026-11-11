"""Sign-in switched off stays on this machine, whatever starts Kazma.

Two holes from the security audit of 2026-11-10:

* The exposure check ran in ``serve.py`` and ``kazma serve`` only. The Docker
  image starts ``uvicorn kazma_ui.app:create_app --factory --host 0.0.0.0``,
  and ``kazma-web`` called ``create_app()`` with no check, so
  ``KAZMA_AUTH_DISABLED=1`` on a network bind served every /api route.
* With sign-in off nothing looked at the ``Host``: a page on an attacker's
  domain rebound to 127.0.0.1 is a loopback peer whose ``Origin`` matches its
  ``Host``, so it reached the whole API (and the CSRF check, which trusts the
  origin the request names, let its writes through).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from kazma_core.security import boot_guard
from kazma_core.security.boot_guard import factory_exposure_refusal
from kazma_ui import auth
from kazma_ui.csrf import create_csrf_middleware
from kazma_ui.proxy_headers import ForwardedHeadersMiddleware
from starlette.testclient import TestClient
from starlette.websockets import WebSocket

_ROOT = Path(__file__).resolve().parents[1]
DOCKER_ARGV = ["/usr/local/lib/python3.11/site-packages/uvicorn/__main__.py",
               "kazma_ui.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
LOOPBACK = ("127.0.0.1", 40123)
REBOUND = {"host": "rebind.attacker.example:9090", "origin": "http://rebind.attacker.example:9090"}


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("KAZMA_AUTH_DISABLED", "KAZMA_DEV_WS_BYPASS", "KAZMA_DEMO_MODE",
                "KAZMA_PRODUCTION", "KAZMA_SECRET", "KAZMA_HOST", "KAZMA_TRUSTED_PROXIES",
                "KAZMA_AUTOLOGIN_HOSTS", "KAZMA_PUBLIC_URL", "KAZMA_CORS_ORIGINS", "UVICORN_HOST"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(boot_guard, "_launcher_checked", [None])
    auth.reset_proxy_detection()
    yield
    auth.reset_proxy_detection()


# ── the factory ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("switch", ["KAZMA_AUTH_DISABLED", "KAZMA_DEV_WS_BYPASS"])
def test_the_docker_command_with_a_sign_in_switch_is_refused(
    monkeypatch: pytest.MonkeyPatch, switch: str
) -> None:
    monkeypatch.setenv(switch, "1")
    refusal = factory_exposure_refusal(DOCKER_ARGV)
    assert switch in refusal and "0.0.0.0" in refusal


def test_create_app_refuses_before_building(monkeypatch: pytest.MonkeyPatch) -> None:
    from kazma_ui import app as app_module

    monkeypatch.setenv("KAZMA_AUTH_DISABLED", "1")
    import sys

    monkeypatch.setattr(sys, "argv", DOCKER_ARGV)
    built: list[bool] = []
    monkeypatch.setattr(app_module, "KazmaAppBuilder", lambda *a, **k: built.append(True))
    with pytest.raises(SystemExit):
        app_module.create_app()
    assert not built


def test_the_same_command_on_loopback_builds(monkeypatch: pytest.MonkeyPatch) -> None:
    """Negative control: the switch on a loopback bind is local development."""
    monkeypatch.setenv("KAZMA_AUTH_DISABLED", "1")
    loopback = [*DOCKER_ARGV[:4], "127.0.0.1", *DOCKER_ARGV[5:]]
    assert factory_exposure_refusal(loopback) == ""
    no_host = [a for a in DOCKER_ARGV if a not in ("--host", "0.0.0.0")]
    assert factory_exposure_refusal(no_host) == "", "uvicorn's default bind is 127.0.0.1"


def test_no_switch_is_a_no_op(monkeypatch: pytest.MonkeyPatch) -> None:
    assert factory_exposure_refusal(DOCKER_ARGV) == ""
    monkeypatch.setenv("KAZMA_DEMO_MODE", "1")  # the public demo is allowed
    assert factory_exposure_refusal(DOCKER_ARGV) == ""


def test_an_unknown_bind_is_refused_and_a_checked_one_is_not(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAZMA_AUTH_DISABLED", "1")
    assert "unknown" in factory_exposure_refusal(["some_launcher.py"])
    monkeypatch.setenv("KAZMA_HOST", "127.0.0.1")
    assert factory_exposure_refusal(["some_launcher.py"]) == ""
    monkeypatch.setenv("KAZMA_HOST", "0.0.0.0")
    assert factory_exposure_refusal(["some_launcher.py"])
    boot_guard.note_launcher_checked("127.0.0.1")
    assert factory_exposure_refusal(["some_launcher.py"]) == ""


def test_every_launcher_checks_and_says_so() -> None:
    for rel in ("serve.py", "kazma-cli/kazma_cli/main.py", "kazma-ui/kazma_ui/app.py"):
        src = (_ROOT / rel).read_text(encoding="utf-8")
        assert "check_exposure_posture" in src, rel
        assert "note_launcher_checked" in src, rel
    app_src = (_ROOT / "kazma-ui/kazma_ui/app.py").read_text(encoding="utf-8")
    assert "factory_exposure_refusal()" in app_src


def test_the_cli_checks_before_it_builds() -> None:
    src = (_ROOT / "kazma-cli/kazma_cli/main.py").read_text(encoding="utf-8")
    assert src.index("check_exposure_posture(_posture_host)") < src.index("app = create_app()")


# ── DNS rebinding while sign-in is off ──────────────────────────────────


def _client(peer: tuple[str, int] = LOOPBACK, forwarded: bool = False) -> TestClient:
    app = FastAPI()

    @app.get("/api/settings/probe")
    def probe() -> dict[str, str]:
        return {"ok": "yes"}

    @app.post("/api/settings/probe")
    def write() -> dict[str, str]:
        return {"ok": "written"}

    @app.get("/api/chat/probe")
    def chat() -> dict[str, str]:
        return {"ok": "chat"}

    app.middleware("http")(create_csrf_middleware())
    app.middleware("http")(auth.create_auth_middleware())
    return TestClient(ForwardedHeadersMiddleware(app) if forwarded else app, client=peer)


@pytest.mark.parametrize("mode", ["KAZMA_AUTH_DISABLED", "KAZMA_DEMO_MODE", "pytest-empty-secret"])
def test_a_rebound_domain_is_refused(monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    if mode != "pytest-empty-secret":
        monkeypatch.setenv(mode, "1")
    client = _client()
    assert client.get("/api/settings/probe", headers=REBOUND).status_code == 421
    assert client.post("/api/settings/probe", headers=REBOUND).status_code == 421


@pytest.mark.parametrize("host", [
    "127.0.0.1:9090", "localhost:9090", "LOCALHOST", "localhost.:9090", "[::1]:9090",
    "kazma.localhost:9090", "dev.test", "192.168.1.5:9090",
])
def test_this_machine_keeps_working(monkeypatch: pytest.MonkeyPatch, host: str) -> None:
    monkeypatch.setenv("KAZMA_AUTH_DISABLED", "1")
    assert _client().get("/api/settings/probe", headers={"host": host}).status_code == 200


# ── single-label names (2026-11-11) ─────────────────────────────────────

#: A name with no dot: the network's DNS search suffix, or a machine on the
#: LAN answering LLMNR / NetBIOS, can hand a browser such a name, and a page
#: served under it rebinds to 127.0.0.1 like any other.
SINGLE_LABEL = ["attacker:9090", "testserver", "wpad", "intranet:9090"]


@pytest.mark.parametrize("host", SINGLE_LABEL)
def test_a_single_label_name_is_refused(monkeypatch: pytest.MonkeyPatch, host: str) -> None:
    monkeypatch.setenv("KAZMA_AUTH_DISABLED", "1")
    monkeypatch.setattr(auth.socket, "gethostname", lambda: "kazma-box")
    headers = {"host": host, "origin": f"http://{host}"}
    client = _client()
    assert client.get("/api/settings/probe", headers=headers).status_code == 421
    assert client.post("/api/settings/probe", headers=headers).status_code == 421


def test_this_machines_own_name_with_and_without_its_domain(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAZMA_AUTH_DISABLED", "1")
    monkeypatch.setattr(auth.socket, "gethostname", lambda: "Kazma-Box.Corp.Example")
    client = _client()
    for host in ("kazma-box", "KAZMA-BOX:9090", "kazma-box.corp.example", "Kazma-Box.Corp.Example.",
                 "kazma-box.local", "kazma-box.lan:9090"):
        assert client.get("/api/settings/probe", headers={"host": host}).status_code == 200, host
    assert client.get("/api/settings/probe", headers={"host": "other-box"}).status_code == 421
    monkeypatch.setattr(auth.socket, "gethostname", lambda: "kazma-box")
    assert client.get("/api/settings/probe", headers={"host": "kazma-box:9090"}).status_code == 200


def test_a_single_label_name_the_owner_declares_works(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAZMA_AUTH_DISABLED", "1")
    monkeypatch.setattr(auth.socket, "gethostname", lambda: "kazma-box")
    monkeypatch.setenv("KAZMA_AUTOLOGIN_HOSTS", "nas, kazma.lan")
    client = _client()
    assert client.get("/api/settings/probe", headers={"host": "nas:9090"}).status_code == 200
    assert client.get("/api/settings/probe", headers={"host": "attacker:9090"}).status_code == 421


def _old_cannot_be_rebound(request) -> bool:
    """The rule as it was until 2026-11-11: any name without a dot passed."""
    host = (request.headers.get("host") or "").strip().lower()
    hostname = (host.rsplit(":", 1)[0] if host.count(":") == 1 else host).rstrip(".")
    return "." not in hostname or auth._host_is_local_name(request)


def test_negative_control_the_old_rule_let_a_single_label_name_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAZMA_AUTH_DISABLED", "1")
    monkeypatch.setattr(auth.socket, "gethostname", lambda: "kazma-box")
    monkeypatch.setattr(auth, "_host_cannot_be_rebound", _old_cannot_be_rebound)
    headers = {"host": "attacker:9090", "origin": "http://attacker:9090"}
    client = _client()
    assert client.get("/api/settings/probe", headers=headers).status_code == 200
    assert client.post("/api/settings/probe", headers=headers).status_code == 200


def test_a_declared_name_works(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAZMA_AUTH_DISABLED", "1")
    monkeypatch.setenv("KAZMA_AUTOLOGIN_HOSTS", "kazma.lan")
    assert _client().get("/api/settings/probe", headers={"host": "kazma.lan:9090"}).status_code == 200
    monkeypatch.setenv("KAZMA_PUBLIC_URL", "https://my.kazma.example")
    assert _client().get("/api/settings/probe", headers={"host": "my.kazma.example"}).status_code == 200


def test_the_public_demo_through_its_proxy_keeps_working(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAZMA_DEMO_MODE", "1")
    # fly.io / a Docker port: the peer is not loopback.
    assert _client(peer=("172.17.0.1", 5000)).get(
        "/api/chat/probe", headers={"host": "kazma-demo.fly.dev"}).status_code == 200
    # A declared same-host proxy (a tunnel): the peer is the proxy.
    monkeypatch.setenv("KAZMA_TRUSTED_PROXIES", "127.0.0.1")
    assert _client(forwarded=True).get(
        "/api/chat/probe",
        headers={"host": "demo.example.org", "x-forwarded-for": "203.0.113.9"}).status_code == 200


def test_a_secret_install_is_untouched(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sign-in on: the credential is the gate, the Host check stays out."""
    monkeypatch.setenv("KAZMA_SECRET", "s" * 32)
    resp = _client().get("/api/settings/probe",
                         headers={**REBOUND, auth.SECRET_HEADER: "s" * 32})
    assert resp.status_code == 200


def test_without_the_check_the_rebound_page_reached_the_api(monkeypatch: pytest.MonkeyPatch) -> None:
    """Negative control: the middleware without the Host check."""
    monkeypatch.setenv("KAZMA_AUTH_DISABLED", "1")
    monkeypatch.setattr(auth, "_open_mode_host_refused", lambda request: False)
    client = _client()
    assert client.get("/api/settings/probe", headers=REBOUND).status_code == 200
    assert client.post("/api/settings/probe", headers=REBOUND).status_code == 200


def _socket(headers: dict[str, str]) -> WebSocket:
    scope = {
        "type": "websocket",
        "path": "/ws/chat",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "client": LOOPBACK,
        "query_string": b"",
    }

    async def _receive():  # pragma: no cover
        return {}

    async def _send(_message):  # pragma: no cover
        return None

    return WebSocket(scope, _receive, _send)


def test_a_rebound_socket_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAZMA_AUTH_DISABLED", "1")
    assert not auth.websocket_is_authenticated(_socket(REBOUND))
    assert auth.websocket_is_authenticated(_socket({"host": "127.0.0.1:9090"}))
    monkeypatch.setenv("KAZMA_DEV_WS_BYPASS", "1")
    assert not auth.websocket_is_authenticated(_socket(REBOUND), expected_secret="s" * 32)
    assert auth.websocket_is_authenticated(_socket({"host": "localhost:9090"}), expected_secret="s" * 32)
