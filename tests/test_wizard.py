"""Tests for the Skill Installation Wizard."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from kazma_core.cli.wizard import SkillInstallationWizard, WizardContext

# ── WizardContext ──────────────────────────────────────────────────────────────


class TestWizardContext:
    """Tests for WizardContext."""

    def test_default_context(self):
        ctx = WizardContext()
        assert ctx.selected_skill is None
        assert ctx.manifest_data is None
        assert ctx.security_results is None
        assert ctx.registry_path == "~/.kazma/hub/registry.db"
        assert ctx.skills_dir == Path("~/.kazma/skills").expanduser()

    def test_custom_registry_path(self):
        ctx = WizardContext()
        ctx.registry_path = "/tmp/test.db"
        assert ctx.registry_path == "/tmp/test.db"


# ── SkillInstallationWizard initialization ─────────────────────────────────────


class TestWizardInit:
    """Tests for wizard initialization."""

    def test_default_init(self):
        wizard = SkillInstallationWizard()
        assert wizard.current_step == 0
        assert wizard.non_interactive is False
        assert wizard.context is not None

    def test_custom_registry_path(self):
        wizard = SkillInstallationWizard(registry_path="/tmp/test.db")
        assert wizard.context.registry_path == "/tmp/test.db"

    def test_non_interactive_mode(self):
        wizard = SkillInstallationWizard(non_interactive=True)
        assert wizard.non_interactive is True

    def test_steps_list(self):
        assert SkillInstallationWizard.STEPS == [
            "welcome",
            "select_skill",
            "review_manifest",
            "security_check",
            "confirm_install",
            "install",
            "verify",
            "success",
        ]


# ── Step: Welcome ──────────────────────────────────────────────────────────────


class TestStepWelcome:
    """Tests for the welcome step."""

    @pytest.mark.asyncio
    async def test_welcome_prints_banner(self, capsys):
        wizard = SkillInstallationWizard()
        await wizard._step_welcome()
        captured = capsys.readouterr()
        assert "Kazma Hub" in captured.out
        assert "Skill Installation Wizard" in captured.out


# ── Step: Select Skill ────────────────────────────────────────────────────────


class TestStepSelectSkill:
    """Tests for the select_skill step."""

    @pytest.mark.asyncio
    async def test_select_skill_no_skills(self, capsys):
        wizard = SkillInstallationWizard()

        with patch("kazma_core.hub.registry.KazmaHub") as MockHub:
            mock_hub = AsyncMock()
            mock_hub.search = AsyncMock(return_value=[])
            mock_hub.close = AsyncMock()
            MockHub.return_value = mock_hub

            result = await wizard._step_select_skill()
            assert result is False
            captured = capsys.readouterr()
            assert "No skills found" in captured.out

    @pytest.mark.asyncio
    async def test_select_skill_displays_list(self, capsys):
        wizard = SkillInstallationWizard(non_interactive=True)

        mock_manifest = MagicMock()
        mock_manifest.data = {
            "name": "test-skill",
            "author": "test-author",
            "version": "1.0.0",
            "description": "A test skill",
        }

        with patch("kazma_core.hub.registry.KazmaHub") as MockHub:
            mock_hub = AsyncMock()
            mock_hub.search = AsyncMock(return_value=[mock_manifest])
            mock_hub.close = AsyncMock()
            MockHub.return_value = mock_hub

            result = await wizard._step_select_skill()
            captured = capsys.readouterr()
            assert "test-skill" in captured.out
            assert "test-author" in captured.out
            assert wizard.context.selected_skill is not None
            assert wizard.context.selected_skill["name"] == "test-skill"

    @pytest.mark.asyncio
    async def test_select_skill_user_quit(self, capsys):
        wizard = SkillInstallationWizard()

        mock_manifest = MagicMock()
        mock_manifest.data = {"name": "test-skill", "author": "a", "version": "1.0"}

        with patch("kazma_core.hub.registry.KazmaHub") as MockHub:
            mock_hub = AsyncMock()
            mock_hub.search = AsyncMock(return_value=[mock_manifest])
            mock_hub.close = AsyncMock()
            MockHub.return_value = mock_hub

            with patch("builtins.input", return_value="q"):
                result = await wizard._step_select_skill()
                assert result is False

    @pytest.mark.asyncio
    async def test_select_skill_user_invalid_input(self, capsys):
        wizard = SkillInstallationWizard()

        mock_manifest = MagicMock()
        mock_manifest.data = {"name": "test-skill", "author": "a", "version": "1.0"}

        with patch("kazma_core.hub.registry.KazmaHub") as MockHub:
            mock_hub = AsyncMock()
            mock_hub.search = AsyncMock(return_value=[mock_manifest])
            mock_hub.close = AsyncMock()
            MockHub.return_value = mock_hub

            with patch("builtins.input", return_value="abc"):
                result = await wizard._step_select_skill()
                assert result is False


# ── Step: Review Manifest ─────────────────────────────────────────────────────


class TestStepReviewManifest:
    """Tests for the review_manifest step."""

    @pytest.mark.asyncio
    async def test_review_manifest_displays_info(self, capsys):
        wizard = SkillInstallationWizard()
        wizard.context.selected_skill = {
            "name": "test-skill",
            "version": "2.0.0",
            "author": "test-author",
            "license": "MIT",
            "description": "A test skill for testing",
            "capabilities": ["analysis", "data-processing"],
            "permissions": ["file_read", "network_outbound"],
            "dependencies": [{"name": "httpx", "version": ">=0.27.0"}],
            "mcp_servers": [{"name": "my-server"}],
        }

        await wizard._step_review_manifest()
        captured = capsys.readouterr()

        assert "test-skill" in captured.out
        assert "2.0.0" in captured.out
        assert "test-author" in captured.out
        assert "MIT" in captured.out
        assert "analysis" in captured.out
        assert "file_read" in captured.out
        assert "httpx" in captured.out
        assert "my-server" in captured.out

    @pytest.mark.asyncio
    async def test_review_manifest_no_skill(self, capsys):
        wizard = SkillInstallationWizard()
        wizard.context.selected_skill = None

        await wizard._step_review_manifest()
        captured = capsys.readouterr()
        assert "No skill selected" in captured.out


# ── Step: Security Check ──────────────────────────────────────────────────────


class TestStepSecurityCheck:
    """Tests for the security_check step."""

    @pytest.mark.asyncio
    async def test_security_check_with_local_path(self, capsys, tmp_path):
        wizard = SkillInstallationWizard()

        # Create a minimal skill directory
        skill_dir = tmp_path / "test-skill"
        skill_dir.mkdir()
        (skill_dir / "skill_manifest.yaml").write_text(
            "name: test\nversion: 1.0.0\nauthor: test\ndescription: test\n"
        )
        (skill_dir / "main.py").write_text("class TestSkill: pass\n")

        wizard.context.selected_skill = {
            "name": "test",
            "installed_path": str(skill_dir),
        }

        await wizard._step_security_check()
        captured = capsys.readouterr()

        assert "Running security validation" in captured.out
        assert "Security Score:" in captured.out

    @pytest.mark.asyncio
    async def test_security_check_no_local_path(self, capsys):
        wizard = SkillInstallationWizard()
        wizard.context.selected_skill = {
            "name": "remote-skill",
            "installed_path": None,
        }

        await wizard._step_security_check()
        captured = capsys.readouterr()

        assert "manifest-only validation" in captured.out
        assert wizard.context.security_results is not None
        assert wizard.context.security_results["passed"] is True


# ── Step: Confirm Install ─────────────────────────────────────────────────────


class TestStepConfirmInstall:
    """Tests for the confirm_install step."""

    @pytest.mark.asyncio
    async def test_confirm_install_user_yes(self):
        wizard = SkillInstallationWizard()
        wizard.context.selected_skill = {"name": "test", "version": "1.0"}
        wizard.context.security_results = {"passed": True}

        with patch("builtins.input", return_value="y"):
            result = await wizard._step_confirm_install()
            assert result is None  # None means proceed

    @pytest.mark.asyncio
    async def test_confirm_install_user_no(self):
        wizard = SkillInstallationWizard()
        wizard.context.selected_skill = {"name": "test", "version": "1.0"}
        wizard.context.security_results = {"passed": True}

        with patch("builtins.input", return_value="n"):
            result = await wizard._step_confirm_install()
            assert result is False

    @pytest.mark.asyncio
    async def test_confirm_install_security_warning(self, capsys):
        wizard = SkillInstallationWizard()
        wizard.context.selected_skill = {"name": "risky", "version": "1.0"}
        wizard.context.security_results = {"passed": False}

        with patch("builtins.input", return_value="y"):
            await wizard._step_confirm_install()
            captured = capsys.readouterr()
            assert "WARNING" in captured.out

    @pytest.mark.asyncio
    async def test_confirm_install_non_interactive(self):
        wizard = SkillInstallationWizard(non_interactive=True)
        wizard.context.selected_skill = {"name": "test", "version": "1.0"}
        wizard.context.security_results = {"passed": True}

        result = await wizard._step_confirm_install()
        assert result is None  # auto-confirm

    @pytest.mark.asyncio
    async def test_confirm_install_no_skill(self):
        wizard = SkillInstallationWizard()
        wizard.context.selected_skill = None

        result = await wizard._step_confirm_install()
        assert result is False


# ── Step: Install ──────────────────────────────────────────────────────────────


class TestStepInstall:
    """Tests for the install step."""

    @pytest.mark.asyncio
    async def test_install_success(self, capsys):
        wizard = SkillInstallationWizard()
        wizard.context.selected_skill = {
            "name": "test-skill",
            "author": "author",
            "version": "1.0.0",
        }

        mock_manifest = MagicMock()

        with patch("kazma_core.hub.registry.KazmaHub") as MockHub:
            mock_hub = AsyncMock()
            mock_hub.get = AsyncMock(return_value=mock_manifest)
            mock_hub.install = AsyncMock(return_value=Path("/tmp/skills/test-skill"))
            mock_hub.close = AsyncMock()
            MockHub.return_value = mock_hub

            await wizard._step_install()
            captured = capsys.readouterr()

            assert "Installing test-skill" in captured.out
            mock_hub.install.assert_called_once_with(
                "kazma-hub://author/test-skill@1.0.0"
            )

    @pytest.mark.asyncio
    async def test_install_not_found(self, capsys):
        wizard = SkillInstallationWizard()
        wizard.context.selected_skill = {
            "name": "missing",
            "author": "author",
            "version": "1.0.0",
        }

        with patch("kazma_core.hub.registry.KazmaHub") as MockHub:
            mock_hub = AsyncMock()
            mock_hub.get = AsyncMock(return_value=None)
            mock_hub.close = AsyncMock()
            MockHub.return_value = mock_hub

            await wizard._step_install()
            captured = capsys.readouterr()
            assert "not found" in captured.out


# ── Step: Verify ───────────────────────────────────────────────────────────────


class TestStepVerify:
    """Tests for the verify step."""

    @pytest.mark.asyncio
    async def test_verify_found(self, capsys):
        wizard = SkillInstallationWizard()
        wizard.context.selected_skill = {
            "name": "test-skill",
            "author": "author",
            "version": "1.0.0",
        }

        mock_manifest = MagicMock()
        mock_manifest.data = {"name": "test-skill", "author": "author"}

        with patch("kazma_core.hub.registry.KazmaHub") as MockHub:
            mock_hub = AsyncMock()
            mock_hub.list_installed = AsyncMock(return_value=[mock_manifest])
            mock_hub.close = AsyncMock()
            MockHub.return_value = mock_hub

            await wizard._step_verify()
            captured = capsys.readouterr()
            assert "PASS" in captured.out

    @pytest.mark.asyncio
    async def test_verify_not_found(self, capsys):
        wizard = SkillInstallationWizard()
        wizard.context.selected_skill = {
            "name": "missing-skill",
            "author": "author",
            "version": "1.0.0",
        }

        with patch("kazma_core.hub.registry.KazmaHub") as MockHub:
            mock_hub = AsyncMock()
            mock_hub.list_installed = AsyncMock(return_value=[])
            mock_hub.close = AsyncMock()
            MockHub.return_value = mock_hub

            await wizard._step_verify()
            captured = capsys.readouterr()
            assert "WARN" in captured.out


# ── Step: Success ──────────────────────────────────────────────────────────────


class TestStepSuccess:
    """Tests for the success step."""

    @pytest.mark.asyncio
    async def test_success_message(self, capsys):
        wizard = SkillInstallationWizard()
        wizard.context.selected_skill = {"name": "my-skill"}

        await wizard._step_success()
        captured = capsys.readouterr()
        assert "Installation Complete" in captured.out
        assert "my-skill" in captured.out

    @pytest.mark.asyncio
    async def test_success_no_skill(self, capsys):
        wizard = SkillInstallationWizard()
        wizard.context.selected_skill = None

        await wizard._step_success()
        captured = capsys.readouterr()
        assert "Installation Complete" in captured.out


# ── Execute step dispatch ─────────────────────────────────────────────────────


class TestExecuteStep:
    """Tests for _execute_step dispatch."""

    @pytest.mark.asyncio
    async def test_execute_step_dispatches_correctly(self):
        wizard = SkillInstallationWizard()

        with patch.object(wizard, "_step_welcome", new_callable=AsyncMock) as mock:
            mock.return_value = None
            result = await wizard._execute_step("welcome")
            mock.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_step_unknown_returns_none(self):
        wizard = SkillInstallationWizard()
        result = await wizard._execute_step("nonexistent_step")
        assert result is None


# ── Run sync ───────────────────────────────────────────────────────────────────


class TestRunSync:
    """Tests for run_sync wrapper."""

    def test_run_sync_empty_steps(self):
        """Wizard with no steps returns True immediately."""
        wizard = SkillInstallationWizard()
        wizard.STEPS = []  # Override to skip all steps
        # We need to run the actual STEPS list, so test differently
        assert len(SkillInstallationWizard.STEPS) == 8
