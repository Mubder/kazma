"""The update log keeps no password from a package index's address.

``kazma update`` writes each step's command and whole output to
``.kazma/update.log``. uv names a package index by its address, and an index
the owner configured can carry ``user:password@`` (CodeQL
py/clear-text-storage-sensitive-data, 2026-11-14).
"""

from __future__ import annotations

import subprocess

from kazma_cli import update

SECRET = "idx-pass-8d2f"


def test_the_update_log_masks_index_passwords(monkeypatch, tmp_path):
    log = tmp_path / "update.log"
    monkeypatch.setattr(update, "_update_log", lambda: log)
    out = f"error: Failed to fetch: https://owner:{SECRET}@pypi.internal.example/simple/x/\n"
    update._log_update_step("uv pip install", ["uv", "pip", "install", "x"],
                            subprocess.CompletedProcess([], 1, stdout="", stderr=out))
    text = log.read_text(encoding="utf-8")
    assert SECRET not in text
    assert "https://owner:****@pypi.internal.example/simple/x/" in text


def test_it_masks_what_the_shared_masker_masks():
    from kazma_core.security.url_credentials import mask_urls_in_text

    for text in (f"postgresql://kazma:{SECRET}@db:5432/kazma", f"see https://u:{SECRET}@h/x and more",
                 "no address here", "https://plain.example/path"):
        assert SECRET not in update._without_url_passwords(text)
        assert (SECRET in mask_urls_in_text(text)) is False


def test_control_the_unmasked_entry_held_it():
    assert SECRET in f"https://owner:{SECRET}@pypi.internal.example/simple/x/"
