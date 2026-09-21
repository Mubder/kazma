"""The per-turn file-read cache must never serve bytes that changed.

``file_read`` dedupes re-reads within a turn: the model re-read the same
file up to 9x per task (audit 2026-08-15), so a repeat read returns the
cached text under an "[ALREADY READ THIS TURN]" banner instead of hitting
disk and re-injecting the whole file into context. Worth keeping.

The bug (probe run, 2026-09-21) was its lifetime. The entry lived until the
end of the turn and nothing invalidated it, so *read -> write -> read* inside
one turn replayed the PRE-write content: ``file_apply_patch`` landed three
lines, the verifying ``file_read`` showed the two-line original, and only a
different offset/limit shook it loose. Read-after-write is precisely how an
agent checks its own work, so the cache lied at the one moment it mattered —
and it lied with a banner asserting the content was current.

The fix stamps each entry with the file's on-disk identity (mtime_ns, size)
and revalidates on every hit. The alternative — an invalidate hook called by
the five mutating tools (``file_write``, ``file_append``,
``file_apply_patch``, ``file_apply_patch_set``, ``file_delete``) — was
rejected: it is only as good as the next tool that remembers to call it, and
it is blind to writers that are not Kazma tools at all. A ``shell_exec``
redirect, an MCP filesystem server, a sibling swarm worker and the user's
own editor all mutate files during a turn and none of them can call a hook.
``test_out_of_band_write_is_seen`` is that case, and it is the one a hook
implementation would fail.

Sizes differ between versions in every test here on purpose. A stamp is
(mtime_ns, size), and a second write landing inside the same filesystem
mtime tick with an identical size would not move it. That is a real (if
tiny) residual gap in the fix; what it must not become is a flaky test.

``test_unchanged_file_still_dedupes`` is the counterweight: "always re-read"
would pass every other test in this file while silently deleting the feature
and restoring the 9x context bloat the cache was built to stop.
"""

from __future__ import annotations

import importlib
import os
from pathlib import Path

import pytest

# The tools/__init__ package re-exports the file_read FUNCTION under the
# module's own attribute name, so ``import kazma_core.tools.file_read as fr``
# binds the function via getattr. import_module gets the actual module.
fr = importlib.import_module("kazma_core.tools.file_read")

ALREADY_READ = "ALREADY READ THIS TURN"


class _AllowAll:
    allowed = True


@pytest.fixture
def allow_all_paths(monkeypatch):
    """Neutralise path policy everywhere it is bound.

    ``file_read`` imports ``check_path_access`` inside the function, but
    ``file_write`` and ``file_apply_patch`` bind it at module import — so
    patching only ``path_policy`` would leave the writers gated.
    """
    from kazma_core.tools import file_apply_patch as fap
    from kazma_core.tools import file_write as fw
    from kazma_core.workspace import path_policy

    allow = lambda p, m, *a, **k: _AllowAll()  # noqa: E731
    for mod in (path_policy, fw, fap):
        monkeypatch.setattr(mod, "check_path_access", allow, raising=False)


@pytest.fixture(autouse=True)
def cold_cache():
    """Cache is process-global; start and end cold."""
    fr.clear_read_cache()
    yield
    fr.clear_read_cache()


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    from kazma_core.tools.file_write import configure_workspace

    configure_workspace(workspace=str(tmp_path), allow_absolute=True)
    yield tmp_path
    configure_workspace(workspace=None, allow_absolute=False)


async def _read(p: Path) -> str:
    return await fr.file_read(str(p))


@pytest.mark.asyncio
async def test_file_write_then_read_sees_new_content(workspace, allow_all_paths):
    """The incident, via file_write."""
    from kazma_core.tools.file_write import file_write

    target = workspace / "note.txt"
    target.write_text("one\ntwo\n", encoding="utf-8")

    first = await _read(target)
    assert "one" in first and "three" not in first

    await file_write(str(target), "one\ntwo\nthree\nfour\n")

    second = await _read(target)
    assert "three" in second, (
        "file_read served the pre-write content after file_write. A turn that "
        "writes and then reads back to verify is being told its own change did "
        "not land."
    )
    assert ALREADY_READ not in second, (
        "content changed, yet the read was banner-labelled as identical to the "
        "previous one — worse than a stale read, because it asserts currency"
    )


@pytest.mark.asyncio
async def test_file_apply_patch_then_read_sees_new_content(workspace, allow_all_paths):
    """The incident exactly as the probe hit it: patch, then verify."""
    from kazma_core.tools.file_apply_patch import file_apply_patch

    target = workspace / "probe.txt"
    target.write_text("alpha\nbeta\n", encoding="utf-8")

    assert "alpha" in await _read(target)

    res = await file_apply_patch(
        str(target), old_string="beta\n", new_string="beta\ngamma inserted\n"
    )
    assert "Error" not in res, res
    assert "gamma inserted" in target.read_text(encoding="utf-8")

    after = await _read(target)
    assert "gamma inserted" in after, (
        "the patch landed on disk but file_read replayed the cached pre-patch "
        "copy — this is the reported stale read"
    )


@pytest.mark.asyncio
async def test_out_of_band_write_is_seen(workspace, allow_all_paths):
    """A writer that is not a Kazma tool at all.

    shell_exec redirects, MCP filesystem servers, sibling swarm workers and
    the user's editor all land here. No invalidate-hook design can catch
    this; only validating against the bytes can.
    """
    target = workspace / "external.txt"
    target.write_text("before\n", encoding="utf-8")

    assert "before" in await _read(target)

    target.write_text("after the out-of-band write\n", encoding="utf-8")

    second = await _read(target)
    assert "out-of-band" in second, (
        "file_read cached across a write it did not perform; anything that "
        "edits a file without going through a Kazma tool is invisible to the "
        "cache"
    )


@pytest.mark.asyncio
async def test_deleted_file_does_not_serve_ghost_content(workspace, allow_all_paths):
    """A read after deletion must fail, not replay the corpse."""
    target = workspace / "doomed.txt"
    target.write_text("still here\n", encoding="utf-8")

    assert "still here" in await _read(target)

    target.unlink()

    after = await _read(target)
    assert "still here" not in after, (
        "file_read returned the contents of a deleted file from cache"
    )
    assert ALREADY_READ not in after


@pytest.mark.asyncio
async def test_unchanged_file_still_dedupes(workspace, allow_all_paths):
    """The cache must still work — this is the anti-regression for the fix.

    Disabling the cache passes every other test in this file. It also undoes
    the reason it exists.
    """
    target = workspace / "stable.txt"
    target.write_text("unchanging\n", encoding="utf-8")

    first = await _read(target)
    second = await _read(target)

    assert ALREADY_READ in second, (
        "an untouched file was re-read from disk and re-injected in full; the "
        "dedup cache has been disabled rather than made correct"
    )
    assert "unchanging" in second
    assert ALREADY_READ not in first


@pytest.mark.asyncio
async def test_same_tick_same_length_rewrite_is_caught(workspace, allow_all_paths):
    """The residual the stamp could not see, reproduced deterministically.

    ``(mtime_ns, size)`` cannot distinguish two writes of the SAME length that
    land inside one filesystem mtime tick. In the wild that needs a coincidence;
    here ``os.utime`` forces the mtime back to the exact nanosecond of the first
    write, which is the same collision without waiting for luck.

    Small files carry a full content digest. Larger files carry a sampled
    digest, so a change in one of those windows is still seen.
    """
    target = workspace / "same_tick.txt"
    target.write_text("AAAA\nBBBB\n", encoding="utf-8")
    st = target.stat()

    first = await _read(target)
    assert "AAAA" in first

    # Same LENGTH, different content, and the clock wound back to match.
    target.write_text("CCCC\nDDDD\n", encoding="utf-8")
    os.utime(target, ns=(st.st_atime_ns, st.st_mtime_ns))
    after = target.stat()
    assert (after.st_mtime_ns, after.st_size) == (st.st_mtime_ns, st.st_size), (
        "the collision was not actually reproduced; this test proves nothing"
    )

    second = await _read(target)
    assert "CCCC" in second, (
        "a same-length rewrite inside one mtime tick served the OLD bytes — "
        "the digest is not being consulted"
    )
    assert ALREADY_READ not in second


def test_large_files_sample_the_digest(workspace, allow_all_paths):
    """Above the full-hash bound the stamp still changes when a sample changes.

    The whole file is not re-read. The first window is. A same-tick rewrite
    of those leading bytes must not keep the previous cache entry.
    """
    small = workspace / "small.txt"
    small.write_text("x" * 1000, encoding="utf-8")
    assert fr._stat_stamp(small)[2] is not None, "a small file must be hashed"

    big = workspace / "big.bin"
    payload = bytearray(b"y" * (fr._HASH_MAX_BYTES + 1))
    big.write_bytes(payload)
    before = fr._stat_stamp(big)
    assert before is not None and before[2] is not None

    payload[0] = ord("z")
    big.write_bytes(payload)
    os.utime(big, ns=(big.stat().st_atime_ns, before[0]))
    after = fr._stat_stamp(big)
    assert after is not None
    assert after[0] == before[0]
    assert after[1] == before[1]
    assert after[2] != before[2], (
        "a leading-byte rewrite of a large file kept the same digest"
    )


@pytest.mark.asyncio
async def test_stamp_is_taken_before_the_read(workspace, allow_all_paths):
    """Staleness must be resolved in the safe direction.

    Stamping after the content is read would pair OLD bytes with the NEW
    mtime, and that entry validates forever. Stamping before pairs new bytes
    with an old stamp: one wasted re-read, never a lie. The stored stamp must
    therefore be <= the file's stamp once the read completes.
    """
    target = workspace / "ordering.txt"
    target.write_text("x\n", encoding="utf-8")

    await _read(target)

    (key,) = list(fr._turn_read_cache)
    stored_stamp, _content = fr._turn_read_cache[key]
    assert stored_stamp is not None
    assert stored_stamp == fr._stat_stamp(target), (
        "stamp does not match the file it was taken from"
    )
