"""A kill switch plus a non-loopback bind must not start silently.

TypeSafe audit R2 (2026-09-19), and the only residual risk in that sweep
that survived being read against the real code. Two guards existed and did
not compose: the entry points ask "is there a secret?", ``kazma_ui.auth``
asks "is this labelled production?", and neither asks "are you exposed?".

Reproduced before the fix:

    KAZMA_HOST=0.0.0.0  KAZMA_SECRET=<strong>  KAZMA_AUTH_DISABLED=1
    -> startup guard PASSED, binding 0.0.0.0, every /api endpoint open

``KAZMA_PRODUCTION`` is opt-in, so a VPS, a LAN box or a tunnel without that
label inherited nothing from the auth-side guard either.
"""

from __future__ import annotations

import ast
import types
from pathlib import Path

import pytest

from kazma_core.security.boot_guard import check_exposure_posture, is_loopback

_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _clean_switches(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("KAZMA_AUTH_DISABLED", "KAZMA_DEMO_MODE", "KAZMA_PRODUCTION"):
        monkeypatch.delenv(var, raising=False)


# ── The refusal ────────────────────────────────────────────────────────


@pytest.mark.parametrize("host", ["0.0.0.0", "10.0.0.5", "::", "192.168.1.20"])
def test_auth_disabled_on_a_public_bind_refuses(
    host: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("KAZMA_AUTH_DISABLED", "1")
    ok, msg = check_exposure_posture(host)
    assert ok is False, f"{host} with auth disabled was allowed to start"
    assert "refusing to start" in msg
    assert host in msg, "the message must name the bind the operator chose"


def test_the_refusal_does_not_depend_on_kazma_production(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """The gap was exactly this: PRODUCTION is an opt-in label.

    ``kazma_ui.auth`` only refuses a kill switch when KAZMA_PRODUCTION is
    also set, so every deployment that never set it — a VPS, a LAN box, a
    tunnel — was unprotected. Exposure is a property of the bind, not of a
    label someone remembered to apply.
    """
    monkeypatch.setenv("KAZMA_AUTH_DISABLED", "1")
    assert check_exposure_posture("0.0.0.0")[0] is False
    monkeypatch.setenv("KAZMA_PRODUCTION", "1")
    assert check_exposure_posture("0.0.0.0")[0] is False


def test_a_strong_secret_does_not_buy_past_the_guard(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """A secret cannot protect an endpoint whose gate is switched off.

    This is the composition failure in one assertion: the secret satisfies
    the older boot check, and the kill switch then makes it irrelevant.
    """
    monkeypatch.setenv("KAZMA_SECRET", "a-perfectly-strong-random-secret-xyz")
    monkeypatch.setenv("KAZMA_AUTH_DISABLED", "1")
    ok, msg = check_exposure_posture("0.0.0.0")
    assert ok is False
    assert "does not help" in msg


def test_ws_bypass_on_a_public_bind_is_refused(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """``KAZMA_DEV_WS_BYPASS`` is the same risk as AUTH_DISABLED, one layer down.

    It skips authentication on every WebSocket handshake, including the chat
    socket that carries the turn stream. ``kazma_ui.auth`` refuses it only when
    KAZMA_PRODUCTION is set — which is exactly the opt-in label the module
    docstring explains nobody sets on a VPS, a LAN box or a tunnel. So the
    composition this guard was written to close survived here after being
    closed for its sibling, and a 2026-09-20 audit found it still open.

    WS is not a lesser surface. Refused on a non-loopback bind.
    """
    monkeypatch.setenv("KAZMA_DEV_WS_BYPASS", "1")
    ok, msg = check_exposure_posture("0.0.0.0")
    assert ok is False
    assert "KAZMA_DEV_WS_BYPASS" in msg
    # And the production label must not be what makes it safe.
    monkeypatch.setenv("KAZMA_PRODUCTION", "1")
    assert check_exposure_posture("0.0.0.0")[0] is False


def test_demo_mode_does_not_excuse_the_ws_bypass(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """Demo mode means "no login", not "every switch is fair game".

    DEMO_MODE is deliberately allowed on a public bind. It is checked LAST so
    the two hard refusals still apply inside it — otherwise setting one
    permitted flag would silently re-open the other two.
    """
    monkeypatch.setenv("KAZMA_DEMO_MODE", "1")
    monkeypatch.setenv("KAZMA_DEV_WS_BYPASS", "1")
    ok, msg = check_exposure_posture("0.0.0.0")
    assert ok is False
    assert "KAZMA_DEV_WS_BYPASS" in msg


def test_ws_bypass_on_loopback_still_works(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """The switch exists for local development; that use is untouched."""
    monkeypatch.setenv("KAZMA_DEV_WS_BYPASS", "1")
    ok, msg = check_exposure_posture("127.0.0.1")
    assert ok is True and msg == ""


# ── What must keep working ─────────────────────────────────────────────


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1", "LOCALHOST"])
def test_loopback_is_never_refused(host: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Local development is the normal case and must stay frictionless."""
    monkeypatch.setenv("KAZMA_AUTH_DISABLED", "1")
    assert is_loopback(host)
    ok, msg = check_exposure_posture(host)
    assert ok is True and msg == ""


def test_demo_mode_on_a_public_bind_is_allowed_but_loud(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """KAZMA_DEMO_MODE exists to serve a public demo without login.

    Refusing it would break the thing it is for (``fly.toml`` carries it
    ready to uncomment), so it warns instead. The two switches are NOT the
    same risk: AUTH_DISABLED is a local convenience with no remote use,
    DEMO_MODE is a deliberate public posture.
    """
    monkeypatch.setenv("KAZMA_DEMO_MODE", "1")
    ok, msg = check_exposure_posture("0.0.0.0")
    assert ok is True, "demo mode on a public bind must still start"
    assert msg, "...but it must say so loudly"
    assert "throwaway" in msg


def test_no_switches_is_silent(monkeypatch: pytest.MonkeyPatch) -> None:
    """A normal secured public bind must not grow a scary banner."""
    monkeypatch.setenv("KAZMA_SECRET", "a-perfectly-strong-random-secret-xyz")
    assert check_exposure_posture("0.0.0.0") == (True, "")


def test_auth_disabled_beats_demo_mode_when_both_are_set(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ambiguity resolves to the refusal, not to the permissive branch."""
    monkeypatch.setenv("KAZMA_AUTH_DISABLED", "1")
    monkeypatch.setenv("KAZMA_DEMO_MODE", "1")
    assert check_exposure_posture("0.0.0.0")[0] is False


# ── Both entry points inherit it ───────────────────────────────────────


def test_every_entry_point_runs_the_posture_check() -> None:
    """The bind/secret logic is copy-pasted into serve.py and the CLI.

    A check that lives in each caller is a check that goes missing from one
    of them — that is how every chat-platform Install button bypassed
    ALLOWED_PACKAGES until 2026-09-12. Both must import the shared guard.
    """
    for rel in ("serve.py", "kazma-cli/kazma_cli/main.py"):
        src = (_ROOT / rel).read_text(encoding="utf-8")
        assert "check_exposure_posture" in src, f"{rel} skips the posture check"
        assert "boot_guard" in src, f"{rel} re-implements it instead of importing"


def test_serve_py_refuses_the_reported_combination() -> None:
    """End to end on the real function, minus starting a server.

    serve.py calls _bootstrap_bind_and_secret() at import time, so the
    functions are lifted out by AST rather than executing the module.
    """
    import os

    src = (_ROOT / "serve.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    keep = [
        n for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.Assign, ast.Import, ast.ImportFrom))
    ]
    mod = types.ModuleType("serve_probe")
    exec(compile(ast.Module(body=keep, type_ignores=[]), "serve.py", "exec"), mod.__dict__)

    prev = {k: os.environ.get(k) for k in
            ("KAZMA_HOST", "KAZMA_SECRET", "KAZMA_AUTH_DISABLED", "KAZMA_PRODUCTION")}
    try:
        os.environ["KAZMA_HOST"] = "0.0.0.0"
        os.environ["KAZMA_SECRET"] = "a-perfectly-strong-random-secret-xyz"
        os.environ["KAZMA_AUTH_DISABLED"] = "1"
        os.environ.pop("KAZMA_PRODUCTION", None)
        with pytest.raises(SystemExit) as exc:
            mod._bootstrap_bind_and_secret()
        assert exc.value.code == 1
    finally:
        for k, v in prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
