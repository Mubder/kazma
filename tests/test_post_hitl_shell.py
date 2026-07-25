"""Post-HITL shell hardening helpers."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from kazma_core.safety.post_hitl import (
    production_archive_allowed,
    resolve_shell_binary,
    restricted_child_env,
    shell_strict_mode,
    system_path_dirs,
)


def test_shell_strict_defaults_on_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAZMA_PRODUCTION", "1")
    monkeypatch.delenv("KAZMA_SHELL_STRICT", raising=False)
    assert shell_strict_mode() is True


def test_shell_strict_opt_out(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAZMA_PRODUCTION", "1")
    monkeypatch.setenv("KAZMA_SHELL_STRICT", "0")
    assert shell_strict_mode() is False


def test_restricted_env_no_api_keys(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("KAZMA_SHELL_STRICT", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret")
    env = restricted_child_env(cwd=str(tmp_path))
    assert "OPENAI_API_KEY" not in env
    assert env.get("HOME") == str(tmp_path)
    assert env.get("PATH")


def test_resolve_rejects_outside_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("KAZMA_SHELL_STRICT", "1")
    evil = tmp_path / "evil-git"
    evil.write_text("#!/bin/sh\necho pwned\n", encoding="utf-8")
    # Absolute path outside restricted PATH dirs
    found = resolve_shell_binary(str(evil), restricted_path="/usr/bin" + os.pathsep + "/bin")
    assert found is None


def test_production_archive_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAZMA_PRODUCTION", "1")
    monkeypatch.delenv("KAZMA_SHELL_ALLOW_ARCHIVE", raising=False)
    monkeypatch.delenv("KAZMA_SHELL_STRICT", raising=False)
    assert production_archive_allowed() is False
