"""Owned, bounded Playwright stdio transport; no process-global monkeypatches.

This is the sole compatibility seam into Playwright's private Connection API.
Node and Chromium inherit one OS resource boundary. The HTTP broker remains
in the server, preserving process-wide origin, robots, routing and peer checks.
"""
from __future__ import annotations

import asyncio
import json
import os
import queue
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context

from kazma_core.web_acquire.result import AcquisitionError, FetchErrorCode

_MEMORY = 2 * 1024 ** 3
_FRAME = 32 * 1024 ** 2
_QUEUED = 32 * 1024 ** 2
_TRAFFIC = 512 * 1024 ** 2
_LIFETIME = 1800
_GRACE = 5


class _DriverLimitError(AcquisitionError):
    # Playwright recreates errors with type(error)(message). Keep this private
    # protocol exception compatible without weakening the public typed error.
    def __init__(self, message):
        super().__init__(FetchErrorCode.LIMIT, message)


def _observe_future(future):
    if not future.cancelled():
        future.exception()


def _start_driver():
    from playwright._impl._driver import compute_driver_executable

    from kazma_core.web_acquire.browser_isolation import network_command
    command = network_command([*compute_driver_executable(), 'run-driver'])
    # Do not hand browser processes the vault, provider keys, proxy passwords,
    # NODE_OPTIONS, debug sinks or unrelated install/environment credentials.
    allowed = {'systemroot', 'windir', 'temp', 'tmp', 'path', 'home', 'userprofile',
               'localappdata', 'appdata', 'playwright_browsers_path', 'display', 'wayland_display',
               'xdg_runtime_dir', 'lang', 'lc_all'}
    env = {key: value for key, value in os.environ.items() if key.lower() in allowed}
    env.update(PW_LANG_NAME='python', PW_LANG_NAME_VERSION=f'{sys.version_info.major}.{sys.version_info.minor}')
    if os.name == 'nt':
        from kazma_core.security.process_budget import _start_process, job_cpu_rate

        return _start_process(command, env=env, memory_bytes=_MEMORY, cpu_rate=job_cpu_rate(2))
    if sys.platform.startswith('linux'):
        from kazma_core.web_acquire.browser_cgroup import start_linux_driver

        return start_linux_driver(command, env=env, memory_bytes=_MEMORY, lifetime=_LIFETIME)
    raise AcquisitionError(FetchErrorCode.UNAVAILABLE,
        'Bounded local Chromium currently requires Windows Job Objects or Linux cgroup v2; launch was refused.')


class _Transport:
    def __init__(self, loop):
        self.loop = loop
        self.on_message = lambda message: None
        self.on_send = lambda message: None
        self.on_error_future = loop.create_future()
        self.on_error_future.add_done_callback(_observe_future)
        self.stopped = loop.create_future()
        self.proc = self.tree = self.launch = None
        self.failure = None
        self._stop = threading.Event()
        self._writer_wake = threading.Event()
        self._lock = threading.RLock()
        self._out = queue.Queue(maxsize=32)
        self._queued = self._traffic = 0
        self._threads = []
        self._reader = ThreadPoolExecutor(max_workers=1, thread_name_prefix='kazma-browser-pipe')
        self._closer = None
        self.active_task = None
        self.cleanup_deadline = None

    async def connect(self):
        self.launch = asyncio.create_task(asyncio.to_thread(_start_driver))
        self.proc, self.tree = await asyncio.shield(self.launch)
        with self._lock:
            if self._stop.is_set():
                self.request_stop()
                return
            for name, target in [('writer', self._write), ('stderr', self._stderr), ('watchdog', self._watchdog)]:
                thread = threading.Thread(target=target, name='kazma-browser-' + name, daemon=True)
                thread.start()
                self._threads.append(thread)

    def _fail(self, message):
        with self._lock:
            if self.failure is None:
                self.failure = AcquisitionError(FetchErrorCode.LIMIT, message)
        self.request_stop()

    def _watchdog(self):
        deadline = time.monotonic() + _LIFETIME
        while not self._stop.wait(0.05):
            if self.active_task is not None and self.active_task.cancelling():
                self.request_stop()
                return
            if time.monotonic() >= deadline:
                self._fail('Local browser owner exceeded its 30-minute hard lifetime; its process tree was terminated.')
                return
            if self.cleanup_deadline is not None and time.monotonic() >= self.cleanup_deadline:
                self._fail('Local browser cleanup exceeded its five-second grace period; its process tree was terminated.')
                return

    def _stderr(self):
        total = 0
        try:
            while not self._stop.is_set():
                chunk = self.proc.stderr.read1(4096)
                if not chunk:
                    return
                total += len(chunk)
                if total > 64 * 1024:
                    self._fail('Local browser stderr exceeded its 64KiB budget; its process tree was terminated.')
                    return
        except (OSError, ValueError):
            if not self._stop.is_set():
                self._fail('Local browser stderr pipe failed; its process tree was terminated.')

    def _write(self):
        try:
            while not self._stop.is_set():
                self._writer_wake.wait()
                self._writer_wake.clear()
                while not self._stop.is_set():
                    try:
                        data = self._out.get_nowait()
                    except queue.Empty:
                        break
                    self.proc.stdin.write(data)
                    self.proc.stdin.flush()
                    with self._lock:
                        self._queued -= len(data)
        except (OSError, ValueError):
            if not self._stop.is_set():
                self._fail('Local browser command pipe failed; its process tree was terminated.')

    def send(self, message):
        self.on_send(message)
        data = json.dumps(message, ensure_ascii=False).encode('utf-8')
        with self._lock:
            if self._stop.is_set():
                raise _DriverLimitError(str(self.failure) if self.failure else 'Local browser owner has ended.')
            if len(data) > _FRAME or self._queued + len(data) + 4 > _QUEUED or self._out.full():
                self._fail('Local browser command frame/queue budget exceeded; its process tree was terminated.')
                raise _DriverLimitError(str(self.failure))
            self._traffic += len(data) + 4
            if self._traffic > _TRAFFIC:
                self._fail('Local browser protocol traffic exceeded its 512MiB owner budget; its process tree was terminated.')
                raise _DriverLimitError(str(self.failure))
            payload = len(data).to_bytes(4, 'little') + data
            self._queued += len(payload)
            self._out.put_nowait(payload)
            self._writer_wake.set()

    def _read_frame(self):
        prefix = self.proc.stdout.read(4)
        if not prefix:
            return None
        if len(prefix) != 4:
            raise OSError('Incomplete browser protocol header')
        length = int.from_bytes(prefix, 'little')
        with self._lock:
            self._traffic += length + 4
            if not 0 < length <= _FRAME or self._traffic > _TRAFFIC:
                raise ValueError('Browser protocol byte budget exceeded')
        data = bytearray()
        while len(data) < length:
            chunk = self.proc.stdout.read(min(65536, length - len(data)))
            if not chunk:
                raise OSError('Incomplete browser protocol frame')
            data.extend(chunk)
        return data

    async def run(self):
        try:
            while not self._stop.is_set():
                # The dedicated single reader cannot occupy the shared DNS/DB
                # executor indefinitely; carry the call context explicitly.
                raw = await asyncio.wrap_future(self._reader.submit(copy_context().run, self._read_frame))
                if raw is None:
                    if not self._stop.is_set():
                        self._fail('Local browser driver exited or exhausted an OS resource budget.')
                    break
                message = json.loads(raw)
                if not isinstance(message, dict):
                    raise ValueError('Invalid browser protocol message')
                self.on_message(message)
        except (OSError, ValueError, RecursionError):
            if not self._stop.is_set():
                self._fail('Local browser driver protocol failed or exceeded a byte budget; its process tree was terminated.')
        finally:
            self.request_stop()
            await asyncio.shield(self.stopped)

    def request_stop(self):
        with self._lock:
            self._stop.set()
            self._writer_wake.set()
            if self.proc is not None and self._closer is None:
                self._closer = threading.Thread(target=self._terminate, name='kazma-browser-terminate', daemon=True)
                self._closer.start()

    def _terminate(self):
        error = None
        try:
            self.tree.close()
            self.proc.wait(timeout=5)
            deadline = time.monotonic() + 3
            for thread in self._threads:
                thread.join(max(0, deadline - time.monotonic()))
            if any(thread.is_alive() for thread in self._threads):
                raise TimeoutError('Browser pipe tasks did not end')
            self._reader.shutdown(wait=True, cancel_futures=True)
            for stream in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
                stream.close()
        except (OSError, ValueError, RuntimeError, TimeoutError, subprocess.TimeoutExpired):
            error = AcquisitionError(FetchErrorCode.LIMIT,
                'Local browser process cleanup could not be verified; capacity remains quarantined.')
        self.loop.call_soon_threadsafe(self._terminated, error)

    def _terminated(self, error):
        failure = error or self.failure
        if failure is not None and not self.on_error_future.done():
            self.on_error_future.set_exception(_DriverLimitError(str(failure)))
        if not self.stopped.done():
            if error is not None:
                self.stopped.set_exception(error)
            else:
                self.stopped.set_result(None)

    async def wait_until_stopped(self):
        await asyncio.shield(self.stopped)

    def dispose(self):
        self.request_stop()


class BrowserDriver:
    """Retain transport/connection ownership until the process tree is gone."""
    def __init__(self):
        self.transport = self.connection = self.task = None
        self.playwright = None

    async def start(self):
        from playwright._impl._connection import Connection
        from playwright._impl._object_factory import create_remote_object
        from playwright.async_api import Playwright

        loop = asyncio.get_running_loop()
        self.transport = _Transport(loop)
        self.connection = Connection(None, create_remote_object, self.transport, loop)
        self.transport.on_send = self._observe_command
        self.task = asyncio.create_task(self.connection.run())
        self.task.add_done_callback(self._done)
        future = self.connection.playwright_future
        future.add_done_callback(_observe_future)
        done, _ = await asyncio.wait({future, self.transport.on_error_future, self.task}, return_when=asyncio.FIRST_COMPLETED)
        if future not in done or self.transport.failure is not None:
            if self.task.done() and not self.task.cancelled() and self.task.exception() is not None:
                raise self.task.exception()
            raise self.transport.failure or AcquisitionError(FetchErrorCode.UNAVAILABLE, 'Bounded Playwright driver did not initialize.')
        self.playwright = Playwright(future.result())
        self.playwright.stop = self.stop
        return self.playwright

    def _observe_command(self, message):
        # Cancellation and forced termination can race a Playwright promise.
        # Observe its failure even if its API waiter was already cancelled;
        # reading the exception does not change what an active await receives.
        callback = self.connection._callbacks.get(message.get('id'))
        if callback is not None:
            callback.future.add_done_callback(_observe_future)

    def _done(self, task):
        if not task.cancelled() and task.exception() is not None:
            self.transport._fail('Local browser driver connection failed; its process tree was terminated.')
        if self.connection is not None:
            initial = self.connection._init_task
            if initial is not None:
                initial.add_done_callback(_observe_future)
            self.connection.cleanup(str(self.transport.failure) if self.transport.failure else None)

    async def stop(self):
        from kazma_core.playwright_loop import finish_owned

        transport = self.transport
        if transport is None:
            return
        if transport.launch is not None:
            # A partially initialized launch is still an owned OS process.
            if transport.launch.done() and transport.launch.exception() is not None:
                transport._reader.shutdown(wait=False, cancel_futures=True)
                return
            if transport.proc is None:
                transport.proc, transport.tree = await finish_owned(transport.launch)
        if transport.stopped.done() and transport.stopped.exception() is not None:
            # A failed OS verification retains the boundary and may be retried;
            # never forget its handles or release its admission lease early.
            transport.stopped = asyncio.get_running_loop().create_future()
            transport._closer = None
        transport.request_stop()
        if transport.proc is not None:
            await finish_owned(transport.wait_until_stopped())
        else:
            transport._reader.shutdown(wait=False, cancel_futures=True)
        if self.connection is not None:
            self.connection.cleanup()
            initial = self.connection._init_task
            if initial is not None:
                await asyncio.gather(initial, return_exceptions=True)
        if self.task is not None:
            await asyncio.gather(self.task, return_exceptions=True)
        self.playwright = None

    def watch(self, task):
        self.transport.active_task = task

    @property
    def ending(self):
        return self.transport is not None and self.transport._stop.is_set()

    def cleanup_window(self):
        self.transport.cleanup_deadline = time.monotonic() + _GRACE

    def cleanup_done(self):
        self.transport.cleanup_deadline = None

    def evidence(self):
        return {'memory_bytes': _MEMORY, 'lifetime_seconds': _LIFETIME,
                'frame_bytes': _FRAME, 'queued_bytes': _QUEUED, 'protocol_bytes': _TRAFFIC,
                'boundary': 'windows_job' if os.name == 'nt' else 'linux_cgroup_v2',
                'failed': bool(self.transport and self.transport.failure)}
