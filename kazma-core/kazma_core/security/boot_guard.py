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

The two kill switches are NOT the same risk and are not treated the same:

* ``KAZMA_AUTH_DISABLED`` is a local-development convenience. It has no
  documented remote use, so pairing it with a non-loopback bind is refused.
* ``KAZMA_DEMO_MODE`` exists precisely to serve a public throwaway demo
  without login (``fly.toml``). Refusing it would break the thing it is for,
  so it is allowed and announced loudly instead.

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
    "env_flag",
    "is_loopback",
    "check_exposure_posture",
]

#: Hosts that are not reachable from another machine.
LOOPBACK_HOSTS: frozenset[str] = frozenset({"127.0.0.1", "::1", "localhost"})


def env_flag(name: str) -> bool:
    """True when *name* is set to a truthy value.

    Matches the spelling ``kazma_ui.auth`` accepts, so the boot guard and the
    middleware cannot disagree about whether a switch is on.
    """
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes")


def is_loopback(host: str) -> bool:
    return str(host or "").strip().lower() in LOOPBACK_HOSTS


def check_exposure_posture(host: str) -> tuple[bool, str]:
    """Decide whether this bind + auth-switch combination may start.

    Returns ``(ok, message)``. ``ok=False`` means refuse to start and print
    *message*; ``ok=True`` with a non-empty *message* is a warning to print
    and carry on. Pure — the caller owns the exit, so this is testable
    without spawning a server.
    """
    if is_loopback(host):
        return True, ""

    if env_flag("KAZMA_AUTH_DISABLED"):
        return False, (
            "\n  [SECURITY] KAZMA_AUTH_DISABLED with a non-loopback bind "
            f"({host}) — refusing to start.\n"
            "  Every /api endpoint would be open to the network. A secret "
            "does not help:\n"
            "  the switch disables the gate that checks it.\n\n"
            "  Fix one of:\n"
            "    unset KAZMA_AUTH_DISABLED         (use the secret you set)\n"
            "    KAZMA_HOST=127.0.0.1              (keep it local)\n"
            "    KAZMA_DEMO_MODE=1                 (a PUBLIC throwaway demo, "
            "no real data)\n"
        )

    if env_flag("KAZMA_DEMO_MODE"):
        return True, (
            "\n  [SECURITY] KAZMA_DEMO_MODE with a non-loopback bind "
            f"({host}).\n"
            "  The ENTIRE auth gate is disabled and every /api endpoint is "
            "open to the network.\n"
            "  This is what demo mode is for — only run it on a throwaway "
            "instance with no real\n"
            "  data, credentials or vault.\n"
        )

    return True, ""
