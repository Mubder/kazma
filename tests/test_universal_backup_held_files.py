"""The universal backup leaves out what the running server holds locked.

Live 2026-10-15: every backup logged ``[universal-backup] asset copy failed
.runtime-writer.lock: [Errno 13] Permission denied``. The file is the
server's writer fence (``runtime_writer.RuntimeWriterLease``): the server
holds a lock on it for as long as it runs, so the copy fails on Windows, and
a copy restores nothing (ownership is taken again at start). The registry
says which data-dir entries a backup keeps out (``DataDirEntry.backed_up``);
the backup reads it. Negative control: with nothing kept out, the held file
is attempted again (an error on Windows, a pointless copy elsewhere).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from kazma_core.backup import universal
from kazma_core.runtime_writer import RuntimeWriterLease
from kazma_core.store_registry import DATA_DIR_ENTRIES


def _assets_with_the_lock_held(tmp_path: Path) -> list[dict]:
    data = tmp_path / "data"
    (data / "attachments").mkdir(parents=True)
    (data / "attachments" / "note.txt").write_text("kept", encoding="utf-8")
    (data / "install_id").write_text("install-x", encoding="utf-8")
    lease = RuntimeWriterLease(data)
    lease.acquire()
    try:
        return universal._copy_assets(data, tmp_path / "backup" / "assets")
    finally:
        lease.release()


def test_the_held_writer_lock_is_not_copied(tmp_path: Path) -> None:
    copied = _assets_with_the_lock_held(tmp_path)
    paths = {row["path"] for row in copied}
    assert ".runtime-writer.lock" not in paths, copied
    assert not [row for row in copied if row["type"] == "error"], copied
    assert {"attachments", "install_id"} <= paths, copied


def test_every_entry_kept_out_of_backups_says_why() -> None:
    for name, entry in DATA_DIR_ENTRIES.items():
        if not entry.backed_up:
            assert entry.migration != "bundle" and entry.reason, (name, entry)


def test_negative_control_without_the_rule_the_held_file_is_attempted(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import dataclasses

    entry = DATA_DIR_ENTRIES[".runtime-writer.lock"]
    monkeypatch.setitem(DATA_DIR_ENTRIES, ".runtime-writer.lock", dataclasses.replace(entry, backed_up=True))
    copied = _assets_with_the_lock_held(tmp_path)
    assert ".runtime-writer.lock" in {row["path"] for row in copied}, copied
