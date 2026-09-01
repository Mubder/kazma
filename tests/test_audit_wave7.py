"""Wave 7: correctness hygiene (M-1/M-2, M-4..M-8, M-12/M-13, M-14 leftover)."""

from __future__ import annotations

import inspect
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kazma_core.llm_provider import (
    LLMError,
    _http_status_is_transient,
    _inband_stream_error_is_transient,
)


def test_retry_fallback_status_is_transient() -> None:
    """M-1: 429/5xx stay transient; 400 stays permanent."""
    assert _http_status_is_transient(503) is True
    assert _http_status_is_transient(429) is True
    assert _http_status_is_transient(400) is False
    assert _http_status_is_transient(None) is True


def test_inband_sse_error_parses_status() -> None:
    """M-2: LiteLLM-style in-band 429 is transient; 400 is not."""
    assert _inband_stream_error_is_transient({"status": 429, "message": "slow"}, "slow") is True
    assert _inband_stream_error_is_transient({"code": 503}, "overloaded") is True
    assert _inband_stream_error_is_transient({"status": 400, "message": "bad schema"}, "bad") is False
    err = LLMError("LLM stream error: rate limit", transient=True)
    assert err.transient is True


def test_snapshot_loop_uses_spawn_background() -> None:
    """M-4: callee retains the task so app.py can discard the return."""
    from kazma_core.time_travel import start_snapshot_maintenance_loop

    src = inspect.getsource(start_snapshot_maintenance_loop)
    assert "spawn_background" in src
    assert "asyncio.create_task(_loop())" not in src.replace(" ", "")


def test_kb_crawl_uses_spawn_background() -> None:
    """M-6: /kb crawl and refresh must not discard create_task."""
    src = Path("kazma-gateway/kazma_gateway/agent_handler/commands.py").read_text(
        encoding="utf-8"
    )
    assert "spawn_background" in src
    assert "_asyncio_kb.create_task(_run_crawl())" not in src
    assert "_asyncio_kb.create_task(_run_refresh())" not in src


def test_slack_and_discord_output_ids() -> None:
    """M-7: slack C… / discord snowflake; telegram still int-only."""
    from kazma_gateway.agent_handler.swarm_dispatch import _parse_output_target_suffix

    clean, ov = _parse_output_target_suffix("x -> slack:C0123ABC")
    assert clean == "x"
    assert ov == {"platform": "slack", "chat_id": "C0123ABC", "enabled": True}

    snow = "123456789012345678"
    clean, ov = _parse_output_target_suffix(f"x -> discord:{snow}")
    assert ov is not None
    assert ov["chat_id"] == snow

    clean, ov = _parse_output_target_suffix("x -> telegram:abc")
    assert ov is None
    assert clean == "x -> telegram:abc"


def test_fork_writes_full_state_not_just_messages() -> None:
    """M-8: aupdate_state gets the snapshot dict, not messages-only."""
    src = Path("kazma-gateway/kazma_gateway/agent_handler/graph.py").read_text(
        encoding="utf-8"
    )
    assert "await graph.aupdate_state(new_config, state)" in src
    assert 'aupdate_state(new_config, {"messages": state.get("messages", [])})' not in src
    assert 'gw.pop("chat_id", None)' in src
    assert "active_thread" in src  # comment: do not overwrite


def test_pg_dump_stale_hours_tracks_cadence() -> None:
    """M-14: stale threshold is cadence + slack, not a frozen 26h."""
    from kazma_core.backup.universal import _pg_dump_stale_hours
    from kazma_core.memory.worker_bootstrap import _BACKUP_EXPORT_INTERVAL_HOURS

    assert _pg_dump_stale_hours() == float(_BACKUP_EXPORT_INTERVAL_HOURS) + 2.0
    assert _pg_dump_stale_hours() < 26.0


def test_plain_log_unparseable_is_skipped() -> None:
    """M-13: undated lines must not count as in-window firings."""
    from kazma_core.observability.firing_ledger import _within

    assert _within("not-a-date", 0.0) is False


def test_sensitive_shells_and_telegram_webhook_open() -> None:
    """M-14: /documents and /scheduled gated; telegram webhook open-prefix."""
    from kazma_ui.auth import ALWAYS_OPEN_PREFIXES, is_always_open, is_sensitive_path

    assert is_sensitive_path("/documents") is True
    assert is_sensitive_path("/scheduled") is True
    assert any(p.startswith("/api/webhooks/telegram") for p in ALWAYS_OPEN_PREFIXES)
    assert is_always_open("/api/webhooks/telegram") is True


def test_search_top_k_clamped() -> None:
    src = Path("kazma-ui/kazma_ui/documents_api.py").read_text(encoding="utf-8")
    assert "min(top_k, 50)" in src
    assert 'rate_limit("documents"' in src
    assert 'rate_limit("chat_upload"' in Path(
        "kazma-ui/kazma_ui/routes_chat_upload.py"
    ).read_text(encoding="utf-8")


def test_embedder_rebuild_spawn_background() -> None:
    src = Path("kazma-ui/kazma_ui/settings.py").read_text(encoding="utf-8")
    assert "spawn_background(_run()" in src
    assert "loop.create_task(_run())" not in src


def test_voice_uses_safe_error() -> None:
    src = Path("kazma-ui/kazma_ui/routes_voice.py").read_text(encoding="utf-8")
    assert "safe_error" in src
    ws = Path("kazma-ui/kazma_ui/routes_voice_ws.py").read_text(encoding="utf-8")
    assert "_cancel_utterance" in ws.split("finally:")[1]


@pytest.mark.asyncio
async def test_slack_prefetch_fills_bytes_without_token_in_meta() -> None:
    """M-5: adapter downloads url_private with bot token; token stays off the Attachment."""
    from kazma_gateway.adapters.slack import SlackAdapter
    from kazma_gateway.gateway import Attachment, IncomingMessage

    adapter = SlackAdapter(bot_token="xoxb-secret")
    http = MagicMock()
    resp = MagicMock()
    resp.content = b"PNGDATA"
    resp.raise_for_status = MagicMock()
    http.get = AsyncMock(return_value=resp)
    adapter._http = http

    msg = IncomingMessage(
        platform="slack",
        sender_id="slack:U1",
        text="pic",
        attachments=[
            Attachment(
                kind="image",
                mime="image/png",
                filename="x.png",
                url="https://files.slack.com/files-pri/x.png",
                meta={"source": "slack"},
            )
        ],
    )
    out = await adapter._prefetch_private_files(msg)
    assert out.attachments[0].data == b"PNGDATA"
    assert out.attachments[0].url is None
    assert "xoxb-secret" not in str(out.attachments[0].meta)
    headers = http.get.await_args.kwargs["headers"]
    assert headers["Authorization"].startswith("Bearer xoxb-")
