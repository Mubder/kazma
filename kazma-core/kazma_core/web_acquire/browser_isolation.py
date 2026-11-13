"""Strict browser network boundary: unsupported hosts refuse before Node starts."""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Imports stay inside the functions: this file also runs as the launcher
# inside the new namespace (``__main__`` below), before anything is verified.

_NO_ISOLATION = ('Strict local browser rendering requires OS network isolation. This host has no '
                 'qualified implementation; launch refused. HTTP extraction remains available. '
                 'KAZMA_BROWSER_NETWORK_MODE=compatibility is an explicit weaker operator mode.')
_NO_UNSHARE = 'Strict local browser rendering requires Linux unshare and delegated cgroup v2; launch refused.'
_NO_BOUNDARY = ('Bounded local Chromium currently requires Windows Job Objects or Linux cgroup v2; '
                'launch was refused.')


def _browser_roots() -> list[Path]:
    """Where Playwright keeps its browsers: ``PLAYWRIGHT_BROWSERS_PATH``, else
    the platform's cache folder."""
    configured = (os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or "").strip()
    if configured and configured != "0":
        return [Path(configured)]
    if os.name == "nt":
        return [Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "ms-playwright"]
    if sys.platform == "darwin":
        return [Path.home() / "Library" / "Caches" / "ms-playwright"]
    return [Path.home() / ".cache" / "ms-playwright"]


def _chromium_revision() -> str | None:
    """The Chromium build the installed Playwright launches: its revision in
    Playwright's own ``driver/package/browsers.json``, or None when Playwright
    is not installed or the file cannot be read."""
    import importlib.util
    import json

    try:
        spec = importlib.util.find_spec("playwright")
    except (ImportError, ValueError):
        return None
    for base in (spec.submodule_search_locations or ()) if spec is not None else ():
        try:
            data = json.loads((Path(base) / "driver" / "package" / "browsers.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for browser in data.get("browsers") or ():
            if browser.get("name") == "chromium" and browser.get("revision"):
                return str(browser["revision"])
    return None


def chromium_was_installed() -> bool:
    """Some build of Playwright's Chromium is on this machine (the browser
    part was set up), whichever Playwright it was for."""
    return any(any(root.glob("chromium-*")) for root in _browser_roots() if root.is_dir())


def chromium_installed() -> bool:
    """The Chromium build the installed Playwright launches is on this
    machine: ``chromium-<revision>`` and ``chromium_headless_shell-<revision>``
    (what ``playwright install chromium`` puts there) for the revision in
    Playwright's ``browsers.json``. The install's browser part, the daily
    canary and ``kazma update`` ask this one rule.

    Any ``chromium-*`` folder used to count: after a Playwright update the old
    build's folder said "installed" while every launch failed for want of the
    new one, and ``kazma update`` downloaded no Chromium (2026-11-13). With no
    revision to read (no Playwright), any build counts, as before.
    """
    revision = _chromium_revision()
    if revision is None:
        return chromium_was_installed()
    return any((root / f"chromium-{revision}").is_dir() and (root / f"chromium_headless_shell-{revision}").is_dir()
               for root in _browser_roots())


def browser_unavailable():
    """Why Kazma cannot render a page in its browser here, or None: the
    browser part is not installed (Playwright, its Chromium) or the host
    refuses to start it (:func:`launch_refusal`). Reads the disk and the
    environment: call it off the loop."""
    import importlib.util

    if importlib.util.find_spec("playwright") is None or not chromium_installed():
        return "Kazma's browser is not installed (kazma setup install --with browser)"
    return launch_refusal()


def launch_refusal():
    """Why this host would refuse to start the browser, or None.

    The same decision ``network_command`` and the driver start make, asked
    without starting anything, so a browser tool can refuse before its
    approval card and before it acts (live 2026-10-18: an approved
    ``browser_navigate`` on Windows ended the turn as "effects unknown").
    Reads the environment and PATH: call it off the loop.
    """
    from kazma_core.web_acquire.policy import AcquisitionPolicyError, acquisition_policy
    try:
        mode = acquisition_policy().browser_network
    except AcquisitionPolicyError as exc:
        return str(exc)
    if not (os.name == 'nt' or sys.platform.startswith('linux')):
        return _NO_BOUNDARY
    if mode == 'compatibility':
        return None
    if not sys.platform.startswith('linux'):
        return _NO_ISOLATION
    import shutil
    if shutil.which('unshare') is None:
        return _NO_UNSHARE
    return None


def network_command(command):
    from kazma_core.web_acquire.policy import acquisition_policy
    from kazma_core.web_acquire.result import BrowserLaunchRefused
    mode = acquisition_policy().browser_network
    if mode == 'compatibility':
        import logging
        logging.getLogger(__name__).warning('Browser compatibility mode: CDP/deny-proxy defenses are active; OS network isolation is NOT established.')
        return command
    if not sys.platform.startswith('linux'):
        raise BrowserLaunchRefused(_NO_ISOLATION)
    import shutil
    unshare = shutil.which('unshare')
    if unshare is None:
        raise BrowserLaunchRefused(_NO_UNSHARE)
    namespace = os.readlink('/proc/self/ns/net')
    return [unshare, '--user', '--map-current-user', '--net', '--', sys.executable, '-I', str(Path(__file__).resolve()), namespace, *command]


def capability():
    from kazma_core.web_acquire.policy import acquisition_policy
    mode = acquisition_policy().browser_network
    return dict(mode=mode, os_network_isolation_required=mode == 'required',
        qualification='Linux network namespace and cgroup v2 must both succeed at launch' if sys.platform.startswith('linux') else 'strict rendering unavailable on this platform',
        compatibility_warning='Compatibility mode does not provide OS network isolation.')


if __name__ == '__main__':
    # Runs AFTER unshare, inside the existing OS memory/process boundary.
    # No sockets or driver code exist until a different, empty namespace is verified.
    previous, command = sys.argv[1], sys.argv[2:]
    import socket
    # sysfs can retain the host mount's namespace; query the kernel directly.
    if not command or os.readlink('/proc/self/ns/net') == previous or {name for _, name in socket.if_nameindex()} != {'lo'}:
        raise SystemExit(125)
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(('198.51.100.1', 9))
    except OSError:
        pass
    else:
        raise SystemExit(125)
    finally:
        probe.close()
    os.execv(command[0], command)
