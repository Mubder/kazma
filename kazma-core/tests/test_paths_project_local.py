"""paths.py project-local home + data_dir overrides."""

from __future__ import annotations

from pathlib import Path


def test_data_dir_env_override(tmp_path: Path, monkeypatch) -> None:
    from kazma_core import paths as p

    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path / "custom-data"))
    # Clear any cached project root side effects by calling data_dir fresh
    d = p.data_dir()
    assert d == (tmp_path / "custom-data").resolve()
    assert d.is_dir()
    monkeypatch.delenv("KAZMA_DATA_DIR", raising=False)


def test_user_home_env_override(tmp_path: Path, monkeypatch) -> None:
    from kazma_core import paths as p

    monkeypatch.setenv("KAZMA_USER_HOME", str(tmp_path / "my-home"))
    h = p.user_home()
    assert h == (tmp_path / "my-home").resolve()
    assert h.is_dir()
    monkeypatch.delenv("KAZMA_USER_HOME", raising=False)


def test_agent_skills_and_extras_under_user_home(tmp_path: Path, monkeypatch) -> None:
    from kazma_core import paths as p

    monkeypatch.setenv("KAZMA_USER_HOME", str(tmp_path / "home"))
    assert p.agent_skills_dir().parent == p.user_home()
    assert p.installed_extras_path().parent == p.user_home()
    assert p.preferences_path().parent == p.user_home()
    monkeypatch.delenv("KAZMA_USER_HOME", raising=False)


def test_legacy_user_home_is_tilde_kazma() -> None:
    from kazma_core.paths import legacy_user_home

    assert legacy_user_home() == Path.home() / ".kazma"


def test_merge_legacy_hub_copies_when_target_empty(tmp_path: Path, monkeypatch) -> None:
    from kazma_core import paths as p

    legacy = tmp_path / "legacy"
    project = tmp_path / "project"
    (legacy / "hub").mkdir(parents=True)
    (legacy / "hub" / "registry.db").write_bytes(b"legacy-db")
    project.mkdir()

    monkeypatch.setattr(p, "legacy_user_home", lambda: legacy)
    monkeypatch.setattr(p, "user_home", lambda: project)

    assert p.merge_legacy_hub_if_empty() is True
    assert (project / "hub" / "registry.db").read_bytes() == b"legacy-db"
    # Second call: target non-empty → no-op
    assert p.merge_legacy_hub_if_empty() is False
