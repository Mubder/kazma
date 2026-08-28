"""Official X publisher — OAuth 1.0a, ToU policy, always-HITL."""

from __future__ import annotations

from pathlib import Path
import pytest

from kazma_core.safety.hitl import (
    ALWAYS_HITL_TOOLS,
    CANONICAL_DANGER_TOOLS,
    requires_approval,
)
from kazma_core.x_api.ledger import XPostLedger
from kazma_core.x_api.oauth1 import oauth1_authorization_header, percent_encode, sign_request
from kazma_core.x_api.policy import evaluate_post


def test_percent_encode_rfc3986() -> None:
    assert percent_encode("Ladies + Gentlemen") == "Ladies%20%2B%20Gentlemen"
    assert percent_encode("-._~abcABC012") == "-._~abcABC012"


def test_oauth1_signature_stable() -> None:
    kwargs = dict(
        method="POST",
        url="https://api.x.com/2/tweets",
        consumer_key="ck",
        consumer_secret="cs",
        token="at",
        token_secret="ats",
        nonce="n" * 16,
        timestamp="1318622958",
    )
    a = sign_request(**kwargs)
    b = sign_request(**kwargs)
    assert a["oauth_signature"] == b["oauth_signature"]
    assert a["oauth_signature_method"] == "HMAC-SHA1"
    header = oauth1_authorization_header(a)
    assert header.startswith("OAuth ")
    assert "oauth_signature=" in header


def test_canonical_and_yaml_include_x_tools() -> None:
    assert "x_post" in CANONICAL_DANGER_TOOLS
    assert "x_delete_post" in CANONICAL_DANGER_TOOLS
    import yaml

    root = Path(__file__).resolve().parents[1]
    data = yaml.safe_load((root / "kazma.yaml").read_text(encoding="utf-8"))
    listed = set(data["safety"]["hitl"]["require_approval_for"])
    assert listed == set(CANONICAL_DANGER_TOOLS)


def test_always_hitl_survives_yolo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "kazma_core.safety.yolo.is_yolo_active", lambda tid: True
    )
    from kazma_core.safety.hitl import set_current_thread_id

    tok = set_current_thread_id("thread-x")
    try:
        cfg = {"enabled": True, "require_approval_for": set(CANONICAL_DANGER_TOOLS)}
        assert requires_approval("x_post", cfg) is True
        assert requires_approval("x_delete_post", cfg) is True
        assert requires_approval("x_schedule_post", cfg) is True
        assert requires_approval("x_cancel_scheduled_post", cfg) is True
        assert requires_approval("file_write", cfg) is False  # YOLO skips ordinary danger
        assert ALWAYS_HITL_TOOLS == frozenset({
            "x_post",
            "x_delete_post",
            "x_schedule_post",
            "x_cancel_scheduled_post",
        })
    finally:
        from kazma_core.safety.hitl import reset_current_thread_id

        reset_current_thread_id(tok)


def test_always_hitl_when_hitl_disabled() -> None:
    cfg = {"enabled": False, "require_approval_for": set()}
    assert requires_approval("x_post", cfg) is True
    assert requires_approval("shell_exec", cfg) is False


def test_side_effects_x_outbound() -> None:
    from kazma_core.safety.side_effects import (
        EffectKind,
        SemanticTier,
        get_effect_profile,
    )

    p = get_effect_profile("x_post")
    assert p.effect == EffectKind.OUTBOUND
    assert p.semantic_tier == SemanticTier.CRITICAL
    assert p.act == "send_outbound"


def _cfg(**overrides):
    from kazma_core.x_api.config import XConfig, XCredentials

    creds = XCredentials("k", "ks", "t", "ts")
    base = dict(
        enabled=True,
        handle="@kazma",
        credentials=creds,
        max_posts_per_day=8,
        max_posts_per_month=80,
        max_mentions=2,
        max_cashtags=1,
        max_hashtags=4,
        max_chars=280,
        duplicate_window_days=30,
        kill_switch=False,
    )
    base.update(overrides)
    return XConfig(**base)


def test_policy_denies_over_length_mentions_cashtags(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ledger = XPostLedger(tmp_path / "x.db")
    monkeypatch.setattr("kazma_core.x_api.policy.get_ledger", lambda: ledger)
    cfg = _cfg()
    assert evaluate_post("hello from kazma", cfg=cfg).allow is True
    assert evaluate_post("x" * 281, cfg=cfg).allow is False
    many = "hi @a @b @c friends"
    d = evaluate_post(many, cfg=cfg)
    assert d.allow is False
    assert "mention" in d.reason.lower()
    cash = "buy $AAA $BBB now"
    d2 = evaluate_post(cash, cfg=cfg)
    assert d2.allow is False
    assert evaluate_post("", cfg=cfg).allow is False


def test_policy_duplicate_and_quota(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ledger = XPostLedger(tmp_path / "x.db")
    monkeypatch.setattr("kazma_core.x_api.policy.get_ledger", lambda: ledger)
    cfg = _cfg(max_posts_per_day=1)
    text = "Kazma shipped official X posting"
    assert evaluate_post(text, cfg=cfg).allow is True
    ledger.record(tweet_id="1", text=text, handle="@kazma")
    dup = evaluate_post(text, cfg=cfg)
    assert dup.allow is False
    assert "duplicate" in dup.reason.lower()
    other = evaluate_post("A different legal tweet", cfg=cfg)
    assert other.allow is False
    assert "daily" in other.reason.lower()


def test_policy_kill_switch() -> None:
    d = evaluate_post("hello", cfg=_cfg(kill_switch=True))
    assert d.allow is False
    assert "KAZMA_X_POST" in d.reason


def test_sensitive_x_keys() -> None:
    from kazma_core.config_store import is_sensitive_config_key

    for k in (
        "connectors.x.api_key",
        "connectors.x.api_key_secret",
        "connectors.x.access_token",
        "connectors.x.access_token_secret",
    ):
        assert is_sensitive_config_key(k) is True
    assert is_sensitive_config_key("connectors.x.handle") is False


def test_native_manifest_and_loader() -> None:
    from kazma_core.agent.tool_registry import LocalToolRegistry

    reg = LocalToolRegistry()
    names = set(reg._tools.keys())
    assert "x_post" in names
    assert "x_delete_post" in names
    assert "x_status" in names


@pytest.mark.asyncio
async def test_x_post_blocked_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    from kazma_skills.native.x_publisher import tools as t

    monkeypatch.setattr(t, "get_x_config", lambda: _cfg(enabled=False))
    out = await t.x_post("hello world from kazma")
    assert '"ok": false' in out
    assert '"posted": false' in out


def test_settings_tab_and_api_wired() -> None:
    root = Path(__file__).resolve().parents[1]
    html = (root / "kazma-ui" / "kazma_ui" / "templates" / "settings.html").read_text(encoding="utf-8")
    js = (root / "kazma-ui" / "kazma_ui" / "static" / "js" / "settings_integrations.js").read_text(encoding="utf-8")
    app = (root / "kazma-ui" / "kazma_ui" / "app.py").read_text(encoding="utf-8")
    assert 'tab === \'x\'' in html or 'tab === "x"' in html
    assert "/api/x/credentials" in js
    assert "X API router mounted" in app
    # Captions must sit beside the 36px switch, not inside it (overflow clip).
    assert 'class="toggle-row"' in html
    assert "settings.x_show_keys" in html
    assert "settings.x_enabled" in html
