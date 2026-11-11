"""Startup posture check: never open the API to a network by accident.

Two guards already existed and did not compose:

* ``serve.py`` / ``kazma serve`` refuse a non-loopback bind without a
  ``KAZMA_SECRET`` — they ask *"is there a secret?"*.
* ``kazma_ui.auth`` refuses ``KAZMA_AUTH_DISABLED`` / ``KAZMA_DEMO_MODE``
  when ``KAZMA_PRODUCTION`` is set — it asks *"is this labelled production?"*.

Neither asks **"are you exposed?"**, so this combination started cleanly and
served every ``/api/*`` endpoint to the network:

    KAZMA_HOST=0.0.0.0  KAZMA_SECRET=<strong>  KAZMA_AUTH_DISABLED=1

The secret satisfies the boot check, and the kill switch then makes the
secret irrelevant. ``KAZMA_PRODUCTION`` is opt-in, so a VPS, a LAN box or a
tunnel without that label inherits nothing from the second guard either.
(TypeSafe audit R2, 2026-09-19 — the one residual risk in that sweep that
survived being read against the real code.)

The kill switches are NOT the same risk and are not treated the same:

* ``KAZMA_AUTH_DISABLED`` is a local-development convenience. It has no
  documented remote use, so pairing it with a non-loopback bind is refused.
* ``KAZMA_DEV_WS_BYPASS`` is the same shape one layer down: it skips auth on
  every WebSocket handshake. It was gated only on ``KAZMA_PRODUCTION``, which
  is exactly the opt-in label the paragraph above explains nobody sets on a
  VPS — so the original composition survived here after being closed for its
  sibling. WS is not a lesser surface: the chat socket carries the turn
  stream. Refused on a non-loopback bind, same as ``KAZMA_AUTH_DISABLED``.
* ``KAZMA_DEMO_MODE`` exists precisely to serve a public throwaway demo
  without login (``fly.toml``). Refusing it would break the thing it is for,
  so it is allowed and announced loudly instead. It is checked LAST, so the
  two refusals above still apply inside a demo — demo mode means "no login",
  not "every switch is fair game".

This lives in one module because the bind/secret checks were already
copy-pasted into both entry points, and a check that lives in each caller is
a check that will go missing from one of them — the same failure that let
every chat-platform Install button bypass ``ALLOWED_PACKAGES`` until
2026-09-12 (``kazma_core.system.installer``).
"""

from __future__ import annotations

import os

__all__ = [
    "LOOPBACK_HOSTS",
    "SECURITY_SWITCHES",
    "WEAK_SECRET_CHARS",
    "env_flag",
    "exposure",
    "is_loopback",
    "check_exposure_posture",
    "note_launcher_checked",
    "factory_exposure_refusal",
]

#: Hosts that are not reachable from another machine.
LOOPBACK_HOSTS: frozenset[str] = frozenset({"127.0.0.1", "::1", "localhost"})

#: Shorter than this, a ``KAZMA_SECRET`` set by hand is guessable at scale. A
#: generated one is 32 hex characters (128 bits). ``kazma_ui.auth`` warns at
#: boot, and the security report fails its access check below it.
WEAK_SECRET_CHARS = 20


#: The switches that decide how open Kazma is. Product code reads them only
#: through :func:`env_flag` (gate: ``tests/test_security_switch_spelling.py``):
#: each place kept its own list of spellings, and they disagreed -- the
#: middleware honoured ``KAZMA_DEV_WS_BYPASS=on`` that this guard read as off,
#: and ``KAZMA_PRODUCTION=on`` meant production to the vault and not to the
#: auth gate (security audit 2026-11-10). The chat apps' two
#: (``kazma_gateway.sender_rule``, 2026-11-11) decide who may message Kazma
#: through a bot whose allowlist is empty.
SECURITY_SWITCHES: frozenset[str] = frozenset({
    "KAZMA_PRODUCTION",
    "KAZMA_AUTH_DISABLED",
    "KAZMA_DEMO_MODE",
    "KAZMA_DEV_WS_BYPASS",
    "KAZMA_GATEWAY_OPEN_TO_EVERYONE",
    "KAZMA_GATEWAY_STRICT_ALLOWLIST",
})

_TRUTHY = frozenset({"1", "true", "yes", "on"})


def env_flag(name: str) -> bool:
    """True when *name* is set to a truthy value: ``1``, ``true``, ``yes``, ``on``.

    The one reader of :data:`SECURITY_SWITCHES`, so the boot guard and the
    middleware cannot disagree about whether a switch is on.
    """
    return os.environ.get(name, "").strip().lower() in _TRUTHY


def is_loopback(host: str) -> bool:
    return str(host or "").strip().lower() in LOOPBACK_HOSTS


def exposure(host: str) -> str:
    """How another machine reaches Kazma on *host*; ``""`` when nothing can.

    A loopback bind is not private when a reverse proxy on the same machine
    fronts it (a Cloudflare tunnel, nginx, Caddy): every visitor arrives
    from ``127.0.0.1``. ``KAZMA_TRUSTED_PROXIES`` is how an operator says one
    does, so a declared proxy is exposure -- any entry, even the ``*`` that
    ``kazma_ui.auth`` refuses to trust: it still says a proxy is there.
    Until 2026-10-02 only the bind counted, and ``KAZMA_AUTH_DISABLED``
    behind a tunnel started cleanly with every ``/api`` route open to the
    internet.
    """
    if not is_loopback(host):
        return f"a non-loopback bind ({host})"
    if any(p.strip() for p in os.environ.get("KAZMA_TRUSTED_PROXIES", "").split(",")):
        return "a declared reverse proxy (KAZMA_TRUSTED_PROXIES)"
    return ""


def check_exposure_posture(host: str) -> tuple[bool, str]:
    """Decide whether this bind + auth-switch combination may start.

    Returns ``(ok, message)``. ``ok=False`` means refuse to start and print
    *message*; ``ok=True`` with a non-empty *message* is a warning to print
    and carry on. Pure — the caller owns the exit, so this is testable
    without spawning a server.
    """
    reached = exposure(host)
    if not reached:
        return True, ""

    if env_flag("KAZMA_AUTH_DISABLED"):
        return False, (
            f"\n  [SECURITY] KAZMA_AUTH_DISABLED with {reached} — "
            "refusing to start.\n"
            "  Every /api endpoint would be open to the network. A secret "
            "does not help:\n"
            "  the switch disables the gate that checks it.\n\n"
            "  Fix one of:\n"
            "    unset KAZMA_AUTH_DISABLED         (use the secret you set)\n"
            "    KAZMA_HOST=127.0.0.1, no proxy    (keep it local)\n"
            "    KAZMA_DEMO_MODE=1                 (a PUBLIC throwaway demo, "
            "no real data)\n"
        )

    if env_flag("KAZMA_DEV_WS_BYPASS"):
        return False, (
            f"\n  [SECURITY] KAZMA_DEV_WS_BYPASS with {reached} — "
            "refusing to start.\n"
            "  Every WebSocket handshake would skip authentication, including "
            "the chat socket\n"
            "  that carries the turn stream. KAZMA_PRODUCTION blocks this "
            "switch, but that label\n"
            "  is opt-in and a VPS or tunnel does not set it — which is the "
            "same gap that let\n"
            "  KAZMA_AUTH_DISABLED through before this guard existed.\n\n"
            "  Fix one of:\n"
            "    unset KAZMA_DEV_WS_BYPASS         (use the secret you set)\n"
            "    KAZMA_HOST=127.0.0.1, no proxy    (keep it local)\n"
        )

    if env_flag("KAZMA_DEMO_MODE"):
        return True, (
            f"\n  [SECURITY] KAZMA_DEMO_MODE with {reached}.\n"
            "  The ENTIRE auth gate is disabled and every /api endpoint is "
            "open to the network.\n"
            "  This is what demo mode is for — only run it on a throwaway "
            "instance with no real\n"
            "  data, credentials or vault.\n"
        )

    return True, ""


# ── Launchers that skip the check ───────────────────────────────────────
#
# ``serve.py``, ``kazma serve`` and ``kazma-web`` run the check above before
# they start the server. A launcher that builds the app through the factory
# itself (the Docker image: ``uvicorn kazma_ui.app:create_app --factory
# --host 0.0.0.0``) ran none, so ``KAZMA_AUTH_DISABLED=1`` there served every
# /api route to the network (security audit 2026-11-10). ``create_app()``
# therefore asks :func:`factory_exposure_refusal`, which is a no-op unless a
# switch the check refuses is on.

#: The host a launcher in this process checked and found allowed.
_launcher_checked: list[str | None] = [None]

#: The switches the check above refuses on an exposed bind.
_REFUSED_WHEN_EXPOSED = ("KAZMA_AUTH_DISABLED", "KAZMA_DEV_WS_BYPASS")


def note_launcher_checked(host: str) -> None:
    """A launcher ran :func:`check_exposure_posture` for *host* and it passed."""
    _launcher_checked[0] = str(host or "").strip() or None


def _uvicorn_cli_host(argv: list[str]) -> str | None:
    """The host uvicorn's command line binds, when uvicorn is the program.

    ``None`` when uvicorn is not the program or the bind cannot be told
    (a file descriptor handed over). Without ``--host`` uvicorn binds its
    ``UVICORN_HOST`` or ``127.0.0.1``.
    """
    if not argv:
        return None
    prog = str(argv[0]).replace("\\", "/").lower()
    # `python -m uvicorn` (the Docker images) or the `uvicorn` console script.
    if not (
        prog.endswith("/uvicorn/__main__.py")
        or prog.rsplit("/", 1)[-1] in ("uvicorn", "uvicorn.exe", "uvicorn-script.py")
    ):
        return None
    if "--fd" in argv or any(a.startswith("--fd=") for a in argv):
        return None
    for i, arg in enumerate(argv):
        if arg == "--host" and i + 1 < len(argv):
            return argv[i + 1]
        if arg.startswith("--host="):
            return arg.split("=", 1)[1]
    return os.environ.get("UVICORN_HOST") or "127.0.0.1"


def factory_exposure_refusal(argv: list[str] | None = None) -> str:
    """Why the app factory must not build this app; ``""`` when it may.

    ``""`` unless ``KAZMA_AUTH_DISABLED`` or ``KAZMA_DEV_WS_BYPASS`` is on and
    no launcher in this process checked the bind. Then the bind is read from
    uvicorn's command line, else ``KAZMA_HOST``; a bind that cannot be read
    is refused (it cannot be known to be loopback), and a known one gets the
    launchers' own check.
    """
    import sys

    if not any(env_flag(name) for name in _REFUSED_WHEN_EXPOSED):
        return ""
    if _launcher_checked[0] is not None:
        return ""
    host = _uvicorn_cli_host(list(sys.argv if argv is None else argv))
    if host is None:
        host = (os.environ.get("KAZMA_HOST") or "").strip() or None
    if host is None:
        switch = next(n for n in _REFUSED_WHEN_EXPOSED if env_flag(n))
        return (
            f"\n  [SECURITY] {switch} is on and this launcher's bind address "
            "is unknown — refusing to build the app.\n"
            "  A switch that turns sign-in off is allowed on this machine only, "
            "and nothing here says\n"
            "  the server listens on this machine only.\n\n"
            "  Fix one of:\n"
            f"    unset {switch}\n"
            "    KAZMA_HOST=127.0.0.1               (say the bind is loopback)\n"
            "    start Kazma with serve.py, `kazma serve` or `kazma-web`\n"
        )
    ok, message = check_exposure_posture(host)
    return "" if ok else message
