"""Narrow broker: the only process that talks to Docker for document jobs.

Kazma's container profile (``KAZMA_DOCUMENT_WORKER_ISOLATION=container``)
never holds the Docker engine socket -- the engine API is host-root
authority. This broker does, and it accepts exactly one kind of work: run
one of the four fixed workers (:data:`WORKERS`) in a fresh container of ONE
image, resolved to its immutable id at start, with ONE container policy
(:func:`_container_argv`): no network, no mounts, read-only root, a
size-bounded tmpfs, memory/swap/CPU/PID/file limits, every capability
dropped, ``no-new-privileges``, a non-root user and Docker's default seccomp
profile. A client cannot name an image, command, environment variable,
mount or resource limit; it may only shorten the deadline.

Before serving, the broker attests the policy from inside a real job
container (``worker_entry probe``): identity, capabilities, seccomp,
interfaces, a refused outside connection, mounts, cgroup limits. Any
mismatch stops it -- it never serves with a policy it could not see hold.

Each job: authenticate (shared token, constant-time), admit (bounded
concurrency), start the container, stream the input bundle to its stdin,
forward its stdout as data frames (capped), then a status frame. A deadline
watchdog, the output cap and a client that disconnects all kill the
container. Every container is inspected (OOM kill) and removed, and the
removal is verified; one that cannot be removed keeps its slot out of
service until a later sweep removes it. At start the broker removes every
container a previous run of the same broker name left.

Standard library only, so the broker image needs nothing of Kazma.

Wire protocol (client -> broker): ``REQUEST_MAGIC``, 4-byte big-endian
length, JSON ``{protocol, token, op, worker, run_id, timeout_seconds,
input_size}``. ``op`` is ``run`` or ``status``. The broker answers frames of
one type byte and a 4-byte length: ``A`` (admitted: send exactly
``input_size`` bytes now), ``D`` (output data), ``S`` (final JSON status).
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import queue
import re
import select
import signal
import socket
import socketserver
import struct
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

__all__ = [
    "BrokerLimits",
    "BrokerSettings",
    "main",
]

logger = logging.getLogger("kazma.document_broker")

PROTOCOL = 1
REQUEST_MAGIC = b"KZDB-REQ/1\n"
FRAME_ADMIT, FRAME_DATA, FRAME_STATUS = b"A", b"D", b"S"
WORKERS = ("parser", "mutation", "renderer", "probe")
LABEL = "io.kazma.document-worker"
RUN_LABEL = "io.kazma.document-worker.run"
WORKER_USER = "10001:10001"
MIN_TOKEN_CHARS = 32
MAX_REQUEST_HEADER = 16 * 1024
MAX_FRAME = 1024 * 1024
ENTRY_MODULE = "kazma_core.documents.worker_entry"
# Mirrors kazma_core.documents.worker_bundle (kept equal by a test): the
# broker builds its own attestation request without importing Kazma.
_INPUT_MAGIC = b"KZDW-IN/1\n"
_OUTPUT_MAGIC = b"KZDW-OUT/1\n"
_RUN_ID = re.compile(r"[0-9a-f]{32}\Z")
_NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,30}\Z")
_ALLOWED_ENV = (
    "PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "HOME", "USERPROFILE",
    "APPDATA", "LOCALAPPDATA", "PROGRAMDATA", "PROGRAMFILES",
    "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG", "DOCKER_CERT_PATH",
    "DOCKER_TLS_VERIFY",
)
_VIRTUAL_MOUNT_ROOTS = ("/proc", "/dev", "/sys")
#: How long a stopped job's container may take to end before its CLI is.
_STOP_GRACE_SECONDS = 45.0
# Files Docker itself binds into every container (--init's binary moved
# from /sbin to /usr/sbin across engine versions).
_DOCKER_FILE_MOUNTS = frozenset({
    "/etc/resolv.conf", "/etc/hostname", "/etc/hosts", "/sbin/docker-init", "/usr/sbin/docker-init",
})


@dataclass(frozen=True, slots=True)
class BrokerLimits:
    """Fixed per-container limits. Clients cannot raise any of them."""

    memory_mb: int = 2048
    cpus: float = 1.0
    pids: int = 256
    tmpfs_mb: int = 768
    nofile: int = 1024
    max_timeout_seconds: float = 600.0
    max_input_bytes: int = 640 * 1024 * 1024
    max_output_bytes: int = 320 * 1024 * 1024
    max_stderr_bytes: int = 64 * 1024
    concurrency: int = 2
    admission_wait_seconds: float = 30.0
    header_timeout_seconds: float = 10.0

    def validate(self) -> None:
        for name in ("memory_mb", "pids", "tmpfs_mb", "nofile", "max_input_bytes",
                     "max_output_bytes", "max_stderr_bytes", "concurrency"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if not 0.01 <= float(self.cpus) <= 64:
            raise ValueError("cpus must be between 0.01 and 64")
        if self.tmpfs_mb >= self.memory_mb:
            # tmpfs pages are charged to the container's memory cgroup.
            raise ValueError("tmpfs_mb must be below memory_mb")
        for name in ("max_timeout_seconds", "admission_wait_seconds", "header_timeout_seconds"):
            if not 0 < float(getattr(self, name)) <= 86_400:
                raise ValueError(f"{name} must be positive")

    @property
    def cpu_quota(self) -> int:
        return int(round(float(self.cpus) * 100_000))


@dataclass(frozen=True, slots=True)
class BrokerSettings:
    docker: str
    image: str
    token: str
    name: str = "default"
    limits: BrokerLimits = BrokerLimits()
    qualification: bool = False

    def validate(self) -> None:
        if not _NAME.match(self.name):
            raise ValueError("broker name must be 1-31 lowercase letters, digits or '-'")
        if len(self.token) < MIN_TOKEN_CHARS:
            raise ValueError(f"broker token must be at least {MIN_TOKEN_CHARS} characters")
        if not self.image or self.image.startswith("-"):
            raise ValueError("worker image is required")
        self.limits.validate()


def container_name(broker_name: str, run_id: str) -> str:
    return f"kazma-docjob-{broker_name}-{run_id}"


def _container_argv(settings: BrokerSettings, image_id: str, run_id: str, worker: str) -> list[str]:
    """The one container policy every document job runs under."""

    if worker not in WORKERS:
        raise ValueError("unknown worker")
    if not _RUN_ID.match(run_id):
        raise ValueError("run id must be 32 lowercase hex characters")
    limits = settings.limits
    memory = f"{int(limits.memory_mb)}m"
    argv = [
        "run", "-i",
        "--name", container_name(settings.name, run_id),
        "--label", f"{LABEL}={settings.name}",
        "--label", f"{RUN_LABEL}={run_id}",
        "--pull", "never",
        "--network", "none",
        "--read-only",
        "--tmpfs", f"/tmp:rw,nosuid,nodev,noexec,size={int(limits.tmpfs_mb)}m,mode=1777",
        "--memory", memory,
        "--memory-swap", memory,
        "--cpus", f"{float(limits.cpus):g}",
        "--pids-limit", str(int(limits.pids)),
        "--ulimit", f"nofile={int(limits.nofile)}:{int(limits.nofile)}",
        "--ulimit", "core=0:0",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges=true",
        "--user", WORKER_USER,
        "--ipc", "none",
        "--cgroupns", "private",
        "--log-driver", "none",
        "--init",
        "--hostname", "kazma-docjob",
        "--workdir", "/tmp",
        "--entrypoint", "python",
        image_id,
        "-I", "-m", ENTRY_MODULE, worker,
    ]
    if settings.qualification:
        argv.append("--qualification")
    return argv


def _check_attestation(facts: dict[str, Any], limits: BrokerLimits) -> list[str]:
    """Every way the probe's view differs from the policy; empty means it holds."""

    problems: list[str] = []
    status = facts.get("status") or {}
    if facts.get("uid") in (None, 0) or facts.get("gid") in (None, 0):
        problems.append("worker runs as root")
    for key in ("CapEff", "CapPrm", "CapAmb"):
        if status.get(key) not in ("0000000000000000",):
            problems.append(f"{key} is not empty ({status.get(key)!r})")
    if status.get("NoNewPrivs") != "1":
        problems.append("no_new_privs is not set")
    if status.get("Seccomp") != "2":
        problems.append("seccomp filter is not active")
    if facts.get("interfaces") != ["lo"]:
        problems.append(f"network interfaces present: {facts.get('interfaces')!r}")
    if facts.get("external_connect_errno") is None:
        problems.append("an outside connection succeeded")
    if facts.get("dns_resolved"):
        problems.append("DNS resolution worked")
    if facts.get("sensitive_paths_visible"):
        problems.append(f"host paths visible: {facts.get('sensitive_paths_visible')!r}")
    if facts.get("tmp_write_errno") is not None:
        problems.append("scratch tmpfs is not writable")
    for path, code in (facts.get("write_errno") or {}).items():
        if code is None:
            problems.append(f"{path} is writable")
    if not facts.get("write_errno"):
        problems.append("root filesystem write checks missing")
    mounts = facts.get("mounts") or []
    by_point = {row.get("mount_point"): row for row in mounts}
    root = by_point.get("/")
    if root is None or "ro" not in str(root.get("options", "")).split(","):
        problems.append("root filesystem is not mounted read-only")
    tmp = by_point.get("/tmp")
    tmp_options = set(str(tmp.get("options", "")).split(",")) if tmp else set()
    if tmp is None or tmp.get("fstype") != "tmpfs" or not {"nosuid", "nodev", "noexec"} <= tmp_options:
        problems.append("/tmp is not a nosuid,nodev,noexec tmpfs")
    for point in by_point:
        if point in ("/", "/tmp") or point in _DOCKER_FILE_MOUNTS:
            continue
        if not any(point == base or str(point).startswith(base + "/") for base in _VIRTUAL_MOUNT_ROOTS):
            problems.append(f"unexpected mount {point!r}")
    cgroup = facts.get("cgroup") or {}
    expected = {
        "memory.max": str(limits.memory_mb * 1024 * 1024),
        "memory.swap.max": "0",
        "pids.max": str(limits.pids),
        "cpu.max": f"{limits.cpu_quota} 100000",
    }
    for key, value in expected.items():
        if cgroup.get(key) != value:
            problems.append(f"cgroup {key} is {cgroup.get(key)!r}, expected {value!r}")
    if facts.get("tmp_bytes") != limits.tmpfs_mb * 1024 * 1024:
        problems.append(f"/tmp size is {facts.get('tmp_bytes')!r}")
    return problems


def _bundle_bytes(magic: bytes, files: list[tuple[str, bytes]], metadata: dict[str, Any]) -> bytes:
    header = dict(metadata)
    header["files"] = [
        {"name": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        for name, data in files
    ]
    encoded = json.dumps(header, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return magic + struct.pack(">I", len(encoded)) + encoded + b"".join(data for _, data in files)


def _bundle_result(payload: bytes) -> dict[str, Any]:
    """``result.json`` of an output bundle the broker itself asked for."""

    if not payload.startswith(_OUTPUT_MAGIC):
        raise ValueError("probe output is not a bundle")
    offset = len(_OUTPUT_MAGIC)
    (size,) = struct.unpack(">I", payload[offset:offset + 4])
    header = json.loads(payload[offset + 4:offset + 4 + size])
    cursor = offset + 4 + size
    for record in header["files"]:
        data = payload[cursor:cursor + int(record["size"])]
        cursor += int(record["size"])
        if record["name"] == "result.json":
            if hashlib.sha256(data).hexdigest() != record["sha256"]:
                raise ValueError("probe result digest mismatch")
            return dict(json.loads(data))
    raise ValueError("probe output has no result")


class _Refused(Exception):
    pass


class _Broker:
    """Runs fixed document workers in attested, disposable containers."""

    def __init__(self, settings: BrokerSettings) -> None:
        settings.validate()
        self.settings = settings
        self.limits = settings.limits
        self.image_id: str | None = None
        self.attestation: dict[str, Any] = {"verified": False, "problems": ["not attested"]}
        self.capabilities: dict[str, Any] | None = None
        self.capability_error: str | None = "not collected"
        self._slots = threading.BoundedSemaphore(self.limits.concurrency)
        self._quarantined: dict[str, str] = {}
        self._active: dict[str, subprocess.Popen[bytes]] = {}
        self._lock = threading.Lock()
        self._token = settings.token.encode("utf-8")

    # ── docker CLI ──────────────────────────────────────────────────────
    def _env(self) -> dict[str, str]:
        upper = {key.upper(): key for key in os.environ}
        return {upper[name]: os.environ[upper[name]] for name in _ALLOWED_ENV if name in upper}

    def _docker(self, *args: str, timeout: float = 30.0) -> subprocess.CompletedProcess[str]:
        """Run the docker CLI; a slow engine is a failed call, never an exception."""

        argv = [self.settings.docker, *args]
        try:
            return subprocess.run(  # nosec B603 - fixed argv, docker CLI from operator config, no shell
                argv,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                env=self._env(),
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except subprocess.TimeoutExpired:
            logger.warning("[broker] docker %s did not answer within %.0fs", args[0], timeout)
            return subprocess.CompletedProcess(argv, -1, "", "timeout")

    def resolve_image(self) -> str:
        result = self._docker("image", "inspect", "--format", "{{.Id}}", self.settings.image)
        image_id = result.stdout.strip()
        if result.returncode != 0 or not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
            raise RuntimeError(f"worker image {self.settings.image!r} is not available locally")
        self.image_id = image_id
        return image_id

    def _containers(self, label: str) -> list[str]:
        result = self._docker("ps", "-a", "-q", "--no-trunc", "--filter", f"label={label}")
        if result.returncode != 0:
            raise RuntimeError("docker ps failed")
        return [line for line in result.stdout.split() if line]

    def sweep(self) -> int:
        """Remove every container a previous run of this broker name left."""

        stale = self._containers(f"{LABEL}={self.settings.name}")
        if stale:
            self._docker("rm", "-f", *stale, timeout=60)
        remaining = [cid for cid in self._containers(f"{LABEL}={self.settings.name}") if cid in stale]
        if remaining:
            raise RuntimeError(f"{len(remaining)} stale document job containers could not be removed")
        return len(stale)

    def _remove(self, run_id: str) -> bool:
        name = container_name(self.settings.name, run_id)
        for _ in range(3):
            self._docker("rm", "-f", name, timeout=60)
            try:
                if not self._containers(f"{RUN_LABEL}={run_id}"):
                    return True
            except RuntimeError:
                pass
            time.sleep(0.5)
        return False

    def _kill(self, run_id: str, process: subprocess.Popen[bytes]) -> None:
        self._docker("kill", container_name(self.settings.name, run_id), timeout=30)
        if process.poll() is None:
            process.kill()

    def _retry_quarantine(self) -> None:
        with self._lock:
            pending = list(self._quarantined)
        for run_id in pending:
            if self._remove(run_id):
                with self._lock:
                    self._quarantined.pop(run_id, None)
                self._slots.release()
                logger.info("[broker] quarantined container %s removed; slot restored", run_id)

    # ── jobs ────────────────────────────────────────────────────────────
    def execute(
        self,
        *,
        worker: str,
        run_id: str,
        timeout_seconds: float,
        input_size: int,
        read_input: Callable[[int], bytes],
        emit: Callable[[bytes], None],
        cancelled: Callable[[], bool],
        on_admitted: Callable[[], None] = lambda: None,
    ) -> dict[str, Any]:
        """Run one job; the slot must already be held. Always returns a status.

        The output pump never stops draining the CLI's stdout: an attached
        container whose stream backs up cannot be killed by the engine
        (measured on Docker 29: ``docker kill`` and ``rm -f`` both hung while
        the broker waited on a kill instead of reading). Kills therefore run
        on their own thread, and a CLI that does not exit after a stop is
        terminated so its stream closes.
        """

        if self.image_id is None:
            raise RuntimeError("worker image is not resolved")
        timeout = min(float(timeout_seconds), float(self.limits.max_timeout_seconds))
        argv = [self.settings.docker, *_container_argv(self.settings, self.image_id, run_id, worker)]
        started = time.monotonic()
        process = subprocess.Popen(  # nosec B603 - fixed policy argv, no shell
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=self._env(),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        name = container_name(self.settings.name, run_id)
        with self._lock:
            self._active[name] = process
        events: queue.Queue[bytes | None] = queue.Queue(maxsize=64)
        stderr = bytearray()
        stderr_truncated = threading.Event()
        flags = {"timeout": False, "output_limit": False, "cancelled": False}
        stop_lock = threading.Lock()
        stopping = threading.Event()
        abandoned = threading.Event()
        stopped_at: list[float] = []
        killers: list[threading.Thread] = []

        def stop(reason: str) -> None:
            # The first reason wins. Never blocks: see the docstring.
            with stop_lock:
                first = not any(flags.values())
                if first:
                    flags[reason] = True
                    stopped_at.append(time.monotonic())
            stopping.set()
            if first:
                killer = threading.Thread(
                    target=self._kill, args=(run_id, process), name=f"docjob-kill-{run_id[:8]}", daemon=True
                )
                killer.start()
                killers.append(killer)

        def pump_stdout() -> None:
            assert process.stdout is not None
            try:
                while chunk := process.stdout.read1(65_536):
                    while not stopping.is_set():
                        try:
                            events.put(chunk, timeout=0.1)
                            break
                        except queue.Full:
                            continue
            finally:
                while not abandoned.is_set():
                    try:
                        events.put(None, timeout=0.1)
                        break
                    except queue.Full:
                        continue

        def pump_stderr() -> None:
            assert process.stderr is not None
            while chunk := process.stderr.read(65_536):
                room = self.limits.max_stderr_bytes - len(stderr)
                if room > 0:
                    stderr.extend(chunk[:room])
                if len(chunk) > max(room, 0):
                    stderr_truncated.set()

        readers = [
            threading.Thread(target=pump_stdout, name=f"docjob-out-{run_id[:8]}", daemon=True),
            threading.Thread(target=pump_stderr, name=f"docjob-err-{run_id[:8]}", daemon=True),
        ]
        for reader in readers:
            reader.start()
        watchdog = threading.Timer(timeout, stop, args=("timeout",))
        watchdog.daemon = True
        watchdog.start()
        sent = 0
        failure: OSError | None = None
        finished = False
        assert process.stdin is not None
        try:
            on_admitted()
            remaining = int(input_size)
            stdin_open = True
            while remaining > 0:
                try:
                    chunk = read_input(min(MAX_FRAME, remaining))
                except OSError:
                    chunk = b""
                if not chunk:
                    stop("cancelled")
                    break
                remaining -= len(chunk)
                if stdin_open:
                    try:
                        process.stdin.write(chunk)
                    except OSError:
                        stdin_open = False
            _close_quietly(process.stdin)
            terminated = False
            while True:
                try:
                    item = events.get(timeout=0.1)
                except queue.Empty:
                    if not stopping.is_set() and cancelled():
                        stop("cancelled")
                    if stopped_at and not terminated and time.monotonic() - stopped_at[0] > _STOP_GRACE_SECONDS:
                        # The engine did not end the container: end the CLI so
                        # its stream closes; removal below finishes the job.
                        terminated = True
                        process.kill()
                    continue
                if item is None:
                    break
                if stopping.is_set():
                    continue
                sent += len(item)
                if sent > self.limits.max_output_bytes:
                    stop("output_limit")
                    continue
                try:
                    emit(item)
                except OSError:
                    stop("cancelled")
            finished = True
        except OSError as exc:
            # The client connection broke: the job ends as an error status.
            failure = exc
            logger.warning("[broker] job %s lost its connection: %s", run_id, type(exc).__name__)
        finally:
            if not finished:
                stop("cancelled")
            watchdog.cancel()
            _close_quietly(process.stdin)
            try:
                process.wait(timeout=_STOP_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
            abandoned.set()
            for thread in (*killers, *readers):
                thread.join(timeout=_STOP_GRACE_SECONDS)
            with self._lock:
                self._active.pop(name, None)
            state = self._inspect(run_id)
            removed = self._remove(run_id)
            if not removed:
                with self._lock:
                    self._quarantined[run_id] = worker
                logger.error("[broker] job container %s could not be removed; slot quarantined", run_id)
        if failure is not None:
            outcome = "error"
        elif flags["timeout"]:
            outcome = "timeout"
        elif flags["output_limit"]:
            outcome = "output_limit"
        elif flags["cancelled"]:
            outcome = "cancelled"
        elif state.get("OOMKilled"):
            outcome = "memory_limit"
        elif not state:
            # docker run never produced a container (daemon, image or name error).
            outcome = "error"
        else:
            outcome = "completed"
        return {
            "status": outcome,
            "exit_code": state.get("ExitCode", process.returncode),
            "oom_killed": bool(state.get("OOMKilled")),
            "stderr": bytes(stderr).decode("utf-8", errors="replace"),
            "stderr_truncated": stderr_truncated.is_set(),
            "output_bytes": sent,
            "duration_seconds": round(time.monotonic() - started, 3),
            "cleanup": "verified" if removed else "failed",
            "image_id": self.image_id,
            "run_id": run_id,
        }

    def _inspect(self, run_id: str) -> dict[str, Any]:
        result = self._docker(
            "inspect", "--format", "{{json .State}}", container_name(self.settings.name, run_id)
        )
        if result.returncode != 0:
            return {}
        try:
            value = json.loads(result.stdout)
        except json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {}

    def acquire(self) -> bool:
        self._retry_quarantine()
        return self._slots.acquire(timeout=float(self.limits.admission_wait_seconds))

    def release(self, run_id: str) -> None:
        with self._lock:
            quarantined = run_id in self._quarantined
        if not quarantined:
            self._slots.release()

    def run_local(self, worker: str, request: dict[str, Any], *, timeout_seconds: float = 60.0) -> tuple[dict[str, Any], dict[str, Any] | None]:
        """Run a request-only job in-process (attestation and qualification)."""

        payload = _bundle_bytes(
            _INPUT_MAGIC,
            [("work/request.json", json.dumps(request, sort_keys=True).encode("utf-8"))],
            {"worker": worker},
        )
        view = memoryview(payload)
        position = {"at": 0}
        output = bytearray()

        def read_input(size: int) -> bytes:
            start = position["at"]
            position["at"] = start + size
            return bytes(view[start:start + size])

        run_id = uuid.uuid4().hex
        if not self.acquire():
            raise RuntimeError("no free broker slot")
        try:
            status = self.execute(
                worker=worker,
                run_id=run_id,
                timeout_seconds=timeout_seconds,
                input_size=len(payload),
                read_input=read_input,
                emit=output.extend,
                cancelled=lambda: False,
            )
        finally:
            self.release(run_id)
        result = None
        if status["status"] == "completed":
            try:
                result = _bundle_result(bytes(output))
            except (ValueError, KeyError, struct.error, json.JSONDecodeError):
                result = None
        return status, result

    def attest(self) -> dict[str, Any]:
        status, result = self.run_local("probe", {"action": "attest"})
        facts = (result or {}).get("facts") if isinstance(result, dict) else None
        if status["status"] != "completed" or not isinstance(facts, dict):
            problems = [
                f"probe produced no attestation (container {status['status']}, "
                f"exit {status['exit_code']}): {status['stderr'][-500:]}"
            ]
        else:
            problems = _check_attestation(facts, self.limits)
        self.attestation = {
            "verified": not problems,
            "problems": problems,
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "facts": facts,
            "cleanup": status["cleanup"],
        }
        return self.attestation

    def prepare(self) -> int:
        """Everything before serving: pin the image, sweep, attest, collect.

        Returns how many stale containers the sweep removed. ``main`` and the
        qualification tests both start a broker through here.
        """

        self.resolve_image()
        swept = self.sweep()
        if self.attest()["verified"]:
            self.collect_capabilities()
        return swept

    def collect_capabilities(self) -> dict[str, Any] | None:
        """The engines the worker image has, as the image itself reports them.

        Opaque JSON to the broker; Kazma builds its parser/renderer/OCR
        readiness from it. Fixed for the broker's life (the image is pinned).
        """

        status, result = self.run_local("probe", {"action": "capabilities"}, timeout_seconds=180)
        facts = result.get("facts") if isinstance(result, dict) and result.get("ok") is True else None
        if isinstance(facts, dict) and len(json.dumps(facts)) <= MAX_FRAME // 2:
            self.capabilities, self.capability_error = facts, None
        else:
            self.capabilities = None
            self.capability_error = (
                f"capability probe produced no report (container {status['status']}, "
                f"exit {status['exit_code']}): {status['stderr'][-300:]}"
            )
        return self.capabilities

    def status(self) -> dict[str, Any]:
        with self._lock:
            active, quarantined = len(self._active), len(self._quarantined)
        ready = (
            bool(self.attestation.get("verified"))
            and self.capabilities is not None
            and quarantined < self.limits.concurrency
        )
        return {
            "status": "ok",
            "protocol": PROTOCOL,
            "ready": ready,
            "broker": self.settings.name,
            "image": self.settings.image,
            "image_id": self.image_id,
            "limits": asdict(self.limits),
            "attestation": {
                key: self.attestation.get(key) for key in ("verified", "problems", "at", "cleanup")
            },
            "capabilities": self.capabilities,
            "capability_error": self.capability_error,
            "active": active,
            "quarantined": quarantined,
            "qualification": self.settings.qualification,
        }

    def shutdown(self) -> None:
        with self._lock:
            active = list(self._active.items())
        for name, process in active:
            self._docker("kill", name, timeout=30)
            if process.poll() is None:
                process.kill()
        try:
            self.sweep()
        except RuntimeError as exc:
            logger.error("[broker] shutdown sweep failed: %s", exc)

    # ── connections ─────────────────────────────────────────────────────
    def _read_request(self, sock: socket.socket) -> dict[str, Any]:
        magic = _recv_exact(sock, len(REQUEST_MAGIC))
        if magic != REQUEST_MAGIC:
            raise _Refused("protocol mismatch")
        (size,) = struct.unpack(">I", _recv_exact(sock, 4))
        if size > MAX_REQUEST_HEADER:
            raise _Refused("request header too large")
        try:
            header = json.loads(_recv_exact(sock, size).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise _Refused("request header is not JSON") from exc
        if not isinstance(header, dict) or header.get("protocol") != PROTOCOL:
            raise _Refused("unsupported protocol")
        token = header.get("token")
        if not isinstance(token, str) or not hmac.compare_digest(token.encode("utf-8"), self._token):
            raise _Refused("authentication failed")
        op = header.get("op")
        if op == "status":
            return {"op": "status"}
        if op != "run":
            raise _Refused("unsupported operation")
        allowed = {"protocol", "token", "op", "worker", "run_id", "timeout_seconds", "input_size"}
        if set(header) != allowed:
            raise _Refused("run request fields are invalid")
        worker, run_id = header["worker"], header["run_id"]
        timeout, size = header["timeout_seconds"], header["input_size"]
        if worker not in WORKERS:
            raise _Refused("unknown worker")
        if worker == "probe" and not self.settings.qualification:
            raise _Refused("probe jobs are not served")
        if not isinstance(run_id, str) or not _RUN_ID.match(run_id):
            raise _Refused("invalid run id")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 86_400:
            raise _Refused("invalid deadline")
        if isinstance(size, bool) or not isinstance(size, int) or not 0 < size <= self.limits.max_input_bytes:
            raise _Refused("input size is outside the broker limit")
        return {"op": "run", "worker": worker, "run_id": run_id, "timeout_seconds": float(timeout), "input_size": size}

    def handle(self, sock: socket.socket) -> None:
        sock.settimeout(float(self.limits.header_timeout_seconds))
        try:
            request = self._read_request(sock)
        except _Refused as exc:
            logger.warning("[broker] request refused: %s", exc)
            _send_frame(sock, FRAME_STATUS, {"status": "rejected", "message": str(exc)})
            return
        if request["op"] == "status":
            _send_frame(sock, FRAME_STATUS, self.status())
            return
        if not self.attestation.get("verified") or self.image_id is None:
            _send_frame(sock, FRAME_STATUS, {"status": "unavailable", "message": "broker policy is not attested"})
            return
        if not self.acquire():
            _send_frame(sock, FRAME_STATUS, {"status": "busy", "message": "no free document worker slot"})
            return
        run_id = request["run_id"]
        deadline = time.monotonic() + min(request["timeout_seconds"], self.limits.max_timeout_seconds)

        def read_input(size: int) -> bytes:
            sock.settimeout(max(0.5, deadline - time.monotonic()))
            return sock.recv(size)

        def cancelled() -> bool:
            # The client sends nothing after its input: readable means it
            # closed the connection (or broke protocol). Either ends the job.
            readable, _, _ = select.select([sock], [], [], 0)
            return bool(readable)

        def emit(data: bytes) -> None:
            sock.settimeout(30.0)
            _send_raw(sock, FRAME_DATA, data)

        try:
            status = self.execute(
                worker=request["worker"],
                run_id=run_id,
                timeout_seconds=request["timeout_seconds"],
                input_size=request["input_size"],
                read_input=read_input,
                emit=emit,
                cancelled=cancelled,
                on_admitted=lambda: _send_raw(sock, FRAME_ADMIT, b""),
            )
        finally:
            self.release(run_id)
        logger.info(
            "[broker] job %s worker=%s status=%s exit=%s bytes=%s cleanup=%s %.2fs",
            run_id, request["worker"], status["status"], status["exit_code"],
            status["output_bytes"], status["cleanup"], status["duration_seconds"],
        )
        try:
            sock.settimeout(30.0)
            _send_frame(sock, FRAME_STATUS, status)
        except OSError:
            logger.info("[broker] client left before job %s status", run_id)


def _close_quietly(stream: Any) -> None:
    try:
        stream.close()
    except OSError as exc:  # a dead container's stdin pipe is already broken
        logger.debug("[broker] closing a job pipe failed: %s", exc)


def _recv_exact(sock: socket.socket, size: int) -> bytes:
    data = bytearray()
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise _Refused("connection closed")
        data.extend(chunk)
    return bytes(data)


def _send_raw(sock: socket.socket, kind: bytes, data: bytes) -> None:
    for start in range(0, max(len(data), 1), MAX_FRAME):
        part = data[start:start + MAX_FRAME]
        sock.sendall(kind + struct.pack(">I", len(part)) + part)


def _send_frame(sock: socket.socket, kind: bytes, value: dict[str, Any]) -> None:
    _send_raw(sock, kind, json.dumps(value, sort_keys=True).encode("utf-8"))


class _Handler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        server = self.server
        if not server.connections.acquire(blocking=False):  # type: ignore[attr-defined]
            return
        try:
            server.broker.handle(self.request)  # type: ignore[attr-defined]
        finally:
            server.connections.release()  # type: ignore[attr-defined]


def _log_connection_error(request: object, client_address: object) -> None:
    # socketserver calls this for an exception that escaped one connection;
    # the broker keeps serving the others.
    logger.exception("[broker] connection from %s failed", client_address)


class _TcpServer(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = False

    def handle_error(self, request: Any, client_address: Any) -> None:
        _log_connection_error(request, client_address)


def _make_server(
    listen: str,
    broker: _Broker,
    *,
    allow_non_loopback: bool,
    socket_gid: int | None = None,
    socket_mode: int = 0o660,
) -> socketserver.BaseServer:
    server: socketserver.BaseServer
    if listen.startswith("unix:"):
        path = Path(listen[5:])
        if not hasattr(socket, "AF_UNIX"):
            raise ValueError("unix sockets are not available on this platform")
        if path.exists():
            path.unlink()

        class _UnixServer(socketserver.ThreadingUnixStreamServer):  # type: ignore[name-defined,misc]
            daemon_threads = True

            def handle_error(self, request: Any, client_address: Any) -> None:
                _log_connection_error(request, client_address)

        server = _UnixServer(str(path), _Handler)
        # Owner and the named group only (Kazma's container user); the token
        # still authenticates every request on top of the file permission.
        if socket_gid is not None:
            os.chown(path, -1, socket_gid)
        os.chmod(path, socket_mode)
    elif listen.startswith("tcp:"):
        host, _, port = listen[4:].rpartition(":")
        if not allow_non_loopback and not ipaddress.ip_address(host).is_loopback:
            raise ValueError("a non-loopback listen address needs --allow-non-loopback")
        server = _TcpServer((host, int(port)), _Handler)
    else:
        raise ValueError("listen must be unix:<path> or tcp:<host>:<port>")
    server.broker = broker  # type: ignore[attr-defined]
    server.connections = threading.BoundedSemaphore(4 * broker.limits.concurrency + 8)  # type: ignore[attr-defined]
    return server


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    defaults = BrokerLimits()
    parser = argparse.ArgumentParser(description="Kazma document worker broker")
    parser.add_argument("--image", required=True, help="worker image reference (resolved to its id at start)")
    parser.add_argument("--token-file", required=True, type=Path)
    parser.add_argument("--listen", default="tcp:127.0.0.1:8765")
    parser.add_argument("--docker", default=None, help="docker CLI path (default: PATH)")
    parser.add_argument("--name", default="default")
    parser.add_argument("--memory-mb", type=int, default=defaults.memory_mb)
    parser.add_argument("--cpus", type=float, default=defaults.cpus)
    parser.add_argument("--pids", type=int, default=defaults.pids)
    parser.add_argument("--tmpfs-mb", type=int, default=defaults.tmpfs_mb)
    parser.add_argument("--concurrency", type=int, default=defaults.concurrency)
    parser.add_argument("--max-timeout", type=float, default=defaults.max_timeout_seconds)
    parser.add_argument("--max-input-mb", type=int, default=defaults.max_input_bytes // (1024 * 1024))
    parser.add_argument("--max-output-mb", type=int, default=defaults.max_output_bytes // (1024 * 1024))
    parser.add_argument("--allow-non-loopback", action="store_true")
    parser.add_argument("--socket-gid", type=int, default=None,
                        help="group owning a unix listen socket (Kazma's container group)")
    parser.add_argument("--socket-mode", type=lambda value: int(value, 8), default=0o660,
                        help="octal mode of a unix listen socket (default 660)")
    parser.add_argument("--qualification", action="store_true",
                        help="serve probe stress jobs (qualification only; never in production)")
    parser.add_argument("--attest-only", action="store_true",
                        help="attest the policy, print the report and exit")
    return parser.parse_args(argv)


def _find_docker_cli() -> str | None:
    """Kazma's one docker lookup (``kazma_core/docker_cli.py``: an explicit
    path, PATH, Docker's install folders -- a Docker Desktop update once took
    the CLI off PATH), loaded BY PATH because the broker stays standard-library
    only and never imports ``kazma_core``. The broker image is this file alone
    and names its CLI in ``KAZMA_DOCKER_BIN``."""
    import importlib.util
    import sys

    sibling = Path(__file__).resolve().parents[1] / "docker_cli.py"
    if sibling.is_file():
        spec = importlib.util.spec_from_file_location("kazma_broker_docker_cli", sibling)
        if spec is not None and spec.loader is not None:
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            return module.find_docker_cli()
    named = os.environ.get("KAZMA_DOCKER_BIN", "").strip()
    return named if named and Path(named).is_file() else None


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = _parse_args(argv)
    docker = args.docker or _find_docker_cli()
    if not docker:
        logger.error("[broker] docker CLI not found")
        return 2
    token = args.token_file.read_text(encoding="utf-8").strip()
    limits = BrokerLimits(
        memory_mb=args.memory_mb,
        cpus=args.cpus,
        pids=args.pids,
        tmpfs_mb=args.tmpfs_mb,
        concurrency=args.concurrency,
        max_timeout_seconds=args.max_timeout,
        max_input_bytes=args.max_input_mb * 1024 * 1024,
        max_output_bytes=args.max_output_mb * 1024 * 1024,
    )
    settings = BrokerSettings(
        docker=docker, image=args.image, token=token, name=args.name,
        limits=limits, qualification=args.qualification,
    )
    try:
        broker = _Broker(settings)
        swept = broker.prepare()
        if swept:
            logger.warning("[broker] removed %d containers left by an earlier run", swept)
        report = broker.attestation
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        logger.error("[broker] cannot start: %s", exc)
        return 2
    if args.attest_only:
        print(json.dumps(
            {"image_id": broker.image_id, **report, "capabilities": broker.capabilities,
             "capability_error": broker.capability_error},
            indent=2, sort_keys=True,
        ))
        return 0 if report["verified"] and broker.capabilities is not None else 3
    if not report["verified"]:
        logger.error("[broker] container policy did not hold; refusing to serve: %s", report["problems"])
        return 3
    if broker.capabilities is None:
        logger.error("[broker] the worker image reported no engines; refusing to serve: %s",
                     broker.capability_error)
        return 3
    try:
        server = _make_server(
            args.listen, broker, allow_non_loopback=args.allow_non_loopback,
            socket_gid=args.socket_gid, socket_mode=args.socket_mode,
        )
    except (OSError, ValueError) as exc:
        logger.error("[broker] cannot listen on %s: %s", args.listen, exc)
        return 2
    logger.info("[broker] serving %s image=%s on %s", settings.name, broker.image_id, args.listen)
    # `docker stop` sends SIGTERM: stop serving, then kill and remove every
    # job container (the finally below), instead of dying with jobs running.
    signal.signal(signal.SIGTERM, lambda *_: threading.Thread(target=server.shutdown, daemon=True).start())
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        logger.info("[broker] stopping")
    finally:
        server.server_close()
        broker.shutdown()
        logger.info("[broker] stopped; job containers of %s removed", settings.name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
