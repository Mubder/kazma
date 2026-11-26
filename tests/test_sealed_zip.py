"""A zip sealed with a password (Spaces S1-27, decision N10).

Read back by the tools a person opens it with: 7-Zip and libarchive's
``bsdtar`` (Windows' own ``tar.exe`` is bsdtar). Each must give back every
entry byte for byte with the password and refuse it without, including the
ZIP64 layout (sizes and offsets past 4 GiB; the limit is lowered here so a
small file takes that path).

Negative control: the counter of ``cryptography``'s CTR (big-endian) in
place of WinZip's (little-endian) writes a zip every tool refuses -- the
tools are checking the format, not taking whatever they are given.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from kazma_core.security import sealed_zip

PASSWORD = "a-long-password-1"


def _seven_zip() -> str | None:
    found = shutil.which("7z") or shutil.which("7za")
    if found:
        return found
    standard = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "7-Zip" / "7z.exe"
    return str(standard) if standard.is_file() else None


def _bsdtar() -> str | None:
    found = shutil.which("bsdtar")
    if found:
        return found
    if sys.platform == "win32":
        system = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "tar.exe"
        return str(system) if system.is_file() else None
    return None


def _openers() -> list[tuple[str, str]]:
    found = [("7z", path) for path in [_seven_zip()] if path]
    found += [("bsdtar", path) for path in [_bsdtar()] if path]
    return found


def _extract(tool: tuple[str, str], archive: Path, out: Path, password: str) -> bool:
    kind, path = tool
    out.mkdir(parents=True, exist_ok=True)
    if kind == "7z":
        argv = [path, "x", "-y", f"-p{password}", f"-o{out}", str(archive)]
    else:
        argv = [path, "-xf", str(archive), "-C", str(out), "--passphrase", password]
    done = subprocess.run(argv, capture_output=True, stdin=subprocess.DEVNULL, timeout=120, check=False)
    return done.returncode == 0


def _plain(tmp_path: Path) -> tuple[Path, dict[str, bytes]]:
    entries = {
        "stores/memory_state.db/episodes.jsonl": ("hello word-\u00f1-\u0639\u0631\u0628\u064a\n" * 2000).encode(),
        "empty.txt": b"",
        "files/files/random.bin": os.urandom(150_000),
        "manifest.json": b'{"space": "s1"}',
    }
    plain = tmp_path / "plain.zip"
    with zipfile.ZipFile(plain, "w", compression=zipfile.ZIP_DEFLATED) as zipped:
        for name, data in entries.items():
            zipped.writestr(name, data)
    return plain, entries


def _assert_opens(tool: tuple[str, str], archive: Path, entries: dict[str, bytes], tmp_path: Path) -> None:
    out = tmp_path / f"out-{tool[0]}"
    assert _extract(tool, archive, out, PASSWORD), tool
    for name, data in entries.items():
        assert (out / name).read_bytes() == data, (tool, name)
    wrong = tmp_path / f"wrong-{tool[0]}"
    opened = _extract(tool, archive, wrong, "not-the-password-at-all")
    got = {name: (wrong / name).read_bytes() for name in entries if (wrong / name).is_file()}
    assert not opened or got != entries, tool


@pytest.fixture
def tools():
    found = _openers()
    if not found:
        pytest.skip("neither 7-Zip nor bsdtar is installed")
    return found


def test_the_tools_open_it_with_the_password_and_not_without(tmp_path, tools) -> None:
    plain, entries = _plain(tmp_path)
    sealed = tmp_path / "sealed.zip"
    assert sealed_zip.seal(plain, sealed, PASSWORD) == len(entries)
    # Every entry is sealed: Python's zipfile lists them and reads none.
    with zipfile.ZipFile(sealed) as zipped:
        assert sorted(zipped.namelist()) == sorted(entries)
        assert {info.compress_type for info in zipped.infolist()} == {99}
        with pytest.raises((RuntimeError, NotImplementedError)):
            zipped.read("manifest.json")
    for tool in tools:
        _assert_opens(tool, sealed, entries, tmp_path)


def test_zip64_sizes_and_offsets_open_too(tmp_path, tools, monkeypatch) -> None:
    monkeypatch.setattr(sealed_zip, "_LIMIT", 1000)
    plain, entries = _plain(tmp_path)
    sealed = tmp_path / "sealed64.zip"
    sealed_zip.seal(plain, sealed, PASSWORD)
    data = sealed.read_bytes()
    assert b"PK\x06\x06" in data and b"PK\x06\x07" in data  # the ZIP64 end records
    for tool in tools:
        _assert_opens(tool, sealed, entries, tmp_path)


def test_a_short_password_is_refused_and_nothing_is_left(tmp_path) -> None:
    folder = tmp_path / "short"
    folder.mkdir()
    plain, _entries = _plain(folder)
    with pytest.raises(ValueError):
        sealed_zip.seal(plain, folder / "short.zip", "short")
    assert sorted(p.name for p in folder.iterdir()) == ["plain.zip"]


def test_control_a_big_endian_counter_writes_a_zip_no_tool_opens(tmp_path, tools, monkeypatch) -> None:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    class _BigEndian:
        def __init__(self, key: bytes) -> None:
            self._ctr = Cipher(algorithms.AES(key), modes.CTR((1).to_bytes(16, "big"))).encryptor()

        def apply(self, data: bytes) -> bytes:
            return self._ctr.update(data)

    monkeypatch.setattr(sealed_zip, "_Counter", _BigEndian)
    plain, entries = _plain(tmp_path)
    sealed = tmp_path / "wrong-counter.zip"
    sealed_zip.seal(plain, sealed, PASSWORD)
    for tool in tools:
        out = tmp_path / f"control-{tool[0]}"
        opened = _extract(tool, sealed, out, PASSWORD)
        got = {name: (out / name).read_bytes() for name in entries if (out / name).is_file()}
        assert not opened or got != entries, tool


def _sealed_bodies(archive: Path) -> list[bytes]:
    """Each entry's encrypted data (between its salt and verifier and its
    MAC), read from its local header."""
    raw = archive.read_bytes()
    bodies = []
    with zipfile.ZipFile(archive) as zipped:
        for info in zipped.infolist():
            at = info.header_offset
            name_len, extra_len = int.from_bytes(raw[at + 26:at + 28], "little"), int.from_bytes(raw[at + 28:at + 30], "little")
            start = at + 30 + name_len + extra_len + 16 + 2  # the salt (AES-256) and the verifier
            bodies.append(raw[start:start + info.compress_size - 16 - 2 - 10])
    return bodies


def test_no_two_entries_share_a_keystream(tmp_path, monkeypatch) -> None:
    """The ECB in ``_Counter`` makes WinZip's counter-mode keystream: AES of
    unique counters under one entry's key (CodeQL py/weak-cryptographic-algorithm,
    judged correct 2026-11-26). It is safe only while no two entries share a
    key: each takes a fresh salt. Two entries of the same bytes seal apart.

    Negative control: with one salt for every entry, the same bytes seal to
    the same body -- the reuse this test is there to see."""
    plain = tmp_path / "twins.zip"
    with zipfile.ZipFile(plain, "w") as zipped:
        zipped.writestr("one.txt", b"the same words " * 500)
        zipped.writestr("two.txt", b"the same words " * 500)
    sealed_zip.seal(plain, tmp_path / "sealed.zip", PASSWORD)
    first, second = _sealed_bodies(tmp_path / "sealed.zip")
    assert first and len(first) == len(second) and first != second

    monkeypatch.setattr(sealed_zip.os, "urandom", lambda n: b"\x07" * n)
    sealed_zip.seal(plain, tmp_path / "one-salt.zip", PASSWORD)
    first, second = _sealed_bodies(tmp_path / "one-salt.zip")
    assert first == second, "the control: one salt for both entries reuses the keystream"
