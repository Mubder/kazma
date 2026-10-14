"""Kazma's side of the containerized document worker profile.

``KAZMA_DOCUMENT_WORKER_ISOLATION`` selects how the parser, mutation and
renderer workers run:

* ``process`` (default) -- a local child process under
  :func:`kazma_core.documents.sandbox.run_isolated_subprocess`: scrubbed
  environment, time/output caps, Windows Job Object or POSIX rlimit
  resources. It has **no** filesystem or network isolation, and readiness
  says so.
* ``container`` -- every worker run goes to the document worker broker
  (:mod:`kazma_core.documents.worker_broker`), which alone holds the Docker
  engine connection and runs the worker in a fresh, attested container with
  no network and no mounts. The request and the exact files it names travel
  as a bundle (:mod:`kazma_core.documents.worker_bundle`); shared document
  storage is never mounted. A broker that is unconfigured, unreachable,
  unattested or busy refuses the run with a typed error -- there is no
  fallback to ``process``.

Any other value refuses every run (a typo must not quietly mean "process").
The broker is addressed by ``KAZMA_DOCUMENT_BROKER_URL`` (``unix:///path``
or ``tcp://host:port``) and authenticated with the token in the file named
by ``KAZMA_DOCUMENT_BROKER_TOKEN_FILE``.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import re
import socket
import struct
import threading
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from .errors import DocumentSandboxError
from .ocr.base import OcrComponentHealth, OcrHealth, OcrReadiness
from .registry import ParserCapability, ParserPlugin, ParserReadiness, ParserRegistry
from .renderers import RendererCapability, RendererPlugin, RendererReadiness, RendererRegistry
from .sandbox import SandboxResult
from .worker_bundle import (
    INPUT_MAGIC,
    OUTPUT_MAGIC,
    RUN_ROOT,
    BundleError,
    code_fingerprint,
    prepare_bundle,
    read_bundle,
    validate_input_name,
    validate_output_name,
)

__all__ = [
    "CONTAINER",
    "PROCESS",
    "image_engines_unavailable",
    "image_ocr_health",
    "image_parser_registry",
    "image_renderer_registry",
    "isolation_report",
    "operation_container_request",
    "parser_container_request",
    "process_profile_refusal",
    "run_container_worker",
    "uses_worker_image",
    "worker_isolation_mode",
]

logger = logging.getLogger(__name__)

ISOLATION_ENV = "KAZMA_DOCUMENT_WORKER_ISOLATION"
BROKER_URL_ENV = "KAZMA_DOCUMENT_BROKER_URL"
BROKER_TOKEN_FILE_ENV = "KAZMA_DOCUMENT_BROKER_TOKEN_FILE"
PROCESS, CONTAINER = "process", "container"
_PROTOCOL = 1
_REQUEST_MAGIC = b"KZDB-REQ/1\n"
_MAX_FRAME = 1024 * 1024
_MIN_TOKEN_CHARS = 32
#: Container start, image checks and removal happen outside the job deadline.
_BROKER_GRACE_SECONDS = 90.0
_MODULE_WORKERS = {
    "kazma_core.documents.parser_worker": "parser",
    "kazma_core.documents.mutation_worker": "mutation",
    "kazma_core.documents.renderer_worker": "renderer",
}
_SAFE_SUFFIX = re.compile(r"\.[A-Za-z0-9]{1,16}\Z")
_STATUS_TTL_SECONDS = 15.0
_status_cache: tuple[float, dict[str, Any]] | None = None
_status_lock = threading.Lock()


def _unavailable(message: str, code: str = "worker_isolation_unavailable") -> DocumentSandboxError:
    return DocumentSandboxError(message, code=code)


def worker_isolation_mode() -> str:
    """``process`` or ``container``; anything else refuses execution."""

    raw = (os.environ.get(ISOLATION_ENV) or "").strip().lower()
    if raw in ("", PROCESS):
        return PROCESS
    if raw == CONTAINER:
        return CONTAINER
    raise _unavailable(
        f"{ISOLATION_ENV} must be 'process' or 'container'; document execution is refused",
        code="worker_isolation_invalid",
    )


def process_profile_refusal() -> DocumentSandboxError | None:
    """Refuse the local ``process`` profile when the install requires containers.

    ``security.execution.container_required`` is the operator's strict
    execution profile (``safety/deployment_policy.py``): python_exec already
    runs only in Docker under it, and untrusted documents are untrusted
    execution too. A policy that cannot be read refuses (it must never grant
    host execution). Called only where Kazma starts a worker, never inside one.
    """

    from kazma_core.safety.deployment_policy import container_required, policy_read_failures

    try:
        required = container_required()
    except policy_read_failures():
        return DocumentSandboxError(
            "The execution policy could not be read; local document execution is refused",
            code="document_policy_unavailable",
        )
    if required:
        return DocumentSandboxError(
            "This install requires container isolation (security.execution.container_required): "
            "set KAZMA_DOCUMENT_WORKER_ISOLATION=container with a document worker broker",
            code="worker_isolation_required",
        )
    return None


@dataclass(frozen=True, slots=True)
class _Endpoint:
    family: int
    address: Any
    token: str


def _endpoint() -> _Endpoint:
    url = (os.environ.get(BROKER_URL_ENV) or "").strip()
    token_file = (os.environ.get(BROKER_TOKEN_FILE_ENV) or "").strip()
    if not url or not token_file:
        raise _unavailable(
            f"Container document isolation needs {BROKER_URL_ENV} and {BROKER_TOKEN_FILE_ENV}"
        )
    try:
        token = Path(token_file).read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise _unavailable("The document broker token file cannot be read") from exc
    if len(token) < _MIN_TOKEN_CHARS:
        raise _unavailable(f"The document broker token must be at least {_MIN_TOKEN_CHARS} characters")
    if url.startswith("unix://"):
        if not hasattr(socket, "AF_UNIX"):
            raise _unavailable("Unix sockets are not available on this platform")
        return _Endpoint(socket.AF_UNIX, url[len("unix://"):], token)
    match = re.fullmatch(r"tcp://([^:/]+|\[[0-9a-fA-F:]+\]):(\d{1,5})", url)
    if not match:
        raise _unavailable(f"{BROKER_URL_ENV} must be unix:///path or tcp://host:port")
    host = match.group(1).strip("[]")
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    return _Endpoint(family, (host, int(match.group(2))), token)


def _connect(endpoint: _Endpoint, timeout: float) -> socket.socket:
    sock = socket.socket(endpoint.family, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(endpoint.address)
    except OSError as exc:
        sock.close()
        raise _unavailable("The document worker broker is unreachable") from exc
    return sock


def _send_request(sock: socket.socket, header: Mapping[str, Any]) -> None:
    encoded = json.dumps(dict(header), sort_keys=True).encode("utf-8")
    sock.sendall(_REQUEST_MAGIC + struct.pack(">I", len(encoded)) + encoded)


def _recv_exact(sock: socket.socket, size: int, deadline: float) -> bytes:
    data = bytearray()
    while len(data) < size:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("document broker deadline passed")
        sock.settimeout(remaining)
        chunk = sock.recv(min(size - len(data), _MAX_FRAME))
        if not chunk:
            raise ConnectionError("document broker closed the connection")
        data.extend(chunk)
    return bytes(data)


def _recv_frame(sock: socket.socket, deadline: float) -> tuple[bytes, bytes]:
    head = _recv_exact(sock, 5, deadline)
    kind, (size,) = head[:1], struct.unpack(">I", head[1:])
    if kind not in (b"A", b"D", b"S") or size > _MAX_FRAME:
        raise ConnectionError("document broker sent an invalid frame")
    return kind, _recv_exact(sock, size, deadline)


def _status_frame(payload: bytes) -> dict[str, Any]:
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConnectionError("document broker status is not JSON") from exc
    if not isinstance(value, dict) or not isinstance(value.get("status"), str):
        raise ConnectionError("document broker status is invalid")
    return value


def _staged_source(host: Path, stem: str) -> str:
    suffix = Path(host).suffix
    if not _SAFE_SUFFIX.match(suffix):
        raise DocumentSandboxError(
            "This document's file extension cannot be staged for isolated execution",
            code="worker_input_unsupported",
        )
    return f"in/{stem}{suffix}"


def parser_container_request(
    request: Mapping[str, Any], source: Path
) -> tuple[dict[str, Any], list[tuple[str, Path]]]:
    """The parser request as the container sees it, and the file it needs."""

    body = copy.deepcopy(dict(request))
    staged = _staged_source(source, "source")
    body["source_path"] = f"{RUN_ROOT}/{staged}"
    config = body.get("config")
    if not isinstance(config, dict) or "storage_root" not in config:
        raise DocumentSandboxError("Parser request has no configuration", code="worker_input_unsupported")
    # A per-run scratch root: the shared content store is never visible.
    config["storage_root"] = f"{RUN_ROOT}/storage"
    return body, [(staged, Path(source))]


def operation_container_request(
    module: str, request: Mapping[str, Any], run_dir: Path
) -> tuple[str, dict[str, Any], list[tuple[str, Path]]]:
    """Worker name, container-side request and staged files for an operation."""

    worker = _MODULE_WORKERS.get(module)
    if worker not in ("mutation", "renderer"):
        raise DocumentSandboxError("Unknown document worker", code="worker_input_unsupported")
    body = copy.deepcopy(dict(request))
    inputs: list[tuple[str, Path]] = []
    if worker == "mutation":
        sources = body.get("sources")
        if not isinstance(sources, list):
            raise DocumentSandboxError("Mutation request has no sources", code="worker_input_unsupported")
        for index, item in enumerate(sources):
            host = Path(str(item["path"]))
            staged = _staged_source(host, f"source-{index}")
            item["path"] = f"{RUN_ROOT}/{staged}"
            inputs.append((staged, host))
    else:
        if body.get("source_path") is not None:
            host = Path(str(body["source_path"]))
            staged = _staged_source(host, "source")
            body["source_path"] = f"{RUN_ROOT}/{staged}"
            inputs.append((staged, host))
        assets = Path(run_dir) / "assets"
        if assets.is_dir():
            for path in sorted(assets.iterdir()):
                if path.is_file() and not path.is_symlink():
                    inputs.append((f"work/assets/{path.name}", path))
    return worker, body, inputs


def run_container_worker(
    *,
    worker: str,
    request: Mapping[str, Any],
    inputs: Sequence[tuple[str, Path]],
    run_dir: Path,
    timeout_seconds: float,
    max_output_bytes: int,
    stderr_limit_bytes: int = 65_536,
) -> SandboxResult:
    """Run one worker through the broker; outputs land in *run_dir*.

    ``result.json`` (and the worker's ``output.<ext>``) are written exactly
    where the local process worker writes them, so the caller's validation
    is the same for both profiles.
    """

    endpoint = _endpoint()
    try:
        bundle = prepare_bundle(
            INPUT_MAGIC,
            [
                ("work/request.json", json.dumps(dict(request), ensure_ascii=False, sort_keys=True).encode("utf-8")),
                *inputs,
            ],
            validate_name=validate_input_name,
            metadata={"worker": worker},
        )
    except (BundleError, OSError) as exc:
        raise DocumentSandboxError("Document worker input could not be staged", code="worker_input_unsupported") from exc
    run_id = uuid.uuid4().hex
    started = time.monotonic()
    deadline = started + float(timeout_seconds) + _BROKER_GRACE_SECONDS
    output_path = Path(run_dir) / ".container-output.bundle"
    try:
        status = _exchange(
            endpoint, bundle, worker, run_id, float(timeout_seconds), deadline,
            output_path, max_output_bytes,
        )
        if status is None:
            # Over the caller's cap: the connection was closed, which makes
            # the broker kill and remove the container.
            return _result(worker, started, output_limit=True)
        return _interpret(worker, status, started, run_dir, output_path, max_output_bytes)
    finally:
        output_path.unlink(missing_ok=True)


def _exchange(
    endpoint: _Endpoint,
    bundle: Any,
    worker: str,
    run_id: str,
    timeout_seconds: float,
    deadline: float,
    output_path: Path,
    max_output_bytes: int,
) -> dict[str, Any] | None:
    """Send one run; returns the broker's status, or None past the output cap."""

    sock = _connect(endpoint, timeout=10.0)
    received = 0
    try:
        _send_request(
            sock,
            {
                "protocol": _PROTOCOL,
                "token": endpoint.token,
                "op": "run",
                "worker": worker,
                "run_id": run_id,
                "timeout_seconds": timeout_seconds,
                "input_size": bundle.size,
            },
        )
        kind, payload = _recv_frame(sock, deadline)
        if kind == b"S":
            return _status_frame(payload)
        if kind != b"A":
            raise ConnectionError("document broker did not admit the job")
        sock.settimeout(max(1.0, deadline - time.monotonic()))
        try:
            with sock.makefile("wb") as stream:
                bundle.write(stream)
        except BundleError as exc:
            raise DocumentSandboxError(
                "Document changed before isolated execution began", code="document_changed"
            ) from exc
        with output_path.open("xb") as handle:
            while True:
                kind, payload = _recv_frame(sock, deadline)
                if kind == b"S":
                    return _status_frame(payload)
                if kind != b"D":
                    raise ConnectionError("document broker sent an unexpected frame")
                received += len(payload)
                if received > max_output_bytes:
                    return None
                handle.write(payload)
    except (OSError, ConnectionError, TimeoutError) as exc:
        raise _unavailable(
            f"The document worker broker failed during the run ({type(exc).__name__})"
        ) from exc
    finally:
        sock.close()


def _result(
    worker: str,
    started: float,
    *,
    returncode: int = -9,
    stderr: bytes = b"",
    timed_out: bool = False,
    output_limit: bool = False,
) -> SandboxResult:
    return SandboxResult(
        command=("document-worker-broker", worker),
        returncode=returncode,
        stdout=b"",
        stderr=stderr,
        duration_seconds=time.monotonic() - started,
        timed_out=timed_out,
        output_limit_exceeded=output_limit,
        resource_limits_enforced=True,
        resource_limit_degraded_reason=None,
    )


def _interpret(
    worker: str,
    status: dict[str, Any],
    started: float,
    run_dir: Path,
    output_path: Path,
    max_output_bytes: int,
) -> SandboxResult:
    outcome = status.get("status")
    stderr = str(status.get("stderr") or "").encode("utf-8")
    if status.get("cleanup") == "failed":
        logger.warning(
            "[documents.worker_container] broker could not remove job container %s; its slot is held",
            status.get("run_id"),
        )
    if outcome == "timeout":
        return _result(worker, started, stderr=stderr, timed_out=True)
    if outcome == "output_limit":
        return _result(worker, started, stderr=stderr, output_limit=True)
    if outcome == "memory_limit":
        raise DocumentSandboxError("Document worker exceeded its memory limit", code="parser_oom")
    if outcome == "busy":
        raise _unavailable("No isolated document worker slot is free", code="worker_isolation_busy")
    if outcome != "completed":
        raise _unavailable(
            f"The document worker broker refused or lost the run ({outcome})"
        )
    exit_code = status.get("exit_code")
    returncode = exit_code if isinstance(exit_code, int) and not isinstance(exit_code, bool) else -1
    if not output_path.is_file() or output_path.stat().st_size == 0:
        # The worker died before it wrote a stream (a crash): the same "no
        # result" the local profile reports, so the caller's own handling
        # applies (parser_result_missing, retried within the finite budget).
        return _result(worker, started, returncode=returncode, stderr=stderr)
    try:
        with output_path.open("rb") as stream:
            files, _ = read_bundle(
                stream,
                OUTPUT_MAGIC,
                Path(run_dir),
                validate_name=validate_output_name,
                max_total_bytes=max_output_bytes,
                max_file_bytes=max_output_bytes,
                allowed_metadata=frozenset({"exit_code"}),
            )
        if "result.json" not in files or len(files) > 2:
            raise BundleError("a worker returns result.json and at most one output file")
    except (BundleError, OSError, ValueError) as exc:
        # The stream comes from code that just handled an untrusted document.
        raise DocumentSandboxError(
            "Document worker returned an invalid result stream", code="worker_output_invalid"
        ) from exc
    return _result(worker, started, returncode=returncode, stderr=stderr)


def _broker_status(*, max_age_seconds: float = _STATUS_TTL_SECONDS) -> dict[str, Any]:
    """The broker's own report (cached briefly); never raises."""

    global _status_cache
    with _status_lock:
        cached = _status_cache
    if cached is not None and time.monotonic() - cached[0] < max_age_seconds:
        return dict(cached[1])
    try:
        endpoint = _endpoint()
        sock = _connect(endpoint, timeout=5.0)
        try:
            _send_request(sock, {"protocol": _PROTOCOL, "token": endpoint.token, "op": "status"})
            kind, payload = _recv_frame(sock, time.monotonic() + 10.0)
        finally:
            sock.close()
        if kind != b"S":
            raise ConnectionError("unexpected broker frame")
        report = _status_frame(payload)
    except (DocumentSandboxError, OSError, ConnectionError, TimeoutError) as exc:
        report = {"status": "unreachable", "ready": False, "error": type(exc).__name__,
                  "message": getattr(exc, "safe_message", "document broker unreachable")}
    with _status_lock:
        _status_cache = (time.monotonic(), report)
    return dict(report)


def isolation_report() -> dict[str, Any]:
    """What isolation document workers run under, for health and readiness."""

    try:
        mode = worker_isolation_mode()
    except DocumentSandboxError as exc:
        return {
            "mode": "invalid",
            "ready": False,
            "filesystem_isolation": False,
            "network_isolation": False,
            "reason": exc.safe_message,
        }
    if mode == PROCESS:
        refusal = process_profile_refusal()
        return {
            "mode": PROCESS,
            "ready": refusal is None,
            "filesystem_isolation": False,
            "network_isolation": False,
            "resource_limits": "windows_job_object" if os.name == "nt" else "posix_rlimit",
            "hostile_document_profile": "unqualified",
            "reason": refusal.safe_message if refusal is not None else None,
        }
    status = _broker_status()
    ready = status.get("ready") is True
    attestation = status.get("attestation") if isinstance(status.get("attestation"), dict) else {}
    capabilities = status.get("capabilities") if isinstance(status.get("capabilities"), dict) else {}
    image_code = capabilities.get("documents_code_sha256")
    return {
        "mode": CONTAINER,
        "ready": ready,
        "filesystem_isolation": ready,
        "network_isolation": ready,
        "resource_limits": "container_cgroup" if ready else None,
        "hostile_document_profile": "container_broker" if ready else "unavailable",
        # False: the image was built from other document code than this
        # Kazma runs (a deploy that did not rebuild it). Responses are still
        # validated strictly; rebuild the image and restart the broker.
        "image_code_matches": (
            image_code == _local_code_fingerprint() if isinstance(image_code, str) else None
        ),
        "engines_reported": bool(capabilities),
        "broker": {
            "status": status.get("status"),
            "name": status.get("broker"),
            "image_id": status.get("image_id"),
            "limits": status.get("limits"),
            "attested": bool(attestation.get("verified")),
            "attested_at": attestation.get("at"),
            "problems": attestation.get("problems"),
            "active": status.get("active"),
            "quarantined": status.get("quarantined"),
            "error": status.get("error"),
            "capability_error": status.get("capability_error"),
        },
    }


# ── engine readiness as the worker image reports it ─────────────────────
#
# In the container profile the engines that matter are the image's, not the
# Kazma process's: a host without LibreOffice must not refuse a conversion
# the image can do, and a host with Docling must not promise a salvage tier
# the image lacks. The four factories (get_parser_registry,
# get_renderer_registry, get_mutation_registry, get_ocr_health) answer from
# the broker's capability report, read per call (the broker status is cached
# briefly), so a service built while the broker was down catches up when it
# is back. Anything the image does not report is unavailable, with the reason.


def uses_worker_image() -> bool:
    """True unless the local ``process`` profile is in force.

    An invalid setting counts as the image profile with no image: every
    engine reads unavailable, naming the bad setting.
    """

    try:
        return worker_isolation_mode() != PROCESS
    except DocumentSandboxError:
        return True


def _image_report() -> tuple[dict[str, Any] | None, str]:
    """The image's capability report, or None and why there is none."""

    try:
        worker_isolation_mode()
    except DocumentSandboxError as exc:
        return None, exc.safe_message
    status = _broker_status()
    if status.get("ready") is not True:
        detail = status.get("capability_error") or status.get("message") or status.get("status")
        return None, f"The document worker broker is not ready ({detail})"
    report = status.get("capabilities")
    if not isinstance(report, dict):
        return None, "The document worker broker reported no engine capabilities"
    return report, ""


def image_engines_unavailable() -> tuple[str, str] | None:
    """``(code, message)`` when the container profile cannot say which engines
    exist (no ready broker, or an invalid setting); None otherwise."""

    try:
        if worker_isolation_mode() == PROCESS:
            return None
    except DocumentSandboxError as exc:
        return exc.code, exc.safe_message
    report, why = _image_report()
    return None if report is not None else ("worker_isolation_unavailable", why)


def _strings(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise TypeError("expected a list")
    return tuple(str(item) for item in value)


def _versions(value: Any) -> dict[str, str | None]:
    if not isinstance(value, dict):
        return {}
    return {str(key): (None if item is None else str(item)) for key, item in value.items()}


def _reason(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _image_parser_capabilities(plugins: Mapping[str, ParserPlugin]) -> dict[str, ParserCapability]:
    report, why = _image_report()
    rows = {
        row["parser_id"]: row
        for row in (report or {}).get("parsers") or []
        if isinstance(row, dict) and isinstance(row.get("parser_id"), str)
    }
    capabilities: dict[str, ParserCapability] = {}
    for parser_id, plugin in plugins.items():
        row = rows.get(parser_id)
        capability = None
        if row is not None:
            try:
                capability = ParserCapability(
                    parser_id=parser_id,
                    parser_version=str(row["parser_version"]),
                    mime_types=_strings(row["mime_types"]),
                    extensions=_strings(row["extensions"]),
                    features=_strings(row["features"]),
                    limits=_strings(row["limits"]),
                    isolation_required=bool(row["isolation_required"]),
                    readiness=ParserReadiness(row["readiness"]),
                    reason=_reason(row.get("reason")),
                    dependencies=_versions(row.get("dependencies")),
                    system_binaries=_versions(row.get("system_binaries")),
                )
            except (KeyError, TypeError, ValueError):
                why = why or "The worker image's parser report is invalid"
        if capability is None:
            capability = ParserCapability(
                parser_id=parser_id,
                parser_version=plugin.parser_version,
                mime_types=plugin.mime_types,
                extensions=plugin.extensions,
                features=plugin.features,
                limits=plugin.limits,
                isolation_required=plugin.isolation_required,
                readiness=ParserReadiness.UNAVAILABLE,
                reason=why or f"{parser_id} is not in the document worker image",
                dependencies={},
                system_binaries={},
            )
        capabilities[parser_id] = capability
    return capabilities


class _ImageParserRegistry(ParserRegistry):
    """Parser readiness from the worker image, read on every lookup."""

    def __init__(self) -> None:  # no local probing: the image is the truth
        from .parsers import builtin_plugins

        self._plugins = {plugin.parser_id: plugin for plugin in builtin_plugins()}

    @property  # type: ignore[override]
    def _capabilities(self) -> dict[str, ParserCapability]:
        return _image_parser_capabilities(self._plugins)


def _image_renderer_capabilities(
    kind: str, plugins: Sequence[RendererPlugin]
) -> tuple[RendererCapability, ...]:
    report, why = _image_report()
    capabilities: list[RendererCapability] = []
    for row in (report or {}).get(kind) or []:
        try:
            capabilities.append(RendererCapability(
                str(row["renderer_id"]),
                str(row["renderer_version"]),
                _strings(row["operations"]),
                _strings(row["formats"]),
                _strings(row["features"]),
                RendererReadiness(row["readiness"]),
                _reason(row.get("reason")),
                _versions(row.get("dependencies")),
                _versions(row.get("system_binaries")),
            ))
        except (KeyError, TypeError, ValueError):
            why = why or f"The worker image's {kind} report is invalid"
    reported = {item.renderer_id for item in capabilities}
    # Engines the image does not have stay listed, unavailable, so a request
    # for their operation explains itself instead of reading "unsupported".
    for plugin in plugins:
        if plugin.renderer_id not in reported:
            capabilities.append(RendererCapability(
                plugin.renderer_id, plugin.renderer_version, plugin.operations, plugin.formats,
                plugin.features, RendererReadiness.UNAVAILABLE,
                why or f"{plugin.renderer_id} is not in the document worker image", {}, {},
            ))
    return tuple(capabilities)


class _ImageRendererRegistry(RendererRegistry):
    """Renderer or mutator readiness from the worker image, read per lookup."""

    def __init__(self, kind: str, plugins: Sequence[RendererPlugin]) -> None:
        self._kind = kind
        self._plugins = tuple(plugins)

    @property  # type: ignore[override]
    def _capabilities(self) -> tuple[RendererCapability, ...]:
        return _image_renderer_capabilities(self._kind, self._plugins)


def image_parser_registry() -> ParserRegistry:
    return _ImageParserRegistry()


def image_renderer_registry(kind: str, plugins: Sequence[RendererPlugin]) -> RendererRegistry:
    if kind not in ("renderers", "mutators"):
        raise ValueError("kind must be 'renderers' or 'mutators'")
    return _ImageRendererRegistry(kind, plugins)


def _component(value: Any, fallback_reason: str) -> OcrComponentHealth:
    if not isinstance(value, dict):
        return OcrComponentHealth(OcrReadiness.UNAVAILABLE, None, reason=fallback_reason)
    try:
        readiness = OcrReadiness(value.get("readiness"))
    except ValueError:
        return OcrComponentHealth(OcrReadiness.UNAVAILABLE, None, reason=fallback_reason)
    name, version = value.get("name"), value.get("version")
    return OcrComponentHealth(
        readiness,
        name if isinstance(name, str) else None,
        version if isinstance(version, str) else None,
        _reason(value.get("reason")),
    )


def image_ocr_health(requested_languages: tuple[str, ...] = ()) -> OcrHealth:
    """OCR readiness inside the worker image, for the requested languages."""

    report, why = _image_report()
    row = (report or {}).get("ocr")
    if not isinstance(row, dict):
        missing = OcrComponentHealth(OcrReadiness.UNAVAILABLE, None, reason=why or "no OCR report")
        return OcrHealth(OcrReadiness.UNAVAILABLE, missing, missing, missing, (),
                         tuple(requested_languages), why or "The worker image reported no OCR")
    try:
        languages = _strings(row.get("languages") or [])
        readiness = OcrReadiness(row.get("readiness"))
    except (TypeError, ValueError):
        languages, readiness = (), OcrReadiness.UNAVAILABLE
    engine = _component(row.get("engine"), "invalid OCR engine report")
    reason = _reason(row.get("reason"))
    absent = sorted(set(requested_languages) - set(languages))
    if absent and readiness is not OcrReadiness.UNAVAILABLE:
        reason = f"Tesseract language data is missing in the worker image: {', '.join(absent)}"
        engine = OcrComponentHealth(OcrReadiness.UNAVAILABLE, engine.name, engine.version, reason)
        readiness = OcrReadiness.UNAVAILABLE
    return OcrHealth(
        readiness,
        engine,
        _component(row.get("rasterizer"), "invalid rasterizer report"),
        _component(row.get("image_parser"), "invalid image parser report"),
        languages,
        tuple(requested_languages),
        reason,
    )


@lru_cache(maxsize=1)
def _local_code_fingerprint() -> str:
    return code_fingerprint(Path(__file__).resolve().parent)
