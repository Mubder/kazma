"""Acquisition DNS runs in an owned, bounded, credential-free child.

Synchronous validate_url keeps its API and all-address SSRF checks. This module
activates its resolver boundary only for acquisition calls, retains cancellation
ownership and quarantines uncertain cleanup. This file is also the standalone
isolated child entry point; imports at module load are standard library only.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

_TIMEOUT = 5
_MEMORY = 256 * 1024**2
_OUTPUT = 16 * 1024
if __name__ != '__main__':
    from kazma_core.web_acquire.process_worker import OwnedWorkers, ProcessOwner
    from kazma_core.web_acquire import resources

    _OWNERS = OwnedWorkers('DNS', lambda: resources.dns_capacity())
    _QUARANTINED = _OWNERS.quarantined
else:
    ProcessOwner = object  # Standalone entry needs no application imports.


class _DnsBudget(ProcessOwner):
    def resolve(self, host):
        from kazma_core.security.process_budget import OutputLimitExceeded, run_bounded
        from kazma_core.web_acquire.result import AcquisitionError, FetchErrorCode

        if self.cancelled.is_set():
            raise InterruptedError('DNS validation cancelled')
        if len(host.rstrip('.').encode('idna')) > 253:
            raise AcquisitionError(FetchErrorCode.INVALID_URL, 'DNS hostname exceeds its 253-byte limit.')
        allowed = {'systemroot', 'windir', 'temp', 'tmp', 'path'}
        env = {key: value for key, value in os.environ.items() if key.lower() in allowed}
        try:
            result = run_bounded([sys.executable, '-I', os.path.abspath(__file__)], env=env,
                input=json.dumps({'host': host, 'parent_pid': os.getpid(), 'lifetime': min(_TIMEOUT, 5)}),
                timeout=_TIMEOUT, memory_bytes=_MEMORY, output_bytes=_OUTPUT,
                cancel_event=self.cancelled, on_started=self.attach, text=True)
        except subprocess.TimeoutExpired:
            raise AcquisitionError(FetchErrorCode.TIMEOUT,
                'DNS validation exceeded its five-second worker deadline; its process was terminated.', transient=True) from None
        except OutputLimitExceeded:
            raise AcquisitionError(FetchErrorCode.LIMIT, 'DNS worker output exceeded its 16KiB limit.') from None
        except OSError:
            raise AcquisitionError(FetchErrorCode.UNAVAILABLE, 'Bounded DNS worker could not run; acquisition was refused.') from None
        return _decode(result)


def _decode(result):
    import ipaddress
    from kazma_core.web_acquire.result import AcquisitionError, FetchErrorCode

    if os.name == 'posix':
        import signal
        if result.returncode == -signal.SIGALRM:
            raise AcquisitionError(FetchErrorCode.TIMEOUT, 'DNS worker reached its kernel wall-clock deadline.', transient=True)
    try:
        data = json.loads(result.stdout)
        if result.returncode != 0 or not isinstance(data, dict):
            raise ValueError('Invalid receipt')
        if data.get('status') == 'limit':
            raise AcquisitionError(FetchErrorCode.LIMIT, 'DNS answer exceeded its address budget; an incomplete answer was refused.')
        ips = data.get('ips')
        if set(data) != {'status', 'ips'} or data['status'] != 'ok' or not isinstance(ips, list) or len(ips) > 128:
            raise ValueError('Invalid receipt')
        for address in ips:
            if not isinstance(address, str) or len(address) > 64:
                raise ValueError('Invalid address')
            ipaddress.ip_address(address)
    except (ValueError, TypeError):
        raise AcquisitionError(FetchErrorCode.UNAVAILABLE, 'DNS worker returned no complete, valid answer; acquisition was refused.') from None
    return ips


def _drain_cleanup(timeout=2):
    return _OWNERS.drain(timeout)


async def validate_public_url(url, *, validator=None):
    """Validate every target address using an owned bounded resolver child."""
    from kazma_core.security import ssrf
    from kazma_core.web_acquire.result import AcquisitionError, FetchErrorCode

    if not isinstance(url, str) or len(url) > 8192:
        raise AcquisitionError(FetchErrorCode.INVALID_URL, 'Acquisition URL must be text of at most 8192 characters.')
    def checked(owner):
        token = ssrf._dns_budget.set(owner)
        try:
            return (validator or ssrf.validate_url)(url, block_unresolved=True)
        finally:
            ssrf._dns_budget.reset(token)
    return await _OWNERS.call(checked, owner_factory=_DnsBudget)


async def resolve_endpoint(host, *, trusted_proxy=False):
    """Own transport DNS too; only an operator's proxy may be private.

This does not authorize a content target. The caller connects this literal IP
while httpcore keeps the logical hostname for CONNECT, HTTP authority and TLS.
"""
    import ipaddress
    from kazma_core.security import ssrf
    from kazma_core.web_acquire.result import AcquisitionError, FetchErrorCode

    if not isinstance(host, str) or not host or len(host) > 254:
        raise AcquisitionError(FetchErrorCode.ROUTING, 'Invalid transport endpoint hostname.')
    def checked(owner):
        try:
            addresses = [str(ipaddress.ip_address(host))]
        except ValueError:
            addresses = owner.resolve(host)
        if not addresses:
            raise AcquisitionError(FetchErrorCode.CONNECT, 'Transport endpoint DNS returned no addresses.', transient=True)
        if not trusted_proxy and any(ssrf._is_blocked_ip(ipaddress.ip_address(address)) for address in addresses):
            raise AcquisitionError(FetchErrorCode.SECURITY, 'Hosted service endpoint resolved to a private address; dispatch refused.')
        return addresses[0]
    return await _OWNERS.call(checked, owner_factory=_DnsBudget)


def _arm_deadline(parent_pid, lifetime):
    import runpy
    from pathlib import Path

    guard = Path(__file__).resolve().parents[1] / 'security' / 'worker_lifetime.py'
    runpy.run_path(str(guard), run_name='kazma_core.security.worker_lifetime')['arm_deadline'](parent_pid, lifetime)


def _worker():
    import socket

    request = json.loads(sys.stdin.read(4097))
    host, parent_pid, lifetime = request['host'], request['parent_pid'], request['lifetime']
    if (not isinstance(host, str) or not host or len(host) > 254 or len(host.rstrip('.').encode('idna')) > 253
            or type(parent_pid) is not int or parent_pid <= 0 or type(lifetime) not in (int, float) or not 0 < lifetime <= 5):
        raise ValueError('Invalid DNS worker request')
    _arm_deadline(parent_pid, lifetime)
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        infos = []
    if len(infos) > 2048:
        print('{"status":"limit"}')
        return
    ips = list(dict.fromkeys(info[4][0] for info in infos))
    if len(ips) > 128:
        print('{"status":"limit"}')
        return
    print(json.dumps({'status': 'ok', 'ips': ips}, ensure_ascii=True))


if __name__ == '__main__':
    try:
        _worker()
    except (OSError, ValueError, TypeError, KeyError):
        print('{"status":"unavailable"}')
    except MemoryError:
        print('{"status":"limit"}')
