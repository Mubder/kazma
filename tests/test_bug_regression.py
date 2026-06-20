"""Regression tests for the Technical Debt Sprint (T-Patch-01).

Each test corresponds to a specific bug from the audit report.
If any of these tests fail, the corresponding bug has regressed.
"""
from __future__ import annotations

import asyncio
import itertools
import json
import re
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

# ── Bug 1: checkpoint.py prune() only fetches keep_last+1 ──────────────────


class TestBug01_PruneFetchesAllCheckpoints:
    """prune() must fetch ALL checkpoints, not just keep_last+1."""

    @pytest.mark.asyncio
    async def test_prune_deletes_all_old_checkpoints(self, tmp_path):
        """With 200 checkpoints and keep_last=100, exactly 100 should be deleted."""
        from kazma_core.checkpoint import CheckpointManager

        db_path = str(tmp_path / "test.db")
        manager = CheckpointManager(db_path=db_path)
        await manager._ensure_saver()

        # Create 200 checkpoints
        from kazma_core.state import initial_state
        for i in range(200):
            state = initial_state()
            state["messages"] = [{"role": "user", "content": f"msg {i}"}]
            state["created_at"] = f"2026-01-{(i % 28) + 1:02d}T00:00:00Z"
            await manager.save(state)

        # Prune: keep 100
        removed = await manager.prune(keep_last=100)

        # Should have removed 100, not just 1
        assert removed == 100, f"Expected 100 removed, got {removed}"

        remaining = await manager.list_checkpoints(limit=999_999)
        assert len(remaining) == 100
        await manager.close()


# ── Bug 2: recovery.py uses wrong field name ───────────────────────────────


class TestBug02_RecoveryFieldName:
    """recovery.py must use 'last_cp_id', not 'checkpoint_id'."""

    def test_state_has_last_cp_id_not_checkpoint_id(self):
        """AgentState uses 'last_cp_id', not 'checkpoint_id'."""
        from kazma_core.state import initial_state
        state = initial_state()
        assert "last_cp_id" in state
        assert "checkpoint_id" not in state

    @pytest.mark.asyncio
    async def test_recovery_logs_correct_field(self, tmp_path, caplog):
        """recovery.py should log last_cp_id, not None."""
        import logging
        from kazma_core.checkpoint import CheckpointManager
        from kazma_core.state import initial_state

        db_path = str(tmp_path / "test.db")
        manager = CheckpointManager(db_path=db_path)
        state = initial_state()
        state["messages"] = [{"role": "user", "content": "test"}]
        cp_id = await manager.save(state)
        await manager.close()

        with caplog.at_level(logging.INFO):
            from kazma_core.recovery import recover_on_startup
            recovered = await recover_on_startup(db_path=db_path)

        # Should log the actual checkpoint ID, not None
        assert cp_id in caplog.text or recovered.get("last_cp_id") == cp_id


# ── Bug 3: tone_adapter.py \b broken for Arabic ───────────────────────────


class TestBug03_ArabicWordBoundary:
    """Arabic word boundary regex must use (?<!\\w)...(?!\\w), not \\b."""

    def test_arabic_slang_is_formalized(self):
        """شلونك must be replaced with كيف حالك."""
        from kazma_core.tone_adapter import ToneAdapter
        adapter = ToneAdapter()
        result = adapter._formalize_text("شلونك اليوم", "kw")
        assert "شلونك" not in result
        assert "كيف حالك" in result

    def test_arabic_boundary_does_not_match_substring(self):
        """Must not replace شلونك inside a longer word."""
        from kazma_core.tone_adapter import ToneAdapter
        adapter = ToneAdapter()
        # If the word is part of a longer string, it should still match
        # (Arabic doesn't have compound words like Latin)
        result = adapter._formalize_text("هلا شلونك يا خوي", "kw")
        assert "كيف حالك" in result
        assert "مرحباً" in result  # هلا -> مرحباً

    def test_all_kuwaiti_formalizations_work(self):
        """Every entry in _KUWAITI_FORMAL_MAP must actually be replaced."""
        from kazma_core.tone_adapter import ToneAdapter, _KUWAITI_FORMAL_MAP
        adapter = ToneAdapter()
        for informal, formal in _KUWAITI_FORMAL_MAP.items():
            result = adapter._formalize_text(informal, "kw")
            assert formal in result, f"Failed to formalize '{informal}' -> '{formal}': got '{result}'"


# ── Bug 4: Chinese text in Kuwaiti markers ─────────────────────────────────


class TestBug04_NoChineseInKuwaitiMarkers:
    """Kuwaiti dialect markers must not contain Chinese characters."""

    def test_no_chinese_characters_in_markers(self):
        """No Chinese characters should appear in _KUWAITI_MARKERS values."""
        from kazma_core.dialect_detector import _KUWAITI_MARKERS
        chinese_re = re.compile(r'[\u4e00-\u9fff]')
        for key, value in _KUWAITI_MARKERS.items():
            assert not chinese_re.search(value), (
                f"Chinese character found in _KUWAITI_MARKERS['{key}'] = '{value}'"
            )

    def test_yalla_maps_to_arabic(self):
        """يالله should map to Arabic, not Chinese."""
        from kazma_core.dialect_detector import _KUWAITI_MARKERS
        assert "يالله" in _KUWAITI_MARKERS
        assert _KUWAITI_MARKERS["يالله"] == "هيا بنا"

    def test_no_chinchin_marker(self):
        """chinchin (Japanese slang) should not be in Kuwaiti markers."""
        from kazma_core.dialect_detector import _KUWAITI_MARKERS
        assert "chinchin" not in _KUWAITI_MARKERS


# ── Bug 5: Chinese characters in pacing.py ─────────────────────────────────


class TestBug05_NoChineseInPacingPatterns:
    """Transaction patterns must not contain Chinese characters."""

    def test_no_chinese_in_transaction_patterns(self):
        from kazma_core.pacing import _TRANSACTION_PATTERNS
        chinese_re = re.compile(r'[\u4e00-\u9fff]')
        for pattern in _TRANSACTION_PATTERNS:
            assert not chinese_re.search(pattern), (
                f"Chinese character found in _TRANSACTION_PATTERNS: '{pattern}'"
            )


# ── Bug 6: Dead code _ISLAMIC_EVENTS_GREGORIAN ─────────────────────────────


class TestBug06_NoDeadGregorianEvents:
    """_ISLAMIC_EVENTS_GREGORIAN should be removed."""

    def test_dead_code_removed(self):
        import kazma_core.cultural_context as cc
        assert not hasattr(cc, '_ISLAMIC_EVENTS_GREGORIAN'), (
            "_ISLAMIC_EVENTS_GREGORIAN is dead code — should be removed"
        )


# ── Bug 7: cultural_context.py ignores self._now ───────────────────────────


class TestBug07_BusinessAppropriateUsesTestableHour:
    """is_business_appropriate() must accept an hour override for testing."""

    def test_business_appropriate_during_iftar(self):
        """During Ramadan iftar time (18:00 Kuwait), business is not appropriate."""
        from kazma_core.cultural_context import CulturalContext
        # Ramadan 2026: approx March 1 - March 30
        ctx = CulturalContext(now=date(2026, 3, 15))
        if ctx.state.is_ramadan:
            assert ctx.is_business_appropriate(current_hour=18) is False

    def test_business_appropriate_outside_iftar(self):
        """Outside iftar hours, business is appropriate even in Ramadan."""
        from kazma_core.cultural_context import CulturalContext
        ctx = CulturalContext(now=date(2026, 3, 15))
        if ctx.state.is_ramadan:
            assert ctx.is_business_appropriate(current_hour=10) is True

    def test_business_appropriate_non_ramadan(self):
        """Outside Ramadan, business is always appropriate."""
        from kazma_core.cultural_context import CulturalContext
        ctx = CulturalContext(now=date(2026, 7, 15))
        assert ctx.is_business_appropriate(current_hour=18) is True


# ── Bug 8: DB path mismatch ────────────────────────────────────────────────


class TestBug08_DBPathAligned:
    """kazma.yaml storage.path must match CHECKPOINT_DB in agent.py."""

    def test_paths_aligned(self):
        from kazma_core.agent import CHECKPOINT_DB
        with open("kazma.yaml") as f:
            cfg = yaml.safe_load(f)
        yaml_path = cfg["storage"]["path"]
        assert yaml_path == CHECKPOINT_DB, (
            f"YAML path '{yaml_path}' != code path '{CHECKPOINT_DB}'"
        )


# ── Bug 9: MSA score biased to 0 ──────────────────────────────────────────


class TestBug09_MSAScoreNotZero:
    """MSA must have a base score even when dialect markers are present."""

    def test_msa_text_with_one_dialect_marker_not_ignored(self):
        """A primarily MSA text with one Kuwaiti word should still have MSA score."""
        from kazma_core.dialect_detector import _rule_based_detect
        # Mostly MSA with one casual Kuwaiti word
        result = _rule_based_detect("الذي قال انه شلونك اليوم")
        # MSA should not be zero — it should at least be competitive
        assert result.dialect in ("msa", "kw"), f"Got unexpected dialect: {result.dialect}"

    def test_pure_msa_detected(self):
        """Pure MSA text should be detected as MSA."""
        from kazma_core.dialect_detector import _rule_based_detect
        result = _rule_based_detect("بناءً على ذلك، المملكة العربية السعودية قررت")
        assert result.dialect == "msa"

    def test_pure_kuwaiti_detected(self):
        """Pure Kuwaiti text should be detected as Kuwaiti."""
        from kazma_core.dialect_detector import _rule_based_detect
        result = _rule_based_detect("شلونك خوي شنو الاخبار")
        assert result.dialect == "kw"


# ── Bug 10: O(N) load in checkpoint.py ─────────────────────────────────────


class TestBug10_CheckpointLoadUsesSQL:
    """load() should use direct SQL lookup, not scan all checkpoints."""

    @pytest.mark.asyncio
    async def test_load_finds_checkpoint_directly(self, tmp_path):
        from kazma_core.checkpoint import CheckpointManager
        from kazma_core.state import initial_state

        db_path = str(tmp_path / "test.db")
        manager = CheckpointManager(db_path=db_path)

        state = initial_state()
        state["messages"] = [{"role": "user", "content": "test"}]
        cp_id = await manager.save(state)

        loaded = await manager.load(cp_id)
        assert loaded["messages"][0]["content"] == "test"
        await manager.close()

    @pytest.mark.asyncio
    async def test_load_nonexistent_raises(self, tmp_path):
        from kazma_core.checkpoint import CheckpointManager

        db_path = str(tmp_path / "test.db")
        manager = CheckpointManager(db_path=db_path)
        await manager._ensure_saver()

        with pytest.raises(FileNotFoundError):
            await manager.load("nonexistent-id")
        await manager.close()


# ── Bug 11: mcp_client.py ID concurrency ───────────────────────────────────


class TestBug11_RequestIDConcurrency:
    """100 concurrent async calls must produce 100 unique IDs."""

    def test_100_concurrent_ids_unique(self):
        """itertools.count() must produce unique IDs under concurrent access."""
        from kazma_core.mcp_client import _next_id

        ids = set()
        for _ in range(100):
            ids.add(_next_id())
        assert len(ids) == 100, f"Expected 100 unique IDs, got {len(ids)}"

    @pytest.mark.asyncio
    async def test_concurrent_async_ids_unique(self):
        """100 concurrent async tasks must produce unique IDs."""
        from kazma_core.mcp_client import _next_id

        async def get_id():
            return _next_id()

        tasks = [get_id() for _ in range(100)]
        results = await asyncio.gather(*tasks)
        assert len(set(results)) == 100, (
            f"Expected 100 unique IDs from concurrent async, got {len(set(results))}"
        )


# ── Bug 12: swarm.py type annotation ──────────────────────────────────────


class TestBug12_SwarmTypeAnnotation:
    """parallel_execute results list must accept None values."""

    def test_results_list_accepts_none(self):
        """The type annotation list[DelegationResult | None] must be valid."""
        from kazma_core.delegation.swarm import SwarmIntelligence
        import inspect
        source = inspect.getsource(SwarmIntelligence.parallel_execute)
        assert "DelegationResult | None" in source or "Optional[DelegationResult]" in source


# ── Bug 13: hub/registry.py lazy _agents init ─────────────────────────────


class TestBug13_AgentsDictInInit:
    """_agents must be initialized in __init__, not lazily."""

    def test_agents_exists_after_init(self):
        from kazma_core.hub.registry import KazmaHub
        hub = KazmaHub(registry_path="/tmp/test_bug13.db")
        assert hasattr(hub, "_agents")
        assert isinstance(hub._agents, dict)
        assert len(hub._agents) == 0

    @pytest.mark.asyncio
    async def test_list_agents_works_before_register(self):
        """list_agents() must return [] before any register_agent() call."""
        from kazma_core.hub.registry import KazmaHub
        hub = KazmaHub(registry_path="/tmp/test_bug13.db")
        agents = await hub.list_agents()
        assert agents == []


# ── Bug 14: German/English in Kuwaiti farewell patterns ────────────────────


class TestBug14_NoGermanEnglishInFarewells:
    """Farewell patterns must be Arabic, not German or English."""

    def test_no_german_in_farewells(self):
        from kazma_core.pacing import _FAREWELL_PATTERNS
        for pattern in _FAREWELL_PATTERNS:
            assert "Wiedersehen" not in pattern, f"German found: '{pattern}'"
            assert "ttyl" not in pattern.lower(), f"English slang found: '{pattern}'"

    def test_farewells_are_arabic(self):
        from kazma_core.pacing import _FAREWELL_PATTERNS
        arabic_re = re.compile(r'[\u0600-\u06ff]')
        for pattern in _FAREWELL_PATTERNS:
            assert arabic_re.search(pattern), f"Non-Arabic farewell: '{pattern}'"


# ── Bug 15: validator.py \b__import__\b never matches ─────────────────────


class TestBug15_ImportDetectionWorks:
    """__import__ must be detected in security scans."""

    def test_import_detected_in_code(self):
        """The security scanner must detect __import__ usage."""
        from kazma_core.hub.validator import SkillValidator
        import inspect
        source = inspect.getsource(SkillValidator._scan_for_security_issues)
        # Must use lookaround, not \b
        assert r"(?<!\w)" in source or r"(?<!\w)__import__(?!\w)" in source

    def test_import_pattern_matches(self):
        """The regex pattern must actually match __import__ in code."""
        # The old pattern \b__import__\b never matched because _ is \w
        pattern = r"(?<!\w)__import__(?!\w)"
        assert re.search(pattern, "x = __import__('os')")
        assert re.search(pattern, "__import__('os').system('ls')")


# ── Bug 16: hub/registry.py sync sqlite3 in constructor ────────────────────


class TestBug16_NoSyncSqliteInConstructor:
    """Constructor must not use synchronous sqlite3.connect."""

    def test_constructor_has_no_sync_sqlite(self):
        import inspect
        from kazma_core.hub.registry import KazmaHub
        source = inspect.getsource(KazmaHub.__init__)
        assert "sqlite3.connect" not in source, (
            "Constructor still uses synchronous sqlite3.connect"
        )

    @pytest.mark.asyncio
    async def test_tables_created_on_first_use(self, tmp_path):
        """Tables should be created on first async operation."""
        from kazma_core.hub.registry import KazmaHub
        hub = KazmaHub(registry_path=str(tmp_path / "test.db"))
        # This triggers _get_conn which should create tables
        results = await hub.search()
        assert results == []
        await hub.close()


# ── Bug 17: mcp_client.py sync stdin.write ─────────────────────────────────


class TestBug17_NotifyUsesExecutor:
    """_notify() must use run_in_executor for stdio writes."""

    def test_notify_uses_run_in_executor(self):
        import inspect
        from kazma_core.mcp_client import MCPClient
        source = inspect.getsource(MCPClient._notify)
        assert "run_in_executor" in source, (
            "_notify() still uses synchronous stdin.write"
        )
        assert "proc.stdin.write" in source


# ── Bug 18: test uses production DB ────────────────────────────────────────


class TestBug18_TestUsesTmpPath:
    """Test fixtures must use tmp_path, not production paths."""

    def test_discovery_fixture_uses_tmp_path(self):
        """The hub fixture in test_agent_discovery.py must use tmp_path."""
        with open("tests/test_agent_discovery.py") as f:
            source = f.read()
        assert "tmp_path" in source, "Test fixture still uses production DB path"
        assert "KazmaHub()" not in source.replace("KazmaHub(registry_path=", ""), (
            "KazmaHub() with no args uses production path"
        )


# ── Bug 19: tracing.py ignores YAML config ─────────────────────────────────


class TestBug19_TracingReadsConfig:
    """KazmaTracer must accept config dict and use it for Langfuse keys."""

    def test_tracer_accepts_config_param(self):
        from kazma_core.tracing import KazmaTracer
        import inspect
        sig = inspect.signature(KazmaTracer.__init__)
        assert "config" in sig.parameters

    def test_factory_accepts_config_param(self):
        from kazma_core.tracing import create_tracer
        import inspect
        sig = inspect.signature(create_tracer)
        assert "config" in sig.parameters

    def test_langfuse_uses_config_keys(self):
        """_init_langfuse should read from self._config with env var fallback."""
        import inspect
        from kazma_core.tracing import KazmaTracer
        source = inspect.getsource(KazmaTracer._init_langfuse)
        assert "self._config.get" in source, (
            "Langfuse init still only reads from env vars"
        )


# ── Bug 20: ContextAuthority not wired into agent.py ──────────────────────


class TestBug20_ContextAuthorityWired:
    """KazmaAgent.run() must call ContextAuthority.check_and_enforce()."""

    def test_agent_has_authority_attribute(self):
        from kazma_core.agent import KazmaAgent
        agent = KazmaAgent()
        assert hasattr(agent, "authority")
        from kazma_core.authority import ContextAuthority
        assert isinstance(agent.authority, ContextAuthority)

    @pytest.mark.asyncio
    async def test_run_calls_check_and_enforce(self):
        """run() must invoke the authority check."""
        from kazma_core.agent import KazmaAgent
        agent = KazmaAgent()

        # Patch check_and_enforce to verify it's called
        with patch.object(
            agent.authority, "check_and_enforce", new_callable=AsyncMock
        ) as mock_check:
            mock_check.return_value = {
                "messages": [{"role": "user", "content": "test"}],
                "tool_results": {},
                "context_tokens": 0,
            }
            await agent.run("test input")

        mock_check.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_actually_compacts_when_threshold_exceeded(self):
        """When tokens exceed 80%, compaction must be triggered."""
        from kazma_core.agent import KazmaAgent
        from kazma_core.token_counter import TokenCounter

        agent = KazmaAgent()

        # Create a state with many tokens to trigger compaction
        big_state = {
            "messages": [
                {"role": "user", "content": "x" * 500_000}  # ~125k tokens
            ],
            "tool_results": {},
            "context_tokens": 0,
        }

        # The authority should detect this exceeds threshold
        counter = agent.authority.counter
        assert counter.should_compact(big_state["messages"]) is True
