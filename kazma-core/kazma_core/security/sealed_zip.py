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

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


__all__ = ["MIN_PASSWORD", "seal"]

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
