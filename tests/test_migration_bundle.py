"""Tests for KazmaBundle.verify() — manifest/archive cross-checks.

Covers audit H13: archive members NOT listed in the manifest previously
passed verification silently; the importer would then extract and swap them
onto live databases. verify() must now fail on unknown extras (tamper
signal) and on manifest-listed files missing from the archive (both
directions, regardless of check_hashes).
"""

from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

from kazma_core.migration.bundle import BUNDLE_VERSION, KazmaBundle, Manifest

# ── Bundle factory ──────────────────────────────────────────────────────────


def _write_bundle(
    path: Path,
    *,
    extra_members: list[str] | None = None,
    omit_members: list[str] | None = None,
    corrupt_member: str | None = None,
    keep_hashes_for_omitted: bool = True,
) -> Path:
    """Write a minimal but structurally valid bundle zip.

    Args:
        extra_members: zip members added on top of the hashed file set.
        omit_members: hashed files left OUT of the zip (manifest still lists
            them unless keep_hashes_for_omitted=False).
        corrupt_member: member whose bytes are mutated post-hash.
        keep_hashes_for_omitted: leave omitted members in the manifest hashes.
    """
    files = {
        "meta.env": "KAZMA_VAULT_KEY=\n",
        "config.yaml": "agent:\n  name: x\n",
        "pathmap.json": "{}\n",
        "data/settings.db": b"settings-db-bytes",
        "data/cron.db": b"cron-db-bytes",
    }
    hashes = {
        name: hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()
        for name, data in files.items()
    }
    for name in omit_members or []:
        files.pop(name)
        if not keep_hashes_for_omitted:
            hashes.pop(name)

    if corrupt_member:
        files[corrupt_member] = b"TAMPERED-" + str(files[corrupt_member]).encode()

    manifest = Manifest(
        bundle_version=BUNDLE_VERSION,
        source_workspace_root="/src/kazma",
        source_data_dir="/src/kazma/kazma-data",
        file_hashes=hashes,
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("manifest.json", manifest.to_json())
        for name, content in sorted(files.items()):
            zf.writestr(name, content)
        for name in extra_members or []:
            zf.writestr(name, b"rogue-bytes")
    return path


# ── H13: manifest ↔ archive cross-check ────────────────────────────────────


class TestManifestArchiveCrossCheck:
    def test_legit_bundle_verifies(self, tmp_path: Path) -> None:
        bundle = _write_bundle(tmp_path / "ok.zip")
        report = KazmaBundle(bundle).verify()
        assert report.ok, report.errors
        assert report.file_count == 6  # incl. manifest.json itself
        assert report.errors == []

    def test_unlisted_extra_db_fails_verification(self, tmp_path: Path) -> None:
        """Rogue zip member absent from the manifest is a tamper signal."""
        bundle = _write_bundle(tmp_path / "rogue.zip", extra_members=["data/rogue.db"])
        report = KazmaBundle(bundle).verify()
        assert not report.ok
        assert any(
            "data/rogue.db" in err and "NOT listed" in err for err in report.errors
        ), report.errors

    def test_extra_directory_entries_are_not_flagged(self, tmp_path: Path) -> None:
        """Directory members carry no data — never a tamper signal."""
        bundle = _write_bundle(tmp_path / "dirs.zip")
        with zipfile.ZipFile(bundle, "a") as zf:
            zf.writestr("assets/attachments/", "")  # explicit dir entry
        report = KazmaBundle(bundle).verify()
        assert report.ok, report.errors

    def test_manifest_listed_file_missing_from_archive_fails(self, tmp_path: Path) -> None:
        """Manifest lists it, zip lacks it → failure even without hashing."""
        bundle = _write_bundle(tmp_path / "missing.zip", omit_members=["data/cron.db"])
        assert KazmaBundle(bundle).verify(check_hashes=True).errors
        quick = KazmaBundle(bundle).verify(check_hashes=False)
        assert not quick.ok
        assert any("data/cron.db" in err and "not in the archive" in err for err in quick.errors)

    def test_missing_check_no_longer_gated_on_hashing(self, tmp_path: Path) -> None:
        """keep_hashes_for_omitted=False + omits file → manifest itself shrinks;
        hash path stays silent (nothing listed missing), no extras flagged."""
        bundle = _write_bundle(
            tmp_path / "shrink.zip",
            omit_members=["data/cron.db"],
            keep_hashes_for_omitted=False,
        )
        report = KazmaBundle(bundle).verify()
        assert report.ok, report.errors

    def test_corrupted_hashed_still_detected(self, tmp_path: Path) -> None:
        """Pre-existing behavior keeps working alongside the new checks."""
        bundle = _write_bundle(
            tmp_path / "tampered.zip", corrupt_member="data/settings.db"
        )
        report = KazmaBundle(bundle).verify()
        assert not report.ok
        assert any("hash mismatch" in err for err in report.errors), report.errors
