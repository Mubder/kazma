"""Strict browser network boundary: unsupported hosts refuse before Node starts."""
from __future__ import annotations

import os
import sys
from pathlib import Path


def network_command(command):
    from kazma_core.web_acquire.policy import acquisition_policy
    from kazma_core.web_acquire.result import AcquisitionError, FetchErrorCode
    mode = acquisition_policy().browser_network
    if mode == 'compatibility':
        import logging
        logging.getLogger(__name__).warning('Browser compatibility mode: CDP/deny-proxy defenses are active; OS network isolation is NOT established.')
        return command
    if not sys.platform.startswith('linux'):
        raise AcquisitionError(FetchErrorCode.UNAVAILABLE, 'Strict local browser rendering requires OS network isolation. This host has no qualified implementation; launch refused. HTTP extraction remains available. KAZMA_BROWSER_NETWORK_MODE=compatibility is an explicit weaker operator mode.')
    import shutil
    unshare = shutil.which('unshare')
    if unshare is None:
        raise AcquisitionError(FetchErrorCode.UNAVAILABLE, 'Strict local browser rendering requires Linux unshare and delegated cgroup v2; launch refused.')
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
