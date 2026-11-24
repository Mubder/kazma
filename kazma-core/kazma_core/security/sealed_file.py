"""A file sealed whole with a password: one AEAD over every byte (Spaces
S4-06, after its review).

A space bundle was a zip of WinZip AES entries (``security.sealed_zip``):
each entry sealed on its own, its key from a 1,000-round PBKDF2-SHA1, and
nothing over the zip's names or the set of entries, so a party without the
password could rename or drop an entry (a renamed ``secrets.jsonl`` imported
0 of 2 secrets) and the password was quick to guess against. This seals the
file whole:

* **the key** is scrypt of the password (:data:`SCRYPT_N`, ``r`` 8, ``p`` 1:
  128 MiB and about half a second a try), salted per file. The header's
  ``check`` (an HMAC under a second half of the derived key) tells a wrong
  password from a changed file; it opens nothing;
* **the body** is the plain file cut in chunks of :data:`CHUNK` bytes, each
  sealed with AES-256-GCM. A chunk's nonce is the file's random prefix, the
  chunk's index and whether it is the last (the STREAM construction): a
  chunk moved, dropped, repeated or cut off, or the file cut short or grown,
  does not open. Every chunk is bound to the header (its associated data),
  so the header's parameters cannot be changed either;
* **reading** (:class:`SealedReader`) checks every chunk first, then serves
  the plain bytes by position, each chunk checked again as it is read: a
  zip opens straight from it, and no plain copy is written to disk.
  **Writing** (:class:`SealedWriter`) takes the plain bytes as a stream, so
  a zip is written straight into it (Python's zipfile writes a stream it
  cannot seek).

Only the ``cryptography`` package Kazma already depends on.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import os
import struct
from pathlib import Path
from typing import Any, BinaryIO

from kazma_core.security.secret_compare import same_secret

__all__ = ["CHUNK", "MIN_PASSWORD", "SCRYPT_N", "SealedFileRefused", "SealedReader", "SealedWriter"]

#: The shortest password a sealed file takes.
MIN_PASSWORD = 12
#: scrypt's cost: 2**17 (128 MiB with ``r`` 8).
SCRYPT_N = 2**17
_SCRYPT_R = 8
_SCRYPT_P = 1
#: Plain bytes a chunk holds (the last one fewer, possibly none).
CHUNK = 1 << 16
_TAG = 16
_PREFIX = 7
_MAGIC = b"KZSEALED"
_VERSION = 1
_HEADER_MAX = 4096
_CHECK_LABEL = b"kazma-sealed-file/check/v1"


class SealedFileRefused(ValueError):
    """The file was not opened: ``code`` is ``not_sealed`` (not a file this
    module sealed, or one it cannot read), ``wrong_password``, ``changed``
    (a chunk does not open: altered, reordered, cut short or grown) or
    ``too_big`` (past the caller's bound)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _unb64(text: Any, size: int) -> bytes:
    try:
        raw = base64.b64decode(str(text or ""), validate=True)
    except ValueError:
        raise SealedFileRefused("not_sealed", "the header holds a value that is not base64") from None
    if len(raw) != size:
        raise SealedFileRefused("not_sealed", "the header holds a value of the wrong size")
    return raw


def _keys(password: str, salt: bytes) -> tuple[bytes, bytes]:
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

    derived = Scrypt(salt=salt, length=64, n=SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P).derive(password.encode("utf-8"))
    return derived[:32], derived[32:]


def _check(check_key: bytes, purpose: str, salt: bytes) -> bytes:
    return hmac.new(check_key, _CHECK_LABEL + b"\x00" + purpose.encode("utf-8") + b"\x00" + salt,
                    hashlib.sha256).digest()[:16]


def _nonce(prefix: bytes, index: int, last: bool) -> bytes:
    if index >= 2**32:
        raise SealedFileRefused("too_big", "the file has more chunks than a sealed file can hold")
    return prefix + struct.pack(">IB", index, 1 if last else 0)


class SealedWriter(io.RawIOBase):
    """Write *target*, sealed with *password* for *purpose* (a name bound
    into the file: a file sealed for another purpose does not open as this
    one). Written under a ``.partial`` name; :meth:`finish` seals the last
    chunk and renames it into place, :meth:`abandon` (or leaving it
    unfinished) removes it. Seekable it is not: a zip written into it uses
    data descriptors."""

    def __init__(self, target: Path, password: str, *, purpose: str) -> None:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        if len(password or "") < MIN_PASSWORD:
            raise ValueError(f"a sealed file's password is at least {MIN_PASSWORD} characters")
        super().__init__()
        self._target = Path(target)
        self._partial = self._target.with_name(self._target.name + ".partial")
        salt = os.urandom(16)
        key, check_key = _keys(password, salt)
        self._aead = AESGCM(key)
        self._prefix = os.urandom(_PREFIX)
        header = json.dumps({
            "version": _VERSION, "purpose": purpose, "kdf": "scrypt", "n": SCRYPT_N, "r": _SCRYPT_R,
            "p": _SCRYPT_P, "salt": _b64(salt), "chunk": CHUNK, "prefix": _b64(self._prefix),
            "check": _b64(_check(check_key, purpose, salt)),
        }, sort_keys=True).encode("utf-8")
        self._aad = _MAGIC + struct.pack(">I", len(header)) + header
        self._out: BinaryIO | None = open(self._partial, "wb")  # noqa: SIM115 -- closed by finish/abandon
        self._out.write(self._aad)
        self._buffer = bytearray()
        self._index = 0
        self._written = 0

    def writable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return False

    def tell(self) -> int:
        return self._written

    def write(self, data: Any) -> int:
        if self._out is None:
            raise ValueError("the sealed file is finished")
        view = memoryview(data).cast("B")
        self._buffer += view
        self._written += len(view)
        while len(self._buffer) > CHUNK:  # the last chunk is sealed by finish
            self._seal(bytes(self._buffer[:CHUNK]), last=False)
            del self._buffer[:CHUNK]
        return len(view)

    def _seal(self, plain: bytes, *, last: bool) -> None:
        assert self._out is not None
        self._out.write(self._aead.encrypt(_nonce(self._prefix, self._index, last), plain, self._aad))
        self._index += 1

    def finish(self) -> int:
        """Seal the last chunk and put the file in place. Returns the plain
        bytes written."""
        if self._out is None:
            raise ValueError("the sealed file is finished")
        try:
            self._seal(bytes(self._buffer), last=True)
            self._buffer = bytearray()
            self._out.flush()
            os.fsync(self._out.fileno())
            self._out.close()
            self._out = None
            self._partial.replace(self._target)
        finally:
            self.abandon()
        return self._written

    def abandon(self) -> None:
        """Remove the unfinished file (after :meth:`finish`, nothing)."""
        if self._out is not None:
            self._out.close()
            self._out = None
        if self._partial.exists():
            self._partial.unlink()

    def close(self) -> None:
        self.abandon()
        super().close()


class SealedReader(io.RawIOBase):
    """The plain bytes of the sealed file *source*, opened with *password*
    for *purpose*. Every chunk is checked when it is opened (refused with
    :class:`SealedFileRefused`); then the bytes are served by position, each
    chunk opened again as it is read. Past *max_bytes* plain bytes,
    ``too_big``."""

    def __init__(self, source: Path, password: str, *, purpose: str, max_bytes: int) -> None:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        super().__init__()
        self._file: BinaryIO = open(source, "rb")  # noqa: SIM115 -- closed by close()
        opened = False
        try:
            head = self._file.read(len(_MAGIC) + 4)
            if len(head) != len(_MAGIC) + 4 or head[:len(_MAGIC)] != _MAGIC:
                raise SealedFileRefused("not_sealed", "not a sealed file")
            (size,) = struct.unpack(">I", head[len(_MAGIC):])
            if not 0 < size <= _HEADER_MAX:
                raise SealedFileRefused("not_sealed", "the header is not one this build reads")
            header = self._file.read(size)
            if len(header) != size:
                raise SealedFileRefused("not_sealed", "the header is cut short")
            try:
                held = json.loads(header.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                raise SealedFileRefused("not_sealed", "the header is not one this build reads") from None
            expected = {"version": _VERSION, "purpose": purpose, "kdf": "scrypt", "n": SCRYPT_N,
                        "r": _SCRYPT_R, "p": _SCRYPT_P, "chunk": CHUNK}
            if not isinstance(held, dict) or any(held.get(k) != v for k, v in expected.items()):
                raise SealedFileRefused("not_sealed", "the file is not sealed the way this build seals one")
            salt = _unb64(held.get("salt"), 16)
            self._prefix = _unb64(held.get("prefix"), _PREFIX)
            key, check_key = _keys(password or "", salt)
            if not same_secret(_unb64(held.get("check"), 16), _check(check_key, purpose, salt)):
                raise SealedFileRefused("wrong_password", "the password does not open this file")
            self._aead = AESGCM(key)
            self._aad = head + header
            self._body = len(self._aad)
            total = os.fstat(self._file.fileno()).st_size - self._body
            whole, rest = divmod(total, CHUNK + _TAG)
            if rest == 0:
                # The last chunk is always sealed, so it is a whole one here.
                whole, rest = whole - 1, CHUNK + _TAG
            if whole < 0 or rest < _TAG:
                raise SealedFileRefused("changed", "the file is cut short")
            self._chunks = whole + 1
            self._size = whole * CHUNK + rest - _TAG
            if self._size > max_bytes:
                raise SealedFileRefused("too_big", f"more than {max_bytes} bytes")
            for index in range(self._chunks):
                self._open_chunk(index)
            opened = True
        finally:
            if not opened:
                self._file.close()
        self._at = 0
        self._cached: tuple[int, bytes] | None = None

    @property
    def size(self) -> int:
        return self._size

    def _open_chunk(self, index: int) -> bytes:
        from cryptography.exceptions import InvalidTag

        last = index == self._chunks - 1
        length = (self._size - index * CHUNK if last else CHUNK) + _TAG
        self._file.seek(self._body + index * (CHUNK + _TAG))
        sealed = self._file.read(length)
        if len(sealed) != length:
            raise SealedFileRefused("changed", "the file is cut short")
        try:
            return self._aead.decrypt(_nonce(self._prefix, index, last), sealed, self._aad)
        except InvalidTag:
            raise SealedFileRefused("changed", "the file was changed: a chunk does not open") from None

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._at

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        base = {io.SEEK_SET: 0, io.SEEK_CUR: self._at, io.SEEK_END: self._size}[whence]
        target = base + offset
        if target < 0:
            raise OSError("a negative position")
        self._at = target
        return target

    def readinto(self, buffer: Any) -> int:
        view = memoryview(buffer).cast("B")
        done = 0
        while done < len(view) and self._at < self._size:
            index, inside = divmod(self._at, CHUNK)
            if self._cached is None or self._cached[0] != index:
                self._cached = (index, self._open_chunk(index))
            plain = self._cached[1]
            take = min(len(view) - done, len(plain) - inside)
            view[done:done + take] = plain[inside:inside + take]
            done += take
            self._at += take
        return done

    def close(self) -> None:
        self._cached = None
        self._file.close()
        super().close()
