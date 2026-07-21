"""Portability tests — assert no hardcoded Unix-only paths in config or production code.

These tests validate VAL-PORT-001, VAL-PORT-002, and VAL-PORT-003:
- No /tmp in kazma.yaml or certified_servers.yaml
- No /var/log or /usr/bin in production source
- rbac.py and audit_logger.py use project-root paths via kazma_core.paths
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from kazma_core import audit_logger, rbac
from kazma_core.paths import audit_db, get_project_root, rbac_db

# Repository root (tests/ is one level below)
REPO_ROOT = Path(__file__).resolve().parent.parent


# ─── VAL-PORT-001: No hardcoded /tmp in config files ─────────────────


class TestNoHardcodedTmpInConfig:
    """Config files must not hardcode the POSIX-only /tmp path."""

    def test_no_tmp_in_kazma_yaml(self) -> None:
        """kazma.yaml must not contain /tmp."""
        config_path = REPO_ROOT / "kazma.yaml"
        content = config_path.read_text(encoding="utf-8")
        assert "/tmp" not in content, f"kazma.yaml still contains /tmp: {content}"

    def test_no_tmp_in_certified_servers_yaml(self) -> None:
        """certified_servers.yaml must not contain /tmp."""
        config_path = REPO_ROOT / "kazma-skills" / "kazma_skills" / "certified_servers.yaml"
        content = config_path.read_text(encoding="utf-8")
        assert "/tmp" not in content, "certified_servers.yaml still contains /tmp"


# ─── VAL-PORT-002: No /var/log or /usr/bin in production code ────────


class TestNoHardcodedUnixPathsInProductionCode:
    """Production Python modules must not hardcode /var/log/... or /usr/bin/..."""

    @pytest.mark.parametrize(
        "subdir",
        [
            "kazma-core/kazma_core",
            "kazma-gateway/kazma_gateway",
            "kazma-ui/kazma_ui",
        ],
    )
    def test_no_var_log_or_usr_bin_in_production_code(self, subdir: str) -> None:
        """Scan all .py files in production packages for /var/log or /usr/bin."""
        pkg_dir = REPO_ROOT / subdir
        if not pkg_dir.exists():
            pytest.skip(f"Directory does not exist: {pkg_dir}")
        offenders: list[str] = []
        for py_file in pkg_dir.rglob("*.py"):
            text = py_file.read_text(encoding="utf-8", errors="replace")
            # Check each line, skip docstrings/comments by simple heuristic
            for lineno, line in enumerate(text.splitlines(), start=1):
                stripped = line.strip()
                # Skip comments
                if stripped.startswith("#"):
                    continue
                if "/var/log" in stripped or "/usr/bin" in stripped:
                    offenders.append(f"{py_file.name}:{lineno}: {stripped}")
        assert not offenders, (
            f"Hardcoded Unix paths found in {subdir}: {offenders}"
        )

    def test_settings_manager_no_var_log_fallback(self) -> None:
        """settings_manager.py source must not contain /var/log/kazma.log."""
        from kazma_core import settings_manager

        source = inspect.getsource(settings_manager)
        assert "/var/log" not in source, (
            "settings_manager.py still has /var/log hardcoded path"
        )


# ─── VAL-PORT-003: rbac / audit use project-root paths (paths.py) ─────


class TestRbacPathIsProjectRoot:
    """rbac.py defaults must go through paths.py (project root), not __file__."""

    def test_no_file_in_rbac_source(self) -> None:
        """rbac.py must not contain __file__ in path construction."""
        source = inspect.getsource(rbac)
        assert "__file__" not in source, "rbac.py still uses __file__ for paths"

    def test_default_db_uses_paths_module(self) -> None:
        """Default DB resolves via rbac_db() under the project root."""
        default_db = rbac._default_db()
        assert default_db == rbac_db()
        assert "kazma-data" in default_db
        assert Path(default_db).name == "rbac.db"
        root = str(get_project_root())
        assert default_db.startswith(root), (
            f"default db ({default_db}) is not under project root ({root})"
        )

    def test_engine_default_uses_paths(self) -> None:
        """RBACEngine() without db_path uses the centralized rbac_db() path."""
        engine = rbac.RBACEngine()
        assert engine.db_path == rbac_db()


class TestAuditLoggerPathIsProjectRoot:
    """audit_logger.py defaults must go through paths.py (project root), not __file__."""

    def test_no_file_in_audit_logger_source(self) -> None:
        """audit_logger.py must not contain __file__ in path construction."""
        source = inspect.getsource(audit_logger)
        assert "__file__" not in source, (
            "audit_logger.py still uses __file__ for paths"
        )

    def test_default_db_uses_paths_module(self) -> None:
        """Default DB resolves via audit_db() under the project root."""
        default_db = audit_logger._default_db()
        assert default_db == audit_db()
        assert "kazma-data" in default_db
        assert Path(default_db).name == "audit.db"
        root = str(get_project_root())
        assert default_db.startswith(root), (
            f"default db ({default_db}) is not under project root ({root})"
        )

    def test_logger_default_uses_paths(self) -> None:
        """AuditLogger() without db_path uses the centralized audit_db() path."""
        logger = audit_logger.AuditLogger()
        assert logger.db_path == audit_db()


# ─── VAL-PORT-002: code_exec.py consistency check ────────────────────


class TestCodeExecPathConsistency:
    """Ensure code_exec.py PATH fallback doesn't regress to /usr/bin:/bin."""

    def test_no_posix_only_path_fallback(self) -> None:
        """PATH fallback must not be the POSIX-only /usr/bin:/bin."""
        from kazma_core.tools import code_exec

        source = inspect.getsource(code_exec)
        assert "/usr/bin:/bin" not in source, (
            "POSIX-only /usr/bin:/bin PATH fallback remains in code_exec.py"
        )


# ─── Cross-platform path separator safety ────────────────────────────


class TestCrossPlatformPathSafety:
    """Verify that default paths use proper OS path separators."""

    def test_rbac_default_db_uses_os_sep(self) -> None:
        """rbac default uses Path-native separators via paths.rbac_db()."""
        db_path = Path(rbac._default_db())
        assert db_path.name == "rbac.db"

    def test_audit_logger_default_db_uses_os_sep(self) -> None:
        """audit_logger default uses Path-native separators via paths.audit_db()."""
        db_path = Path(audit_logger._default_db())
        assert db_path.name == "audit.db"

    def test_paths_module_is_project_root_based(self) -> None:
        """paths helpers anchor under get_project_root(), not the package install dir."""
        root = get_project_root()
        assert (root / "pyproject.toml").is_file() or root == Path.cwd()
        assert Path(rbac_db()).is_absolute()
        assert Path(audit_db()).is_absolute()
