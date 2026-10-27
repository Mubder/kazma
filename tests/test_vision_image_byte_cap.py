"""A source image has one byte cap, and a local file is never read past it
(audit 2026-10-26 AUD-109).

``analyze_image`` capped a URL download at ``MAX_DOWNLOAD_BYTES`` (50 MB,
streamed and counted) but a local file at ``2 * MAX_IMAGE_BYTES`` (40 MB),
checked with a ``stat`` and then read whole with ``read_bytes()``: two limits
for one input, and a file that grew after the stat was read past its cap.
Both now use ``MAX_DOWNLOAD_BYTES``, and the local read stops one byte past it.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from kazma_core.tools import vision_analyze

PNG_HEAD = b"\x89PNG\r\n\x1a\n"


def _image(tmp_path: Path, size: int) -> Path:
    path = tmp_path / "photo.png"
    path.write_bytes(PNG_HEAD + b"\0" * (size - len(PNG_HEAD)))
    return path


def test_one_cap_for_a_file_and_a_download() -> None:
    assert vision_analyze.MAX_DOWNLOAD_BYTES == 2 * vision_analyze.MAX_IMAGE_BYTES


def test_a_local_file_over_the_download_cap_is_refused(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(vision_analyze, "MAX_DOWNLOAD_BYTES", 100)
    monkeypatch.setattr(vision_analyze, "MAX_IMAGE_BYTES", 1000)
    with pytest.raises(ValueError, match="too large"):
        vision_analyze._load_local_image(_image(tmp_path, 150))


def _stat_before_it_grew(monkeypatch, path: Path, size: int) -> None:
    """``stat`` reports *size* for *path*: the file as it was a moment ago."""
    real_stat = Path.stat

    def _stat(self, *args, **kwargs):
        st = real_stat(self, *args, **kwargs)
        if self == path:
            return os.stat_result((st.st_mode, st.st_ino, st.st_dev, st.st_nlink,
                                   st.st_uid, st.st_gid, size, int(st.st_atime),
                                   int(st.st_mtime), int(st.st_ctime)))
        return st

    monkeypatch.setattr(Path, "stat", _stat)


def test_a_file_that_grew_after_the_stat_is_not_read_past_the_cap(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(vision_analyze, "MAX_DOWNLOAD_BYTES", 100)
    path = _image(tmp_path, 500)
    _stat_before_it_grew(monkeypatch, path, 50)
    with pytest.raises(ValueError, match="too large"):
        vision_analyze._load_local_image(path)


def test_the_old_read_took_the_whole_file(tmp_path, monkeypatch) -> None:
    """Negative control: the stat check then ``read_bytes()``, as it was."""
    cap = 100

    def _old_load(path: Path) -> bytes:
        if path.stat().st_size > cap:
            raise ValueError("Image file too large")
        return path.read_bytes()

    path = _image(tmp_path, 500)
    _stat_before_it_grew(monkeypatch, path, 50)
    assert len(_old_load(path)) == 500


def test_an_image_under_the_cap_still_loads(tmp_path) -> None:
    raw, mime = vision_analyze._load_local_image(_image(tmp_path, 64))
    assert len(raw) == 64 and mime == "image/png"
