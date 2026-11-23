"""A zip sealed with a password: WinZip AES-256 (AE-2), the encrypted zip
most archive tools read (Spaces S1-27, decision N10: a person's export of
their own space is sealed with a password they choose).

7-Zip, WinZip, WinRAR, Keka, The Unarchiver and libarchive's ``bsdtar``
open it with the password; the zip tools built into Windows Explorer and
macOS's Archive Utility read no AES zip, which the page says.

The format fixes its key derivation (PBKDF2-HMAC-SHA1, 1,000 rounds) and
its authentication (HMAC-SHA1, 10 bytes, over the encrypted data), so a
wrong password or a changed byte is refused when the file is opened. A
derivation that fast is quick to guess against: the strength is the
password's, at least :data:`MIN_PASSWORD` characters.

Every entry is deflated, then encrypted with AES-256 in WinZip's counter
mode (a little-endian 128-bit counter from 1). Sizes past 4 GiB use ZIP64.
Only the ``cryptography`` package Kazma already depends on; no new one.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import struct
import zipfile
import zlib
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

__all__ = ["MIN_PASSWORD", "SealedZipRefused", "seal", "unseal"]

#: The shortest password a sealed zip takes.
MIN_PASSWORD = 12

_KEY_BYTES = 32  # AES-256
_SALT_BYTES = 16  # the salt length WinZip fixes for AES-256
_ROUNDS = 1000  # fixed by the format
_MAC_BYTES = 10
_CHUNK = 1 << 16
_LIMIT = 0xFFFFFFFF  # from this a size or an offset is ZIP64's
_MARK = 0xFFFFFFFF  # what a header holds for a size or an offset kept in ZIP64's field
_METHOD_AES = 99
_VERSION = 51  # "version needed" of a WinZip AES entry
_FLAGS = 0x0001 | 0x0800  # encrypted; the name is UTF-8
#: The AES extra field: id 0x9901, 7 bytes, AE-2, "AE", AES-256, deflate.
_AES_EXTRA = struct.pack("<HHH2sBH", 0x9901, 7, 2, b"AE", 3, zipfile.ZIP_DEFLATED)


class _Counter:
    """WinZip's AES counter mode: block *i* (from 1) is AES of *i* as 16
    little-endian bytes, XORed with the data. ``cryptography``'s CTR counts
    big-endian, so the blocks are made here and encrypted as ECB."""

    def __init__(self, key: bytes) -> None:
        self._aes = Cipher(algorithms.AES(key), modes.ECB()).encryptor()  # noqa: S305 -- the counter blocks of WinZip's CTR, not ECB of the data
        self._next = 1
        self._left = b""

    def apply(self, data: bytes) -> bytes:
        if not data:
            return b""
        stream = self._left
        need = len(data) - len(stream)
        if need > 0:
            blocks = -(-need // 16)
            counters = b"".join((self._next + i).to_bytes(16, "little") for i in range(blocks))
            self._next += blocks
            stream += self._aes.update(counters)
        size = len(data)
        self._left = stream[size:]
        mixed = int.from_bytes(data, "big") ^ int.from_bytes(stream[:size], "big")
        return mixed.to_bytes(size, "big")


def _dos_time(date_time: tuple[int, ...]) -> tuple[int, int]:
    year, month, day, hour, minute, second = date_time[:6]
    year = min(max(year, 1980), 2107)
    return (hour << 11 | minute << 5 | second // 2), ((year - 1980) << 9 | month << 5 | day)


def _local_header(name: bytes, when: tuple[int, int], sizes: tuple[int, int], zip64: bool) -> bytes:
    """A local file header; *sizes* is (compressed, uncompressed)."""
    packed, original = sizes
    extra = _AES_EXTRA
    if zip64:
        extra += struct.pack("<HHQQ", 0x0001, 16, original, packed)
        packed = original = _MARK
    return struct.pack(
        "<4s2B4HL2L2H", b"PK\x03\x04", _VERSION, 0, _FLAGS, _METHOD_AES, when[0], when[1],
        0, packed, original, len(name), len(extra),  # AE-2 leaves the CRC at 0
    ) + name + extra


def _central_entry(name: bytes, when: tuple[int, int], packed: int, original: int, offset: int) -> bytes:
    wide = [value for value in (original, packed, offset) if value >= _LIMIT]
    extra = _AES_EXTRA
    if wide:
        extra += struct.pack(f"<HH{len(wide)}Q", 0x0001, 8 * len(wide), *wide)
    def held(value: int) -> int:
        return _MARK if value >= _LIMIT else value

    return struct.pack(
        "<4s4B4HL2L5H2L", b"PK\x01\x02", _VERSION, 0, _VERSION, 0, _FLAGS, _METHOD_AES,
        when[0], when[1], 0,
        held(packed), held(original), len(name), len(extra), 0, 0, 0,
        0o100600 << 16, held(offset),
    ) + name + extra


def _end(count: int, size: int, offset: int, end_at: int) -> bytes:
    """The end of the central directory, with ZIP64's records when needed."""
    out = b""
    if count >= 0xFFFF or size >= _LIMIT or offset >= _LIMIT:
        out += struct.pack("<4sQ2H2L4Q", b"PK\x06\x06", 44, 45, 45, 0, 0, count, count, size, offset)
        out += struct.pack("<4sLQL", b"PK\x06\x07", 0, end_at, 1)
        count, size, offset = 0xFFFF, _MARK, _MARK
    return out + struct.pack("<4s4H2LH", b"PK\x05\x06", 0, 0, count, count, size, offset, 0)


def _keys(password: str, salt: bytes) -> tuple[bytes, bytes, bytes]:
    derived = hashlib.pbkdf2_hmac("sha1", password.encode("utf-8"), salt, _ROUNDS,  # nosec B324 - the derivation WinZip AES fixes
                                  dklen=2 * _KEY_BYTES + 2)
    return derived[:_KEY_BYTES], derived[_KEY_BYTES:2 * _KEY_BYTES], derived[2 * _KEY_BYTES:]


def seal(source: Path, target: Path, password: str) -> int:
    """Write *target*: every file entry of the zip *source*, deflated and
    sealed with *password*. Written under a ``.partial`` name and renamed
    when complete. Returns the number of entries.

    Raises ``ValueError`` for a password shorter than :data:`MIN_PASSWORD`."""
    if len(password or "") < MIN_PASSWORD:
        raise ValueError(f"a sealed zip's password is at least {MIN_PASSWORD} characters")
    partial = target.with_name(target.name + ".partial")
    central: list[bytes] = []
    try:
        with zipfile.ZipFile(source) as plain, open(partial, "wb") as out:
            for info in plain.infolist():
                if info.is_dir():
                    continue
                name = info.filename.encode("utf-8")
                when = _dos_time(info.date_time)
                offset = out.tell()
                zip64 = info.file_size * 1.05 + 64 > _LIMIT
                out.write(_local_header(name, when, (0, 0), zip64))
                start = out.tell()
                salt = os.urandom(_SALT_BYTES)
                key, auth, verifier = _keys(password, salt)
                out.write(salt + verifier)
                counter = _Counter(key)
                mac = hmac.new(auth, digestmod=hashlib.sha1)  # nosec B324 - the authentication WinZip AES fixes
                deflate = zlib.compressobj(6, zlib.DEFLATED, -15)
                with plain.open(info) as member:
                    while True:
                        chunk = member.read(_CHUNK)
                        piece = deflate.compress(chunk) if chunk else deflate.flush()
                        sealed = counter.apply(piece)
                        mac.update(sealed)
                        out.write(sealed)
                        if not chunk:
                            break
                out.write(mac.digest()[:_MAC_BYTES])
                packed = out.tell() - start
                if not zip64 and (packed >= _LIMIT or info.file_size >= _LIMIT):
                    raise ValueError(f"{info.filename} outgrew its header")
                end_of_entry = out.tell()
                out.seek(offset)
                out.write(_local_header(name, when, (packed, info.file_size), zip64))
                out.seek(end_of_entry)
                central.append(_central_entry(name, when, packed, info.file_size, offset))
            directory_at = out.tell()
            for entry in central:
                out.write(entry)
            directory_size = out.tell() - directory_at
            out.write(_end(len(central), directory_size, directory_at, out.tell()))
        partial.replace(target)
    finally:
        if partial.exists():
            partial.unlink()
    return len(central)


class SealedZipRefused(ValueError):
    """A sealed zip was not opened (Spaces S4-06): ``code`` is
    ``wrong_password``, ``changed`` (an entry's authentication failed: the
    file was altered or cut short), ``not_sealed`` (not a zip this module
    sealed) or ``too_big`` (past the caller's bound). Nothing usable was
    written."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _data_start(handle: Any, info: zipfile.ZipInfo) -> int:
    """Where *info*'s sealed bytes start: after its local header, read from
    the file (its extra field may differ from the central directory's)."""
    handle.seek(info.header_offset)
    header = handle.read(30)
    if len(header) != 30 or header[:4] != b"PK\x03\x04":
        raise SealedZipRefused("not_sealed", f"{info.filename}: no local header")
    name_len, extra_len = struct.unpack("<2H", header[26:30])
    return info.header_offset + 30 + name_len + extra_len


def _aes_method(info: zipfile.ZipInfo) -> int:
    """The compression inside a WinZip AES entry (its 0x9901 extra field)."""
    extra = info.extra
    at = 0
    while at + 4 <= len(extra):
        kind, size = struct.unpack("<HH", extra[at:at + 4])
        body = extra[at + 4:at + 4 + size]
        if kind == 0x9901 and size == 7:
            _version, vendor, strength, method = struct.unpack("<H2sBH", body)
            if vendor != b"AE" or strength != 3:
                raise SealedZipRefused("not_sealed", f"{info.filename}: not AES-256")
            return int(method)
        at += 4 + size
    raise SealedZipRefused("not_sealed", f"{info.filename}: not a WinZip AES entry")


def unseal(source: Path, target: Path, password: str, *, max_bytes: int, max_entries: int = 200_000) -> int:
    """Write *target*, a plain zip of every entry of the sealed zip *source*
    (as :func:`seal` writes it), each entry checked before it is kept: the
    password's verifier, then the entry's authentication over every sealed
    byte. Written under a ``.partial`` name and renamed when complete; a
    refusal (:class:`SealedZipRefused`) leaves no *target*. Bounded: past
    *max_bytes* unpacked in all, or *max_entries*, ``too_big``. Returns the
    number of entries.

    The names are copied as they are: the caller reads entries by name and
    never writes one to a path it did not choose."""
    partial = target.with_name(target.name + ".partial")
    total = 0
    count = 0
    try:
        try:
            sealed = zipfile.ZipFile(source)
        except (zipfile.BadZipFile, OSError) as exc:
            raise SealedZipRefused("not_sealed", "not a zip") from exc
        with sealed, open(source, "rb") as handle, zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED) as plain:
            entries = [info for info in sealed.infolist() if not info.is_dir()]
            if len(entries) > max_entries:
                raise SealedZipRefused("too_big", f"more than {max_entries} entries")
            for info in entries:
                if info.compress_type != _METHOD_AES:
                    raise SealedZipRefused("not_sealed", f"{info.filename}: not sealed")
                method = _aes_method(info)
                if method not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                    raise SealedZipRefused("not_sealed", f"{info.filename}: an unknown compression")
                sealed_size = info.compress_size - _SALT_BYTES - 2 - _MAC_BYTES
                if sealed_size < 0:
                    raise SealedZipRefused("changed", f"{info.filename}: cut short")
                handle.seek(_data_start(handle, info))
                salt = handle.read(_SALT_BYTES)
                verifier = handle.read(2)
                key, auth, expected = _keys(password, salt)
                if not hmac.compare_digest(verifier, expected):
                    raise SealedZipRefused("wrong_password", "the password does not open this file")
                counter = _Counter(key)
                mac = hmac.new(auth, digestmod=hashlib.sha1)  # nosec B324 - the authentication WinZip AES fixes
                inflate = zlib.decompressobj(-15) if method == zipfile.ZIP_DEFLATED else None
                left = sealed_size
                with plain.open(info.filename, "w", force_zip64=True) as out:
                    while left:
                        piece = handle.read(min(_CHUNK, left))
                        if not piece:
                            raise SealedZipRefused("changed", f"{info.filename}: cut short")
                        left -= len(piece)
                        mac.update(piece)
                        clear = counter.apply(piece)
                        if inflate is not None:
                            # Bounded per step: a small entry cannot expand
                            # past what is left of the bound (a zip bomb).
                            clear = inflate.decompress(clear, max(1, max_bytes - total + 1))
                            if inflate.unconsumed_tail:
                                raise SealedZipRefused("too_big", f"more than {max_bytes} bytes unpacked")
                        total += len(clear)
                        if total > max_bytes:
                            raise SealedZipRefused("too_big", f"more than {max_bytes} bytes unpacked")
                        out.write(clear)
                    if inflate is not None:
                        tail = inflate.flush()
                        total += len(tail)
                        if total > max_bytes:
                            raise SealedZipRefused("too_big", f"more than {max_bytes} bytes unpacked")
                        out.write(tail)
                    stored_mac = handle.read(_MAC_BYTES)
                    if not hmac.compare_digest(stored_mac, mac.digest()[:_MAC_BYTES]):
                        raise SealedZipRefused("changed", f"{info.filename}: altered")
                count += 1
        partial.replace(target)
    except zlib.error as exc:
        raise SealedZipRefused("changed", "an entry does not unpack") from exc
    finally:
        if partial.exists():
            partial.unlink()
    return count
