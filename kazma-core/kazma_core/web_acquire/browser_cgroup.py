"""Linux aggregate browser budgets in a delegated cgroup v2, before exec.

No controller setup or privileges are changed on the host. A missing writable
delegation refuses rendering. RLIMIT_AS is unsuitable for V8's virtual address
reservations and does not bound a multiprocess browser's aggregate memory.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

from kazma_core.web_acquire.result import AcquisitionError, FetchErrorCode

_GATE = """import os,select,subprocess,sys
parent_fd,kill_fd,seconds=int(sys.argv[1]),int(sys.argv[2]),float(sys.argv[3])
os.read(0,1) == b'1' or sys.exit(125)
child=subprocess.Popen(sys.argv[4:],stdin=0,stdout=1,stderr=2,close_fds=True)
child_fd=os.pidfd_open(child.pid)
poll=select.poll()
poll.register(parent_fd,select.POLLIN)
poll.register(child_fd,select.POLLIN)
poll.poll(max(1,int(seconds*1000)))
# Parent death, driver death or deadline ends ALL tasks, including this guard.
os.write(kill_fd,b'1')
sys.exit(125)
"""


def _is_cgroup2(directory):
    import ctypes

    buffer = ctypes.create_string_buffer(256)
    libc = ctypes.CDLL(None, use_errno=True)
    libc.fstatfs.argtypes = [ctypes.c_int, ctypes.c_void_p]
    libc.fstatfs.restype = ctypes.c_int
    if libc.fstatfs(directory, buffer) != 0:
        raise OSError('Cannot inspect browser cgroup filesystem')
    return ctypes.c_long.from_buffer(buffer).value == 0x63677270


class _Cgroup:
    def __init__(self, root: Path, memory_bytes: int):
        self.path = root / ('kazma-browser-' + uuid.uuid4().hex)
        self.directory = None
        self.closed = False
        # Check the actual filesystem before creating anything; ordinary
        # writable files cannot stand in for kernel-enforced controls.
        parent = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            if not _is_cgroup2(parent):
                raise OSError('Browser budgets require a cgroup v2 filesystem')
        finally:
            os.close(parent)
        self.path.mkdir()
        ready = False
        try:
            self.directory = os.open(self.path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW)
            for name, value in [('memory.max', str(memory_bytes)), ('memory.swap.max', '0'),
                                ('memory.oom.group', '1'), ('pids.max', '256'), ('cpu.max', '200000 100000')]:
                self._set(name, value)
                if self._read(name).split() != value.split():
                    raise OSError('Browser cgroup resource limit did not hold')
            # Require group-wide kill even when no process has been attached.
            kill = os.open('cgroup.kill', os.O_WRONLY | os.O_CLOEXEC | os.O_NOFOLLOW, dir_fd=self.directory)
            os.close(kill)
            ready = True
        finally:
            if not ready:
                if self.directory is not None:
                    os.close(self.directory)
                self.directory = None
                self.path.rmdir()

    def _set(self, name, value):
        fd = os.open(name, os.O_WRONLY | os.O_CLOEXEC | os.O_NOFOLLOW, dir_fd=self.directory)
        try:
            if os.write(fd, value.encode()) != len(value):
                raise OSError('Incomplete browser cgroup control write')
        finally:
            os.close(fd)

    def _read(self, name):
        fd = os.open(name, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW, dir_fd=self.directory)
        try:
            return os.read(fd, 4096).decode('ascii').strip()
        finally:
            os.close(fd)

    def close(self):
        if self.closed:
            return
        self._set('cgroup.kill', '1')
        deadline = time.monotonic() + 5
        while True:
            events = self._read('cgroup.events').splitlines()
            if 'populated 0' in events:
                break
            if 'populated 1' not in events:
                raise OSError('Cannot verify that the browser cgroup is empty')
            if time.monotonic() >= deadline:
                raise TimeoutError('Browser cgroup still contains live tasks after five seconds')
            time.sleep(0.01)
        self.path.rmdir()
        os.close(self.directory)
        self.directory = None
        self.closed = True


def _delegation():
    configured = os.environ.get('KAZMA_BROWSER_CGROUP_ROOT')
    if configured:
        root = Path(configured)
        if not root.is_absolute():
            raise OSError('Browser cgroup delegation must be an absolute path')
        return root.resolve(strict=True)
    current = Path('/proc/self/cgroup').read_text(encoding='ascii')
    own = next((line[3:] for line in current.splitlines() if line.startswith('0::')), None)
    if own is None or not own.startswith('/') or '..' in Path(own).parts:
        raise OSError('Cannot determine the current cgroup v2 delegation')
    return Path('/sys/fs/cgroup').joinpath(own.lstrip('/')).resolve(strict=True)


def start_linux_driver(command, *, env, memory_bytes, lifetime=1800):
    """The trusted launcher cannot exec Node until its PID is contained."""
    group = None
    proc = None
    ready = False
    descriptors = []
    try:
        if not hasattr(os, 'pidfd_open'):
            raise OSError('Browser parent-death guard requires Linux pidfd support')
        group = _Cgroup(_delegation(), memory_bytes)
        parent_fd = os.pidfd_open(os.getpid())
        descriptors.append(parent_fd)
        kill_fd = os.open('cgroup.kill', os.O_WRONLY | os.O_CLOEXEC | os.O_NOFOLLOW, dir_fd=group.directory)
        descriptors.append(kill_fd)
        proc = subprocess.Popen([sys.executable, '-I', '-c', _GATE, str(parent_fd), str(kill_fd), str(lifetime), *command],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=env, start_new_session=True, pass_fds=tuple(descriptors))
        group._set('cgroup.procs', str(proc.pid))
        if str(proc.pid) not in group._read('cgroup.procs').splitlines():
            raise OSError('Browser driver was not attached to the resource cgroup')
        proc.stdin.write(b'1')
        proc.stdin.flush()
        ready = True
        return proc, group
    except OSError:
        raise AcquisitionError(FetchErrorCode.UNAVAILABLE,
            'Local Chromium requires a writable delegated cgroup v2 with memory, pids, CPU and group-kill controls. '
            'Configure KAZMA_BROWSER_CGROUP_ROOT; unrestricted browser launch was refused.') from None
    finally:
        for descriptor in descriptors:
            os.close(descriptor)
        if not ready:
            if proc is not None:
                proc.kill()
                proc.wait(timeout=5)
                for stream in (proc.stdin, proc.stdout, proc.stderr):
                    stream.close()
            if group is not None:
                group.close()
