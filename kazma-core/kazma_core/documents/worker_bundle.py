"""Bounded byte framing between Kazma and a containerized document worker.

The container profile (``KAZMA_DOCUMENT_WORKER_ISOLATION=container``) runs the
parser, mutation and renderer workers in a fresh container with no mounts and
no network. Nothing is shared with the host, so a run travels as two streams:

* **input** (Kazma -> container stdin): ``work/request.json`` plus the exact
  files the request names, staged under ``in/`` (sources) and
  ``work/assets/`` (approved render assets);
* **output** (container stdout -> Kazma): ``result.json`` and at most one
  ``output.<ext>`` the worker wrote next to it.

Each stream is ``MAGIC``, a 4-byte big-endian header length, a JSON header
listing ``{name, size, sha256}`` per file, then the files' bytes in that
order and nothing after them. A reader writes only names its side allows,
never overwrites, stops at the declared sizes and caps, and checks every
digest; the output side is written by code that has just processed an
untrusted document, so Kazma reads it as untrusted.

Standard library only: the in-container entry point and the stand-alone
broker image import this module without the rest of Kazma.
"""

from __future__ import annotations

import hashlib
import json
import re
import struct
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO

__all__ = [
    "BundleError",
    "BundleFile",
    "INPUT_MAGIC",
    "OUTPUT_MAGIC",
    "PreparedBundle",
    "RUN_ROOT",
    "code_fingerprint",
    "prepare_bundle",
    "read_bundle",
    "validate_input_name",
    "validate_output_name",
    "write_bundle",
]

INPUT_MAGIC = b"KZDW-IN/1\n"
OUTPUT_MAGIC = b"KZDW-OUT/1\n"
#: Fixed staging root inside the worker container (a size-bounded tmpfs).
RUN_ROOT = "/tmp/kazma-run"
MAX_HEADER_BYTES = 64 * 1024
MAX_FILES = 64
_CHUNK = 1024 * 1024
_SAFE_PART = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_OUTPUT_FILE = re.compile(r"output\.[a-z0-9]{1,8}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class BundleError(ValueError):
    """A stream does not follow the bundle protocol; nothing in it is used."""

    def __init__(self, message: str, *, code: str = "worker_bundle_invalid") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class BundleFile:
    name: str
    size: int
    sha256: str


def _safe_part(part: str) -> str:
    if not _SAFE_PART.match(part) or ".." in part:
        raise BundleError(f"unsafe bundle file name part {part!r}")
    return part


def validate_input_name(name: str) -> str:
    """``work/request.json``, ``in/<file>`` or ``work/assets/<file>``."""

    if not isinstance(name, str):
        raise BundleError("bundle file name must be a string")
    if name == "work/request.json":
        return name
    parts = PurePosixPath(name).parts
    if not parts or str(PurePosixPath(*parts)) != name:
        raise BundleError(f"bundle file name is not canonical: {name!r}")
    if len(parts) == 2 and parts[0] == "in":
        _safe_part(parts[1])
        return name
    if len(parts) == 3 and parts[:2] == ("work", "assets"):
        _safe_part(parts[2])
        return name
    raise BundleError(f"bundle input name is not allowed: {name!r}")


def validate_output_name(name: str) -> str:
    """``result.json`` or a single ``output.<ext>``."""

    if not isinstance(name, str):
        raise BundleError("bundle file name must be a string")
    if name == "result.json" or _OUTPUT_FILE.match(name):
        return name
    raise BundleError(f"bundle output name is not allowed: {name!r}")


def _digest(source: Path | bytes) -> tuple[int, str]:
    if isinstance(source, bytes):
        return len(source), hashlib.sha256(source).hexdigest()
    digest = hashlib.sha256()
    size = 0
    with Path(source).open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


@dataclass(frozen=True, slots=True)
class PreparedBundle:
    """A bundle whose header and exact size are known before it is sent."""

    magic: bytes
    header: bytes
    files: tuple[tuple[BundleFile, Path | bytes], ...]

    @property
    def size(self) -> int:
        return len(self.magic) + 4 + len(self.header) + sum(item.size for item, _ in self.files)

    def write(self, stream: BinaryIO) -> int:
        """Send the bundle; a file that changed since it was prepared fails."""

        written = 0
        for part in (self.magic, struct.pack(">I", len(self.header)), self.header):
            stream.write(part)
            written += len(part)
        for item, source in self.files:
            digest = hashlib.sha256()
            copied = 0
            if isinstance(source, bytes):
                chunks: Iterable[bytes] = (source,)
                handle = None
            else:
                handle = Path(source).open("rb")
                chunks = iter(lambda h=handle: h.read(_CHUNK), b"")
            try:
                for chunk in chunks:
                    copied += len(chunk)
                    if copied > item.size:
                        raise BundleError(f"{item.name} grew while it was sent")
                    digest.update(chunk)
                    stream.write(chunk)
                    written += len(chunk)
            finally:
                if handle is not None:
                    handle.close()
            if copied != item.size or digest.hexdigest() != item.sha256:
                raise BundleError(f"{item.name} changed while it was sent")
        stream.flush()
        return written


def prepare_bundle(
    magic: bytes,
    files: Sequence[tuple[str, Path | bytes]],
    *,
    validate_name: Callable[[str], str],
    metadata: Mapping[str, object] | None = None,
) -> PreparedBundle:
    """Validate names and digest every file once; nothing is sent yet."""

    if len(files) > MAX_FILES:
        raise BundleError("too many files for one bundle")
    prepared: list[tuple[BundleFile, Path | bytes]] = []
    seen: set[str] = set()
    for name, source in files:
        validate_name(name)
        if name in seen:
            raise BundleError(f"duplicate bundle file {name!r}")
        seen.add(name)
        size, sha = _digest(source)
        prepared.append((BundleFile(name, size, sha), source))
    header = dict(metadata or {})
    if "files" in header:
        raise BundleError("bundle metadata cannot replace the file list")
    header["files"] = [
        {"name": item.name, "size": item.size, "sha256": item.sha256} for item, _ in prepared
    ]
    encoded = json.dumps(header, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_HEADER_BYTES:
        raise BundleError("bundle header is too large")
    return PreparedBundle(magic, encoded, tuple(prepared))


def write_bundle(
    stream: BinaryIO,
    magic: bytes,
    files: Sequence[tuple[str, Path | bytes]],
    *,
    validate_name: Callable[[str], str],
    metadata: Mapping[str, object] | None = None,
) -> int:
    """Write *files* as one bundle; returns the number of bytes written."""

    return prepare_bundle(
        magic, files, validate_name=validate_name, metadata=metadata
    ).write(stream)


def code_fingerprint(root: Path) -> str:
    """SHA-256 of a package's Python source (paths and text, line endings unified).

    The worker image reports it for its ``kazma_core.documents``; Kazma
    compares it with its own, so an image built from other code (a deploy
    that did not rebuild it) is visible in readiness.
    """

    root = Path(root)
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts):
        digest.update(path.relative_to(root).as_posix().encode("utf-8") + b"\0")
        digest.update(path.read_bytes().replace(b"\r\n", b"\n") + b"\0")
    return digest.hexdigest()


def _read_exact(stream: BinaryIO, size: int) -> bytes:
    data = bytearray()
    while len(data) < size:
        chunk = stream.read(size - len(data))
        if not chunk:
            raise BundleError("bundle ended early")
        data.extend(chunk)
    return bytes(data)


def read_bundle(
    stream: BinaryIO,
    magic: bytes,
    destination: Path,
    *,
    validate_name: Callable[[str], str],
    max_total_bytes: int,
    max_file_bytes: int,
    allowed_metadata: frozenset[str] = frozenset(),
    require_eof: bool = True,
) -> tuple[dict[str, Path], dict[str, object]]:
    """Materialize a bundle under *destination*; returns ``(files, metadata)``.

    Every file is created exclusively (an existing path is an error) inside
    *destination*; sizes are capped before any byte is written past them,
    and each digest is checked as the file closes. On any error the caller
    discards *destination* — partial files are never used.
    """

    if _read_exact(stream, len(magic)) != magic:
        raise BundleError("bundle magic mismatch")
    (header_size,) = struct.unpack(">I", _read_exact(stream, 4))
    if header_size > MAX_HEADER_BYTES:
        raise BundleError("bundle header is too large")
    try:
        header = json.loads(_read_exact(stream, header_size).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BundleError("bundle header is not JSON") from exc
    if not isinstance(header, dict) or not isinstance(header.get("files"), list):
        raise BundleError("bundle header is invalid")
    unexpected = set(header) - {"files"} - set(allowed_metadata)
    if unexpected:
        raise BundleError(f"bundle header has unexpected keys: {sorted(unexpected)}")
    records = header["files"]
    if len(records) > MAX_FILES:
        raise BundleError("too many files in bundle")
    parsed: list[BundleFile] = []
    total = 0
    seen: set[str] = set()
    for record in records:
        if not isinstance(record, dict) or set(record) != {"name", "size", "sha256"}:
            raise BundleError("bundle file record is invalid")
        name, size, sha = record["name"], record["size"], record["sha256"]
        validate_name(name)
        if name in seen:
            raise BundleError(f"duplicate bundle file {name!r}")
        seen.add(name)
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise BundleError(f"bundle file size is invalid for {name!r}")
        if size > max_file_bytes:
            raise BundleError(f"{name} exceeds the per-file limit", code="worker_bundle_limit")
        if not isinstance(sha, str) or not _SHA256.match(sha):
            raise BundleError(f"bundle digest is invalid for {name!r}")
        total += size
        if total > max_total_bytes:
            raise BundleError("bundle exceeds its total limit", code="worker_bundle_limit")
        parsed.append(BundleFile(name, size, sha))
    root = destination.resolve()
    written: dict[str, Path] = {}
    for item in parsed:
        target = (root / item.name).resolve()
        if root not in target.parents:
            raise BundleError(f"bundle file escapes its root: {item.name!r}")
        target.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        remaining = item.size
        with target.open("xb") as handle:
            while remaining:
                chunk = stream.read(min(_CHUNK, remaining))
                if not chunk:
                    raise BundleError("bundle ended early")
                remaining -= len(chunk)
                digest.update(chunk)
                handle.write(chunk)
        if digest.hexdigest() != item.sha256:
            raise BundleError(f"{item.name} does not match its digest")
        written[item.name] = target
    if require_eof and stream.read(1):
        raise BundleError("bundle has trailing bytes")
    metadata = {key: header[key] for key in header if key != "files"}
    return written, metadata
