"""Fixed entry point of the containerized document worker.

The broker starts every job container with the same command:
``python -I -m kazma_core.documents.worker_entry <worker> [--qualification]``,
where ``<worker>`` is one of :data:`WORKERS`. This module is trusted code that
runs before any untrusted byte is parsed:

1. fd 1 is moved aside and replaced with stderr, so anything a parser or a
   child process prints cannot corrupt the result stream;
2. the input bundle on stdin is staged under :data:`RUN_ROOT` (the
   container's size-bounded tmpfs; there are no mounts and no network);
3. the selected worker module runs its ordinary ``execute(request, result)``;
4. ``result.json`` and at most one ``output.<ext>`` go back on the saved fd 1
   as an output bundle.

``probe`` reports what the container can see and do -- identity,
capabilities, seccomp, mounts, interfaces, a refused connection, cgroup
limits -- which the broker checks before it serves (attestation). Its
stress actions exist to qualify the limits and run only when the broker
was started with ``--qualification``.
"""

from __future__ import annotations

import errno
import importlib
import json
import os
import signal
import socket
import sys
import time
from pathlib import Path
from typing import Any

from .worker_bundle import (
    INPUT_MAGIC,
    OUTPUT_MAGIC,
    RUN_ROOT,
    BundleError,
    read_bundle,
    validate_input_name,
    validate_output_name,
    write_bundle,
)

WORKERS: dict[str, str | None] = {
    "parser": "kazma_core.documents.parser_worker",
    "mutation": "kazma_core.documents.mutation_worker",
    "renderer": "kazma_core.documents.renderer_worker",
    "probe": None,
}
_MAX_INPUT_TOTAL = 1024 * 1024 * 1024
_MAX_INPUT_FILE = 512 * 1024 * 1024
_STRESS_ACTIONS = frozenset({
    "allocate", "spawn", "spin", "fill_tmp", "sleep", "orphan", "flood", "stderr_flood", "crash",
})
#: Paths whose presence would mean the container sees host state.
SENSITIVE_PATHS = (
    "/var/run/docker.sock",
    "/run/docker.sock",
    "/app/kazma-data",
    "/home/kazma",
    "/root/.kazma",
    "/kazma-data",
)


def _failure(code: str, message: str) -> bytes:
    return json.dumps(
        {"protocol_version": 1, "ok": False, "code": code, "message": message},
        sort_keys=True,
    ).encode("utf-8")


def _emit(out_fd: int, files: list[tuple[str, Path | bytes]], exit_code: int) -> None:
    with os.fdopen(out_fd, "wb", closefd=True) as stream:
        write_bundle(
            stream,
            OUTPUT_MAGIC,
            files,
            validate_name=validate_output_name,
            metadata={"exit_code": int(exit_code)},
        )


def _proc_status() -> dict[str, str]:
    fields: dict[str, str] = {}
    try:
        text = Path("/proc/self/status").read_text(encoding="ascii")
    except OSError:
        return fields
    for line in text.splitlines():
        key, _, value = line.partition(":")
        if key in {"Uid", "Gid", "CapEff", "CapPrm", "CapBnd", "CapAmb", "NoNewPrivs", "Seccomp"}:
            fields[key] = value.strip()
    return fields


def _unescape(value: str) -> str:
    out, index = [], 0
    while index < len(value):
        if value[index] == "\\" and value[index + 1:index + 4].isdigit():
            out.append(chr(int(value[index + 1:index + 4], 8)))
            index += 4
        else:
            out.append(value[index])
            index += 1
    return "".join(out)


def _mounts() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    try:
        text = Path("/proc/self/mountinfo").read_text(encoding="utf-8")
    except OSError:
        return rows
    for line in text.splitlines():
        left, _, right = line.partition(" - ")
        fields, fs = left.split(), right.split()
        if len(fields) < 6 or len(fs) < 2:
            continue
        rows.append(
            {
                "mount_point": _unescape(fields[4]),
                "options": fields[5],
                "fstype": fs[0],
                "super_options": fs[2] if len(fs) > 2 else "",
            }
        )
    return rows


def _read(path: str) -> str | None:
    try:
        return Path(path).read_text(encoding="ascii").strip()
    except OSError:
        return None


def _connect_errno(host: str, port: int) -> int | None:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.settimeout(2.0)
    try:
        probe.connect((host, port))
    except OSError as exc:
        return exc.errno if exc.errno is not None else -1
    finally:
        probe.close()
    return None


def _write_errno(directory: str) -> int | None:
    target = Path(directory) / f".kazma-probe-{os.getpid()}"
    try:
        with target.open("xb"):
            pass
    except OSError as exc:
        return exc.errno
    target.unlink(missing_ok=True)
    return None


def _attest() -> dict[str, Any]:
    try:
        resolved = bool(socket.getaddrinfo("example.com", 443))
    except OSError:
        resolved = False
    try:
        interfaces = sorted(name for _, name in socket.if_nameindex())
    except OSError:
        interfaces = []
    return {
        "uid": os.getuid(),
        "gid": os.getgid(),
        "status": _proc_status(),
        "mounts": _mounts(),
        "interfaces": interfaces,
        "external_connect_errno": _connect_errno("198.51.100.1", 9),
        "dns_resolved": resolved,
        "write_errno": {
            path: _write_errno(path) for path in ("/", "/opt", "/usr", "/etc", "/var")
        },
        "tmp_write_errno": _write_errno(RUN_ROOT),
        "sensitive_paths_visible": [path for path in SENSITIVE_PATHS if os.path.lexists(path)],
        "cgroup": {
            name: _read(f"/sys/fs/cgroup/{name}")
            for name in ("memory.max", "memory.swap.max", "pids.max", "cpu.max")
        },
        "tmp_bytes": (lambda st: st.f_blocks * st.f_frsize)(os.statvfs("/tmp")),
    }


def _stress(action: str, request: dict[str, Any], out_fd: int) -> dict[str, Any]:
    """Bounded misbehaviour that the container limits must contain."""

    if action == "sleep":
        time.sleep(float(request.get("seconds", 1)))
        return {"slept": True}
    if action == "allocate":
        mib = int(request.get("mib", 64))
        block = bytearray(mib * 1024 * 1024)
        for index in range(0, len(block), 4096):
            block[index] = 1
        return {"allocated_mib": mib}
    if action == "fill_tmp":
        written, code = 0, None
        target = Path(RUN_ROOT) / "fill.bin"
        chunk = b"\0" * (1024 * 1024)
        try:
            with target.open("wb") as handle:
                while True:
                    handle.write(chunk)
                    handle.flush()
                    written += len(chunk)
        except OSError as exc:
            code = exc.errno
        finally:
            target.unlink(missing_ok=True)
        return {"written": written, "errno": code}
    if action == "spawn":
        children: list[int] = []
        code = None
        try:
            for _ in range(int(request.get("count", 64))):
                pid = os.fork()
                if pid == 0:
                    time.sleep(60)
                    os._exit(0)
                children.append(pid)
        except OSError as exc:
            code = exc.errno
        for pid in children:
            os.kill(pid, 9)
            os.waitpid(pid, 0)
        return {"spawned": len(children), "errno": code}
    if action == "spin":
        workers, seconds = int(request.get("workers", 4)), float(request.get("seconds", 3))
        started = time.monotonic()
        pids = []
        for _ in range(workers):
            pid = os.fork()
            if pid == 0:
                deadline = time.monotonic() + seconds
                while time.monotonic() < deadline:
                    pass
                os._exit(0)
            pids.append(pid)
        cpu = 0.0
        for pid in pids:
            _, _, usage = os.wait4(pid, 0)
            cpu += usage.ru_utime + usage.ru_stime
        wall = time.monotonic() - started
        return {"cpu_seconds": cpu, "wall_seconds": wall, "cores_used": cpu / wall if wall else None}
    if action == "orphan":
        reader, writer = os.pipe()
        pid = os.fork()
        if pid == 0:
            os.close(reader)
            os.setsid()
            grandchild = os.fork()
            if grandchild == 0:
                time.sleep(600)
                os._exit(0)
            os.write(writer, str(grandchild).encode())
            os._exit(0)
        os.close(writer)
        os.waitpid(pid, 0)
        grandchild = int(os.read(reader, 32) or b"0")
        os.close(reader)
        return {"orphan_pid": grandchild}
    if action == "flood":
        payload = b"x" * (1024 * 1024)
        for _ in range(int(request.get("mib", 8))):
            os.write(out_fd, payload)
        return {"flooded": True}
    if action == "crash":
        # A native parser crash: the process dies by signal, no result.
        os.kill(os.getpid(), signal.SIGSEGV)
    if action == "stderr_flood":
        payload = b"e" * (1024 * 1024)
        for _ in range(int(request.get("mib", 8))):
            os.write(2, payload)
        return {"flooded": True}
    raise ValueError("unknown probe action")


def _capabilities() -> dict[str, Any]:
    """The engines THIS image has: Kazma reads readiness from here, not from
    its own process, when documents run in containers."""

    import dataclasses

    from .mutation import get_mutation_registry
    from .ocr import get_ocr_health
    from .registry import ParserRegistry
    from .renderers import get_renderer_registry
    from .worker_bundle import code_fingerprint

    def rows(items: Any) -> list[Any]:
        return json.loads(json.dumps([dataclasses.asdict(item) for item in items], default=str))

    return {
        "parsers": rows(ParserRegistry().capabilities()),
        "renderers": rows(get_renderer_registry().capabilities()),
        "mutators": rows(get_mutation_registry().capabilities()),
        "ocr": get_ocr_health(()).to_dict(),
        "documents_code_sha256": code_fingerprint(Path(__file__).resolve().parent),
    }


def _probe(request_path: Path, result_path: Path, *, qualification: bool, out_fd: int) -> int:
    request = json.loads(request_path.read_text(encoding="utf-8"))
    action = request.get("action") if isinstance(request, dict) else None
    if action == "attest":
        facts = _attest()
    elif action == "capabilities":
        facts = _capabilities()
    elif action in _STRESS_ACTIONS and qualification:
        facts = _stress(str(action), request, out_fd)
    else:
        result_path.write_bytes(_failure("probe_action_refused", "Probe action is not available"))
        return 0
    result_path.write_text(
        json.dumps({"protocol_version": 1, "ok": True, "code": "ok", "facts": facts}, sort_keys=True),
        encoding="utf-8",
    )
    return 0


def _outputs(work: Path, result_path: Path) -> list[tuple[str, Path | bytes]]:
    files: list[tuple[str, Path | bytes]] = [("result.json", result_path)]
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return files
    name = result.get("output_name") if isinstance(result, dict) else None
    if not (isinstance(result, dict) and result.get("ok") is True and isinstance(name, str)):
        return files
    try:
        validate_output_name(name)
    except BundleError:
        return files
    output = work / name
    if output.is_file() and not output.is_symlink():
        files.append((name, output))
    return files


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    qualification = args[1:] == ["--qualification"]
    if not args or args[0] not in WORKERS or (len(args) > 1 and not qualification):
        sys.stderr.write("usage: worker_entry <parser|mutation|renderer|probe>\n")
        return 2
    worker = args[0]
    out_fd = os.dup(1)
    os.dup2(2, 1)
    for directory in (os.environ.get(name) for name in ("HOME", "XDG_CACHE_HOME", "XDG_CONFIG_HOME")):
        if directory:
            Path(directory).mkdir(parents=True, exist_ok=True)
    root = Path(RUN_ROOT)
    root.mkdir(mode=0o700)
    try:
        _, metadata = read_bundle(
            sys.stdin.buffer,
            INPUT_MAGIC,
            root,
            validate_name=validate_input_name,
            max_total_bytes=_MAX_INPUT_TOTAL,
            max_file_bytes=_MAX_INPUT_FILE,
            allowed_metadata=frozenset({"worker"}),
        )
        if metadata.get("worker") != worker:
            raise BundleError("bundle names a different worker")
    except (BundleError, OSError) as exc:
        code = exc.code if isinstance(exc, BundleError) else "worker_staging_failed"
        if isinstance(exc, OSError) and exc.errno == errno.ENOSPC:
            code = "worker_scratch_full"
        _emit(out_fd, [("result.json", _failure(code, "Worker input was refused"))], 2)
        return 2
    work = root / "work"
    (root / "storage").mkdir(exist_ok=True)
    request_path, result_path = work / "request.json", work / "result.json"
    module = WORKERS[worker]
    if module is None:
        exit_code = _probe(request_path, result_path, qualification=qualification, out_fd=out_fd)
    else:
        exit_code = int(importlib.import_module(module).execute(request_path, result_path))
    if not result_path.is_file():
        _emit(out_fd, [("result.json", _failure("worker_result_missing", "Worker wrote no result"))], 1)
        return 1
    _emit(out_fd, _outputs(work, result_path), exit_code)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
