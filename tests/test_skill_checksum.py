"""Tests for P1-3: skill checksum enforcement (fail-closed + HMAC).

Covers:
    - Unsigned skills load with a warning (backward compat)
    - Signed skills with correct checksum + signature load normally
    - Tampered checksum → SkillLoadError (fail-closed)
    - Invalid signature → SkillLoadError (fail-closed)
    - Verification error → SkillLoadError (no more except: pass)
    - `kazma hub sign` writes checksum + signature to manifest
"""

from __future__ import annotations

import hashlib
import hmac
import os
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml


# ── Helpers ─────────────────────────────────────────────────────────────


def _make_skill(tmp_path: Path, name: str = "test-skill", code: str = "2+2") -> Path:
    """Create a minimal skill directory with manifest + entry point."""
    skill_dir = tmp_path / "skills" / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "main.py").write_text(
        f"class TestSkill:\n"
        f"    def __init__(self):\n        self.value = {code}\n"
    )
    manifest = {
        "name": name,
        "version": "1.0.0",
        "description": "Test skill",
        "author": "tester",
        "license": "MIT",
        "entry_point": "main:TestSkill",
    }
    (skill_dir / "skill_manifest.yaml").write_text(yaml.dump(manifest))
    return skill_dir


def _sign_skill(skill_dir: Path, secret: str = "test-secret") -> None:
    """Sign a skill's main.py by writing checksum + HMAC into the manifest."""
    py_file = skill_dir / "main.py"
    actual_hash = hashlib.sha256(py_file.read_bytes()).hexdigest()
    signature = hmac.new(
        secret.encode(), actual_hash.encode(), hashlib.sha256
    ).hexdigest()
    manifest = yaml.safe_load((skill_dir / "skill_manifest.yaml").read_text(encoding="utf-8"))
    manifest["checksum"] = actual_hash
    manifest["signature"] = signature
    (skill_dir / "skill_manifest.yaml").write_text(yaml.dump(manifest))


# ══════════════════════════════════════════════════════════════════════════
# Tests
# ══════════════════════════════════════════════════════════════════════════


class TestUnsignedSkillBackwardCompat:
    """Unsigned skills (no checksum) should still load — backward compat."""

    @pytest.mark.asyncio
    async def test_unsigned_skill_loads(self, tmp_path: Path) -> None:
        from kazma_core.hub.loader import SkillLoader

        skill_dir = _make_skill(tmp_path)
        loader = SkillLoader(skills_dir=str(tmp_path / "skills"))
        skill = await loader.load_skill("test-skill")
        assert skill.value == 4


class TestSignedSkillVerification:
    """Signed skills with correct checksum + signature load normally."""

    @pytest.mark.asyncio
    async def test_signed_skill_loads(self, tmp_path: Path) -> None:
        from kazma_core.hub.loader import SkillLoader

        skill_dir = _make_skill(tmp_path)
        _sign_skill(skill_dir, secret="test-secret")
        loader = SkillLoader(skills_dir=str(tmp_path / "skills"))

        with patch.dict(os.environ, {"KAZMA_SECRET": "test-secret"}):
            skill = await loader.load_skill("test-skill")
        assert skill.value == 4


class TestTamperDetection:
    """Tampered files with a checksum must fail-closed."""

    @pytest.mark.asyncio
    async def test_tampered_checksum_raises(self, tmp_path: Path) -> None:
        """Modifying main.py after signing → SkillLoadError."""
        from kazma_core.hub.loader import SkillLoadError, SkillLoader

        skill_dir = _make_skill(tmp_path)
        _sign_skill(skill_dir, secret="test-secret")

        # Tamper: modify the file AFTER signing
        (skill_dir / "main.py").write_text(
            "class TestSkill:\n"
            "    def __init__(self):\n        self.value = 999  # tampered\n"
        )

        loader = SkillLoader(skills_dir=str(tmp_path / "skills"))
        with patch.dict(os.environ, {"KAZMA_SECRET": "test-secret"}):
            with pytest.raises(SkillLoadError, match="Checksum mismatch"):
                await loader.load_skill("test-skill")

    @pytest.mark.asyncio
    async def test_wrong_secret_raises(self, tmp_path: Path) -> None:
        """Signature with wrong secret → SkillLoadError."""
        from kazma_core.hub.loader import SkillLoadError, SkillLoader

        skill_dir = _make_skill(tmp_path)
        _sign_skill(skill_dir, secret="correct-secret")

        loader = SkillLoader(skills_dir=str(tmp_path / "skills"))
        with patch.dict(os.environ, {"KAZMA_SECRET": "wrong-secret"}):
            with pytest.raises(SkillLoadError, match="Signature verification failed"):
                await loader.load_skill("test-skill")

    @pytest.mark.asyncio
    async def test_signature_without_secret_raises(self, tmp_path: Path) -> None:
        """Signed skill but KAZMA_SECRET not set → SkillLoadError."""
        from kazma_core.hub.loader import SkillLoadError, SkillLoader

        skill_dir = _make_skill(tmp_path)
        _sign_skill(skill_dir, secret="some-secret")

        loader = SkillLoader(skills_dir=str(tmp_path / "skills"))
        # Ensure KAZMA_SECRET is not set
        env = {k: v for k, v in os.environ.items() if k != "KAZMA_SECRET"}
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(SkillLoadError, match="KAZMA_SECRET.*not set"):
                await loader.load_skill("test-skill")

    @pytest.mark.asyncio
    async def test_corrupt_manifest_raises(self, tmp_path: Path) -> None:
        """Corrupt manifest with checksum → SkillLoadError (not swallowed)."""
        from kazma_core.hub.loader import SkillLoadError, SkillLoader

        skill_dir = _make_skill(tmp_path)
        _sign_skill(skill_dir, secret="test-secret")

        # Corrupt the manifest with invalid YAML
        (skill_dir / "skill_manifest.yaml").write_text("{{{{invalid yaml")

        loader = SkillLoader(skills_dir=str(tmp_path / "skills"))
        # The YAML parse error is caught as SkillLoadError either in the
        # manifest parsing stage or the checksum verification stage.
        with pytest.raises(SkillLoadError):
            await loader.load_skill("test-skill")


class TestSignCommand:
    """The `kazma hub sign` CLI command writes checksum + signature."""

    def test_sign_writes_checksum_and_signature(self, tmp_path: Path) -> None:
        from click.testing import CliRunner
        from kazma_core.hub.cli import hub

        skill_dir = _make_skill(tmp_path)
        runner = CliRunner()

        result = runner.invoke(
            hub,
            ["sign", str(skill_dir), "--secret", "my-secret"],
        )

        assert result.exit_code == 0, f"sign failed: {result.output}"
        assert "Signed" in result.output

        # Verify the manifest now has checksum + signature
        manifest = yaml.safe_load(
            (skill_dir / "skill_manifest.yaml").read_text(encoding="utf-8")
        )
        assert "checksum" in manifest
        assert "signature" in manifest
        assert len(manifest["checksum"]) == 64  # SHA256 hex

        # Verify the signature is correct
        py_file = skill_dir / "main.py"
        actual_hash = hashlib.sha256(py_file.read_bytes()).hexdigest()
        expected_sig = hmac.new(
            b"my-secret", actual_hash.encode(), hashlib.sha256
        ).hexdigest()
        assert manifest["checksum"] == actual_hash
        assert manifest["signature"] == expected_sig

    def test_sign_requires_secret(self, tmp_path: Path) -> None:
        """sign without KAZMA_SECRET or --secret → error."""
        from click.testing import CliRunner
        from kazma_core.hub.cli import hub

        skill_dir = _make_skill(tmp_path)
        runner = CliRunner()

        env = {k: v for k, v in os.environ.items() if k != "KAZMA_SECRET"}
        result = runner.invoke(
            hub, ["sign", str(skill_dir)], env=env,
        )

        assert result.exit_code != 0
        assert "KAZMA_SECRET" in result.output

    def test_signed_skill_loads_after_sign(self, tmp_path: Path) -> None:
        """End-to-end: sign a skill, then load it successfully."""
        from click.testing import CliRunner
        from kazma_core.hub.cli import hub
        from kazma_core.hub.loader import SkillLoader

        skill_dir = _make_skill(tmp_path)
        runner = CliRunner()

        result = runner.invoke(
            hub, ["sign", str(skill_dir), "--secret", "load-secret"],
        )
        assert result.exit_code == 0

        loader = SkillLoader(skills_dir=str(tmp_path / "skills"))
        with patch.dict(os.environ, {"KAZMA_SECRET": "load-secret"}):
            loaded = asyncio_run(loader.load_skill("test-skill"))
        assert loaded.value == 4


def asyncio_run(coro):
    """Helper to run async from sync test context."""
    import asyncio

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import nest_asyncio
            nest_asyncio.apply()
        return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)
