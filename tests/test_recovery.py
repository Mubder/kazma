"""Tests for Recovery Hook — startup recovery and crash survival."""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from kazma_core.checkpoint import CheckpointManager
from kazma_core.recovery import recover_on_startup, resume_agent
from kazma_core.state import AgentState, initial_state


@pytest.fixture
def db_path(tmp_path: Path) -> str:
    """Return a temporary database path."""
    return str(tmp_path / "test_recovery.db")


class TestRecoverOnStartup:
    """Tests for the recover_on_startup function."""

    @pytest.mark.asyncio
    async def test_recovers_saved_state(self, db_path: str) -> None:
        """recover_on_startup should load the last saved checkpoint."""
        # Save a checkpoint first
        manager = CheckpointManager(db_path=db_path)
        state = initial_state()
        state["context_tokens"] = 999
        state["messages"] = [{"role": "user", "content": "important"}]
        await manager.save(state)
        await manager.close()

        # Recover
        recovered = await recover_on_startup(db_path=db_path)
        assert recovered["context_tokens"] == 999
        assert recovered["messages"][0]["content"] == "important"

    @pytest.mark.asyncio
    async def test_returns_initial_when_no_checkpoints(self, db_path: str) -> None:
        """recover_on_startup should return initial state when DB is empty."""
        recovered = await recover_on_startup(db_path=db_path)
        assert recovered["context_tokens"] == 0
        assert recovered["messages"] == []

    @pytest.mark.asyncio
    async def test_handles_corrupted_db(self, tmp_path: Path) -> None:
        """recover_on_startup should handle corrupted database gracefully."""
        db_path = str(tmp_path / "corrupted.db")
        # Write garbage to the DB file
        Path(db_path).write_bytes(b"not a sqlite database")

        # Should return initial state without crashing
        recovered = await recover_on_startup(db_path=db_path)
        assert recovered["context_tokens"] == 0

    @pytest.mark.asyncio
    async def test_recovers_with_multiple_checkpoints(self, db_path: str) -> None:
        """recover_on_startup should return the latest checkpoint."""
        manager = CheckpointManager(db_path=db_path)

        # Save 3 checkpoints with the SAME thread_id so they're in the same thread
        thread_id = "test-thread"
        for tokens in [100, 200, 300]:
            state = initial_state()
            state["context_tokens"] = tokens
            state["provenance"]["thread_id"] = thread_id
            await manager.save(state)

        await manager.close()

        recovered = await recover_on_startup(db_path=db_path)
        assert recovered["context_tokens"] == 300


class TestCrashSurvival:
    """Tests that checkpoints survive process crashes (SIGKILL)."""

    @pytest.mark.asyncio
    async def test_checkpoint_survives_kill9(self, db_path: str) -> None:
        """Checkpoint should survive kill -9 (SIGKILL) of the writing process."""
        # Save a checkpoint from the main process
        manager = CheckpointManager(db_path=db_path)
        state = initial_state()
        state["context_tokens"] = 42
        state["messages"] = [{"role": "user", "content": "before crash"}]
        cp_id = await manager.save(state)
        await manager.close()

        # Simulate crash: spawn a subprocess that writes and gets killed
        script = (
            'import asyncio\n'
            'import sys\n'
            'sys.path.insert(0, "'
            + str(Path(__file__).parent.parent / "kazma-core")
            + '")\n'
            "from kazma_core.checkpoint import CheckpointManager\n"
            "from kazma_core.state import initial_state\n"
            "async def main():\n"
            '    mgr = CheckpointManager(db_path="' + db_path + '")\n'
            "    state = initial_state()\n"
            "    state['context_tokens'] = 999\n"
            "    state['messages'] = [{'role': 'user', 'content': 'during crash'}]\n"
            "    await mgr.save(state)\n"
            '    Path("' + db_path + '.signal").write_text("saved")\n'
            "    await asyncio.sleep(0.5)\n"
            "asyncio.run(main())\n"
        )

        proc = subprocess.Popen(
            [sys.executable, "-c", script],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        # Wait for the signal file to appear (indicating save completed)
        signal_file = Path(f"{db_path}.signal")
        for _ in range(50):
            if signal_file.exists():
                break
            await asyncio.sleep(0.1)

        # Kill the subprocess with SIGKILL
        proc.kill()
        proc.wait()

        # Recover from the database
        recovered = await recover_on_startup(db_path=db_path)

        # The original checkpoint should still be there
        # (the kill might have happened before or after the second save)
        assert recovered["context_tokens"] in (42, 999)

        # Clean up signal file
        signal_file.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_resume_agent_loads_state(self, db_path: str) -> None:
        """resume_agent should load state and create an app."""
        # Save a checkpoint
        manager = CheckpointManager(db_path=db_path)
        state = initial_state()
        state["context_tokens"] = 777
        await manager.save(state)
        await manager.close()

        # Resume
        result = await resume_agent(db_path=db_path)
        assert result["state"]["context_tokens"] == 777
        assert result["app"] is not None
        assert result["thread_id"] is not None

        # Clean up
        await result["saver"].conn.close()


class TestRecoveryIntegration:
    """Integration tests for the full save-recover cycle."""

    @pytest.mark.asyncio
    async def test_full_cycle(self, db_path: str) -> None:
        """Full cycle: save -> close -> recover -> verify."""
        # Phase 1: Save some state
        mgr1 = CheckpointManager(db_path=db_path)
        state = initial_state()
        state["context_tokens"] = 500
        state["messages"] = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there"},
        ]
        state["tool_results"] = {"search": {"results": ["a", "b", "c"]}}
        await mgr1.save(state)
        await mgr1.close()

        # Phase 2: Simulate restart — recover
        recovered = await recover_on_startup(db_path=db_path)
        assert recovered["context_tokens"] == 500
        assert len(recovered["messages"]) == 2
        assert recovered["tool_results"]["search"]["results"] == ["a", "b", "c"]

        # Phase 3: Save more state on top
        mgr2 = CheckpointManager(db_path=db_path)
        recovered["context_tokens"] = 600
        recovered["messages"].append({"role": "user", "content": "more"})
        await mgr2.save(recovered)
        await mgr2.close()

        # Phase 4: Verify the latest checkpoint has everything
        final = await recover_on_startup(db_path=db_path)
        assert final["context_tokens"] == 600
        assert len(final["messages"]) == 3
        assert final["messages"][2]["content"] == "more"
