"""Owned bounded process calls, with quarantine rather than false cleanup proof."""
from __future__ import annotations

import asyncio
import logging
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar, copy_context

from kazma_core.playwright_loop import finish_owned
from kazma_core.web_acquire.result import AcquisitionError, FetchErrorCode

logger = logging.getLogger(__name__)
current_process_owner = ContextVar('acquisition_process_owner', default=None)


class ProcessOwner:
    def __init__(self, name):
        self.cancelled = threading.Event()
        self.process = self.tree = self.thread = None
        self.threads = ()
        self.children = []
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f'kazma-{name}-worker')
        self.thread_closed = False

    def run(self, function):
        self.thread = threading.current_thread()
        if self.cancelled.is_set():
            raise InterruptedError('Acquisition worker cancelled')
        token = current_process_owner.set(self)
        try:
            return function(self)
        finally:
            current_process_owner.reset(token)

    async def finish(self):
        self.executor.shutdown(wait=False, cancel_futures=True)
        deadline = time.monotonic() + 2
        while self.thread is not None and self.thread.is_alive():
            if time.monotonic() >= deadline:
                return
            await asyncio.sleep(.001)
        self.thread_closed = True

    def attach(self, process, tree, threads):
        self.process, self.tree, self.threads = process, tree, threads
        self.children.append((process, tree, threads))

    def proven(self):
        return self.thread_closed and all(self._child_proven(*child) for child in self.children)

    @staticmethod
    def _child_proven(process, tree, threads):
        return (tree.closed and process.returncode is not None
            and not any(thread.is_alive() for thread in threads)
            and all(stream.closed for stream in (process.stdin, process.stdout, process.stderr)))

    def before_dispatch(self):
        if self.cancelled.is_set():
            raise InterruptedError('Acquisition cancelled before child dispatch')
        if not all(self._child_proven(*child) for child in self.children):
            raise AcquisitionError(FetchErrorCode.LIMIT, 'Previous worker cleanup is unverified; further dispatch refused.')

    def retry_cleanup(self):
        self.executor.shutdown(wait=False, cancel_futures=True)
        if self.thread is not None:
            self.thread.join(timeout=2)
            if self.thread.is_alive():
                return False
        self.thread_closed = True
        for process, tree, threads in self.children:
            tree.close()
            process.wait(timeout=5)
            deadline = time.monotonic() + 2
            for thread in threads:
                if thread.ident is not None:
                    thread.join(max(0, deadline - time.monotonic()))
            if any(thread.is_alive() for thread in threads):
                return False
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()
        return self.proven()


class OwnedWorkers:
    """One bounded admission pool and retained cleanup registry per worker kind."""
    def __init__(self, name, capacity):
        self.name, self.capacity = name, capacity
        self.quarantined = {}
        self.lock, self.reaper = threading.Lock(), threading.Lock()
        self.cleanup_threads = set()

    def _reap(self):
        try:
            with self.lock:
                pending = tuple(self.quarantined.items())
            for token, (lease, owner) in pending:
                try:
                    if not owner.retry_cleanup():
                        continue
                except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired):
                    continue
                with self.lock:
                    self.quarantined.pop(token, None)
                lease.release()
        finally:
            self.reaper.release()

    def _request_reap(self):
        self.drain(timeout=0)
        with self.lock:
            pending = bool(self.quarantined)
        if pending and self.reaper.acquire(blocking=False):
            thread = threading.Thread(target=self._reap, name=f'kazma-{self.name}-cleanup', daemon=True)
            try:
                with self.lock:
                    self.cleanup_threads.add(thread)
                    thread.start()
            except RuntimeError:
                with self.lock:
                    self.cleanup_threads.discard(thread)
                self.reaper.release()
                logger.error('%s cleanup thread could not start; ownership retained', self.name)

    def drain(self, timeout=2):
        deadline = time.monotonic() + max(0, timeout)
        with self.lock:
            threads = tuple(self.cleanup_threads)
        for thread in threads:
            if thread.ident is not None and thread is not threading.current_thread():
                thread.join(max(0, deadline - time.monotonic()))
        with self.lock:
            self.cleanup_threads.difference_update(thread for thread in threads
                if thread.ident is not None and not thread.is_alive())
            return not self.cleanup_threads

    async def call(self, function, *, owner_factory=ProcessOwner):
        self._request_reap()
        lease = await self.capacity().acquire()
        try:
            owner = owner_factory(self.name)
        except (OSError, RuntimeError):
            lease.release()
            raise
        interrupted = False
        try:
            work = asyncio.wrap_future(owner.executor.submit(copy_context().run, owner.run, function))
            owned = asyncio.gather(work, return_exceptions=True)
            try:
                outcome, = await asyncio.shield(owned)
            except asyncio.CancelledError:
                interrupted = True
                owner.cancelled.set()
                await finish_owned(owned)
                raise
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome
        finally:
            try:
                await finish_owned(owner.finish())
            except asyncio.CancelledError:
                interrupted = True
                raise
            finally:
                if owner.proven():
                    lease.release()
                else:
                    lease.quarantine()
                    with self.lock:
                        self.quarantined[lease.token] = (lease, owner)
                    logger.error('%s cleanup could not be verified; capacity quarantined and handles retained', self.name)
                    if not interrupted:
                        raise AcquisitionError(FetchErrorCode.LIMIT,
                            f'{self.name} cleanup could not be verified; capacity remains quarantined. '
                            'Later requests retry cleanup; check readiness.')
