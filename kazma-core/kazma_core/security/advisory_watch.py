"""A dependency advisory reaches the owner the day it is published.

The functional audit of 2026-11 (roadmap theme 11, after FA-258): the boot
check (:func:`kazma_core.install_requirements.report_unmet_requirements`)
speaks only at a start, and only once a commit has raised a floor. An
advisory published against a package this install runs said nothing until
someone ran the security report by hand; the owner, using Kazma daily, never
saw a notice.

:func:`advisory_sweep` is a maintenance sweep (``worker_bootstrap``): once a
day it asks OSV about every installed version (the security report's own
:func:`~kazma_core.security.dependency_scanner.audit_installed`), and an
advisory it has not told before -- reviewed ones
(``REVIEWED_ADVISORIES``) never -- is told in ONE ops alert per check,
naming each package, its installed and fixed versions, and what fixes it:
the update when this build's floor already requires the fix, the package
upgrade when no floor reaches it yet. One alert per check, never one per
advisory: the first check on an install can find a dozen, and a channel
that sends a dozen messages at once is a channel the owner mutes (§33).

The advisories still open are kept (:data:`ADVISORIES_KEY`) so the daily
digest names them until they are gone (theme 12). OSV not answering raises
nothing and is asked again in an hour; a check that found nothing new is
quiet.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["ADVISORIES_KEY", "advisory_sweep", "open_advisories"]

#: What the last check found: when it ran, the advisories still open (for
#: the digest) and the ones already told (so each is told once).
ADVISORIES_KEY = "system.security.advisories"
#: A successful check is good for a day.
_CHECK_EVERY_S = 24 * 3600.0
#: OSV not answering is asked again after this long, not every pass.
_RETRY_AFTER_S = 3600.0
#: Bounds on what is kept: every open advisory a sane install could have.
_KEEP_OPEN = 50
_SUMMARY_CHARS = 160


def _key(package: str, vuln_id: str) -> str:
    return f"{package}:{vuln_id}"


def _read_state() -> dict[str, Any]:
    from kazma_core.config_store import get_config_store

    raw = get_config_store().get(ADVISORIES_KEY)
    return dict(raw) if isinstance(raw, dict) else {}


def _write_state(state: dict[str, Any]) -> None:
    from kazma_core.config_store import get_config_store

    get_config_store().set(ADVISORIES_KEY, state, category="security")


def open_advisories() -> list[dict[str, Any]]:
    """The advisories the last check found open (newest check), for the digest.
    Raises what the settings store raises: the digest says it could not read."""
    state = _read_state()
    found = state.get("open")
    return [dict(item) for item in found if isinstance(item, dict)] if isinstance(found, list) else []


def _fix_for(package: str, fixed: str | None, unmet: set[str], python: str, update: str) -> str:
    """What fixes one advisory, in the owner's words."""
    if fixed is None:
        return "no fixed release yet"
    if package in unmet:
        return f"fixed in {fixed}, which this build already requires. {update}"
    return (f'fixed in {fixed}, which no Kazma floor requires yet: upgrade it with Kazma '
            f'stopped, uv pip install --python {python} "{package}>={fixed}"')


def _audit() -> Any:
    from kazma_core.security.dependency_scanner import audit_installed

    return asyncio.run(audit_installed())


def advisory_sweep(*, now: float | None = None, audit: Callable[[], Any] | None = None) -> str:
    """The maintenance sweep's check. Returns what it did: ``not_due``,
    ``osv_unavailable``, ``quiet`` (nothing new) or ``told`` (an alert went out)."""
    from kazma_core.security.dependency_scanner import DependencyQueryError

    now = time.time() if now is None else now
    state = _read_state()
    if now - float(state.get("checked_at") or 0) < _CHECK_EVERY_S:
        return "not_due"
    if now - float(state.get("attempted_at") or 0) < _RETRY_AFTER_S:
        return "not_due"
    state["attempted_at"] = now
    try:
        report = (audit or _audit)()
    except DependencyQueryError as exc:
        _write_state(state)
        logger.info("[advisories] OSV could not be asked (%s); asked again in an hour", exc)
        return "osv_unavailable"

    from kazma_core.install_requirements import _install_python, _update_instructions, unmet_requirements
    from kazma_core.paths import installed_project_root

    root = installed_project_root()
    unmet = {u.name for u in unmet_requirements(root)}
    python = _install_python(root)
    update = _update_instructions(root)
    told = set(state.get("told") or ())
    open_now: list[dict[str, Any]] = []
    new_lines: list[str] = []
    for vuln in report.open_vulnerabilities:
        key = _key(vuln.package, vuln.vuln_id)
        item = {
            "package": vuln.package, "version": vuln.version, "id": vuln.vuln_id,
            "fixed": vuln.fixed_version, "summary": (vuln.summary or "")[:_SUMMARY_CHARS],
            "fix": _fix_for(vuln.package, vuln.fixed_version, unmet, python, update),
        }
        if key not in {_key(o["package"], o["id"]) for o in open_now}:
            open_now.append(item)
        if key not in told:
            new_lines.append(f"{vuln.package} {vuln.version}: {vuln.vuln_id} -- {item['fix']}")
    state.update({
        "checked_at": now,
        "open": open_now[:_KEEP_OPEN],
        # Told = what is open now: an advisory that goes away and comes back
        # (a downgrade) is told again.
        "told": sorted({_key(o["package"], o["id"]) for o in open_now}),
    })
    if not new_lines:
        _write_state(state)
        return "quiet"
    from kazma_core.observability.ops_alerts import alert  # never raises

    detail = (f"{len(new_lines)} new advisory(ies) against the installed packages: "
              + "; ".join(new_lines[:_KEEP_OPEN]))
    alert("security.advisories_new", "A dependency advisory affects this install", detail,
          severity="warn", cooldown_s=0.0, max_detail=3500)
    logger.warning("[advisories] %s", detail)
    # Told only once the alert was handed on: a crash before it tells again.
    _write_state(state)
    return "told"
