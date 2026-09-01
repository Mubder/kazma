"""Path rewrite separator variants (audit M-10)."""

from __future__ import annotations

from kazma_core.migration.path_rewrite import PathMap, rewrite_text


def test_linux_source_rewrites_stored_backslash_variant() -> None:
    pm = PathMap()
    pm.add("/home/user/kazma", r"C:\Users\user\kazma")
    text = r"open \home\user\kazma\workspace\file.py"
    out, n = rewrite_text(text, pm)
    assert n >= 1
    assert r"\home\user\kazma" not in out
    assert "C:\\Users\\user\\kazma" in out or "C:/Users/user/kazma" in out


def test_forward_slash_linux_source_still_rewrites() -> None:
    pm = PathMap()
    pm.add("/home/user/kazma", "/opt/kazma")
    out, n = rewrite_text("root=/home/user/kazma/data", pm)
    assert n == 1
    assert out == "root=/opt/kazma/data"
