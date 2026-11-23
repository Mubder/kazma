"""The cloud profile (Spaces S4-08): what a public install must have at boot.

An install reachable from the internet, with people on it who do not all
trust each other, needs more than one that runs on its owner's machine. The
operator says the install is public with ``KAZMA_CLOUD_PROFILE=1`` (read
through ``boot_guard.env_flag``, like the other switches that decide how open
Kazma is), and then every check below runs at boot. Each declares what a
missing piece does:

* **refuse** -- Kazma does not start, and says what is missing and how to fix
  it: the piece keeps a stranger out of the data, and an install without it
  must not serve. TLS (the public address is ``https``), a declared reverse
  proxy, sign-in on (no switch that turns it off, no hand-set secret that is
  guessable), and code that runs only in containers.
* **warn** -- Kazma starts and says it at WARNING: the piece costs resilience,
  not a boundary. A copy of the backups that leaves the machine, the
  production label, a bind reachable without the proxy, and a container
  engine on the machine (readiness waits for one that does not answer).

Two stages, so a refusal comes before Kazma waits for its database: the
checks that read only the environment run in ``create_app`` before the app
is built; those that read settings run in the builder once the settings store
answers. ``kazma doctor`` shows every check, the profile on or off.

A check that cannot be made is a missing piece (unknown is never a pass).
With the profile off nothing here runs at boot and nothing is read.
"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass

from kazma_core.security.boot_guard import WEAK_SECRET_CHARS, env_flag, is_loopback

__all__ = ["CHECKS", "PROFILE_SWITCH", "REFUSE", "WARN", "Finding", "boot_refusal", "findings", "profile_on"]

logger = logging.getLogger(__name__)

#: The switch that says this install is public.
PROFILE_SWITCH = "KAZMA_CLOUD_PROFILE"
#: What a missing piece does: Kazma does not start.
REFUSE = "refuse"
#: What a missing piece does: Kazma starts and says it.
WARN = "warn"


@dataclass(frozen=True)
class Finding:
    """One check's answer: *ok*, what was found (*detail*) and, when not ok,
    how to fix it (*fix*). *severity* is what a missing piece does."""

    key: str
    title: str
    severity: str
    ok: bool
    detail: str
    fix: str


@dataclass(frozen=True)
class _Check:
    key: str
    title: str
    severity: str
    #: Reads the settings store (runs in the builder, after it answers).
    settings: bool
    run: Callable[[], tuple[bool, str]]
    fix: str


def profile_on() -> bool:
    """Whether the operator declared this install public. In memory."""
    return env_flag(PROFILE_SWITCH)


# ── the checks ───────────────────────────────────────────────────────────


def _tls() -> tuple[bool, str]:
    from urllib.parse import urlparse

    url = (os.environ.get("KAZMA_PUBLIC_URL") or "").strip()
    if not url:
        return False, "KAZMA_PUBLIC_URL is not set: Kazma does not know the address people reach it at"
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        return False, f"KAZMA_PUBLIC_URL is {parsed.scheme or 'not'} an https address"
    if is_loopback(parsed.hostname):
        return False, "KAZMA_PUBLIC_URL names this machine, not a public name"
    return True, f"the public address is https://{parsed.hostname}"


def _proxies() -> list[str]:
    return [p.strip() for p in os.environ.get("KAZMA_TRUSTED_PROXIES", "").split(",") if p.strip()]


def _proxy() -> tuple[bool, str]:
    named = [p for p in _proxies() if p != "*"]
    if named:
        return True, f"the reverse proxy is declared ({', '.join(named)})"
    if _proxies():
        return False, "KAZMA_TRUSTED_PROXIES is '*', which Kazma never trusts: name the proxy's address"
    return False, "KAZMA_TRUSTED_PROXIES is not set: no reverse proxy (and its TLS) is declared"


def _bind_host() -> str:
    from kazma_core.security.boot_guard import _uvicorn_cli_host

    return (_uvicorn_cli_host(list(sys.argv)) or os.environ.get("KAZMA_HOST") or "127.0.0.1").strip()


def _bind() -> tuple[bool, str]:
    host = _bind_host()
    if is_loopback(host):
        return True, f"Kazma listens on {host}: only the proxy on this machine reaches it"
    return False, (f"Kazma listens on {host}: a visitor can reach it without the proxy and its TLS "
                   "(fine inside a container whose only published port is the proxy's)")


#: The switches that turn sign-in off, or let a visitor in without it.
_SIGN_IN_OFF = ("KAZMA_AUTH_DISABLED", "KAZMA_DEMO_MODE", "KAZMA_DEV_WS_BYPASS", "KAZMA_LOOPBACK_AUTOLOGIN")


def _sign_in() -> tuple[bool, str]:
    problems = [f"{name} is on" for name in _SIGN_IN_OFF if env_flag(name)]
    secret = (os.environ.get("KAZMA_SECRET") or "").strip()
    if secret and len(secret) < WEAK_SECRET_CHARS:
        problems.append(f"KAZMA_SECRET is shorter than {WEAK_SECRET_CHARS} characters")
    if problems:
        return False, "; ".join(problems)
    return True, "sign-in is on (no switch turns it off; the secret is not a short hand-set one)"


def _production() -> tuple[bool, str]:
    if env_flag("KAZMA_PRODUCTION"):
        return True, "KAZMA_PRODUCTION is on"
    return False, "KAZMA_PRODUCTION is off: the production rules (the vault's default rung closed, held lessons) do not apply"


def _code_in_containers() -> tuple[bool, str]:
    from kazma_core.safety.deployment_policy import CONTAINER_REQUIRED_KEY, container_required

    if container_required():
        return True, "code runs only in containers (Settings: the container profile is required)"
    return False, f"{CONTAINER_REQUIRED_KEY} is off: code the agent writes may run on this machine itself"


def _container_engine() -> tuple[bool, str]:
    from kazma_core.docker_cli import engine_label, find_docker_cli

    if find_docker_cli() is None:
        return False, "no docker CLI on this machine: code cannot run until a container engine is installed"
    return True, f"{engine_label()} is installed (readiness says whether it answers)"


def _backups() -> tuple[bool, str]:
    from kazma_core.backup.restic_repo import repo_paths
    from kazma_core.backup.universal import plain_copy_configured

    paths = repo_paths()
    where = [name for name in ("remote", "folder") if paths.get(name)]
    if plain_copy_configured():
        where.append("offsite")
    if where:
        return True, f"a backup copy leaves this machine ({', '.join(where)})"
    return False, "every backup stays on this machine: losing its disk loses them all"


#: Every check, in the order they are shown. A new piece is a row here.
CHECKS: tuple[_Check, ...] = (
    _Check("tls", "The public address is https", REFUSE, False, _tls,
           "Set KAZMA_PUBLIC_URL to the https address people open (https://kazma.example.org)."),
    _Check("proxy", "A reverse proxy is declared", REFUSE, False, _proxy,
           "Put Kazma behind a reverse proxy that serves TLS and name its address in KAZMA_TRUSTED_PROXIES."),
    _Check("sign_in", "Sign-in is on", REFUSE, False, _sign_in,
           "Unset KAZMA_AUTH_DISABLED, KAZMA_DEMO_MODE, KAZMA_DEV_WS_BYPASS and KAZMA_LOOPBACK_AUTOLOGIN; "
           "leave KAZMA_SECRET unset (Kazma generates one) or set one of 32 characters or more."),
    _Check("code_in_containers", "Code runs only in containers", REFUSE, True, _code_in_containers,
           "Settings -> System: require the container profile for code (security.execution.container_required)."),
    _Check("bind", "Kazma is reached only through the proxy", WARN, False, _bind,
           "Set KAZMA_HOST=127.0.0.1 so only the proxy on this machine reaches Kazma."),
    _Check("production", "The production label is on", WARN, False, _production,
           "Set KAZMA_PRODUCTION=1."),
    _Check("container_engine", "A container engine is installed", WARN, True, _container_engine,
           "Install one: kazma setup engine (Kazma's own) or Docker."),
    _Check("backups", "A backup copy leaves this machine", WARN, True, _backups,
           "Settings -> Backup: an offsite copy (restic or a provider) or a copy on another drive."),
)


def findings(*, settings: bool = True, only_settings: bool = False) -> list[Finding]:
    """Every check's answer (blocking: the settings checks read the store).
    *settings* False leaves out the checks that read settings; *only_settings*
    runs only those. A check that raises is a missing piece."""
    from kazma_core.safety.deployment_policy import policy_read_failures

    # What a check's reads can raise: the settings store and the identity
    # store away (storage, configuration), a tool that cannot be found.
    unreadable = (*policy_read_failures(), LookupError, TypeError)
    out: list[Finding] = []
    for check in CHECKS:
        if (check.settings and not settings) or (only_settings and not check.settings):
            continue
        try:
            ok, detail = check.run()
        except unreadable as exc:  # a check that cannot be made is never a pass
            logger.warning("[cloud-profile] %s could not be checked", check.title, exc_info=True)
            ok, detail = False, f"could not be checked ({type(exc).__name__})"
        out.append(Finding(check.key, check.title, check.severity, bool(ok), detail, "" if ok else check.fix))
    return out


def boot_refusal(*, settings: bool) -> str:
    """At boot, with the profile on: the refusal text when a piece declared
    ``refuse`` is missing, else ``""`` (each missing ``warn`` piece said at
    WARNING). *settings* False runs the environment's checks (``create_app``,
    before the database); True runs the settings' (the builder, once the
    store answers). With the profile off: ``""``, nothing read."""
    if not profile_on():
        return ""
    found = findings(settings=settings, only_settings=settings)
    for f in found:
        if not f.ok and f.severity == WARN:
            logger.warning("[cloud-profile] %s: no -- %s. %s", f.title, f.detail, f.fix)
    missing = [f for f in found if not f.ok and f.severity == REFUSE]
    if not missing:
        logger.info("[cloud-profile] the %s checks pass", "settings'" if settings else "environment's")
        return ""
    lines = [f"\n  [SECURITY] {PROFILE_SWITCH} is on and this install is not ready to be public -- "
             "refusing to start.\n"]
    for f in missing:
        lines.append(f"  * {f.title}: no -- {f.detail}.\n      Fix: {f.fix}\n")
    lines.append(f"\n  Every check: kazma doctor. A private install unsets {PROFILE_SWITCH}.\n")
    return "".join(lines)
