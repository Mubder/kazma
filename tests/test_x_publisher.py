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

    monkeypatch.setattr("kazma_core.x_api.config.get_x_config", lambda: _cfg(enabled=False))
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


def test_x_post_schema_carries_proposal_id() -> None:
    """2026-09-02: the commitment gate requires proposal_id, but the tool
    schema (built from the signature) never advertised it — strict-mode
    providers could not emit it and filter_tool_arguments stripped it, so
    the first post attempt was structurally doomed to a deny."""
    from kazma_core.agent.tool_registry import LocalToolRegistry

    reg = LocalToolRegistry()
    for name in ("x_post", "x_schedule_post"):
        tool = reg._tools[name]
        props = (tool.input_schema or {}).get("properties") or {}
        assert "proposal_id" in props, f"{name} schema must declare proposal_id"


def test_manifest_documents_save_first_contract() -> None:
    """The descriptions the model plans from must teach the workflow:
    save_proposal first, one call per item, id wins over context text."""
    import yaml

    root = Path(__file__).resolve().parents[1]
    manifest = yaml.safe_load(
        (
            root
            / "kazma-skills"
            / "kazma_skills"
            / "native"
            / "x_publisher"
            / "skill_manifest.yaml"
        ).read_text(encoding="utf-8")
    )
    for name in ("x_post", "x_schedule_post"):
        desc = manifest["tools"][name]["description"]
        assert "save_proposal" in desc
        assert "proposal_id" in desc
        assert "ONCE PER ITEM" in desc


def test_product_knowledge_states_card_delivery_facts() -> None:
    """The 'waves of 3' fiction came from the model free-styling about card
    throttles it half-remembered from gateway code. The system-prompt
    knowledge must state the authoritative delivery behavior."""
    from kazma_core.product_knowledge import build_product_knowledge

    text = build_product_knowledge()
    assert "never invent throttles" in text
    assert "no rate limit" in text
    assert "Never tell the operator to batch posts" in text
    assert "Allow tool (session)" in text


def test_scheduled_fire_binds_booking_tenant(monkeypatch, tmp_path) -> None:
    """2026-09-03: X OAuth keys saved via Settings live in the vault under
    the operator's tenant; the background fire loop has NO request context,
    so vault.retrieve(name) (global-only) missed them and every scheduled
    post died as "connector disabled at fire time" while chat posts worked.
    The fire loop must run under the post's booked tenant."""
    import asyncio

    from kazma_core.x_api import scheduled_fire as sf

    seen: dict[str, object] = {}

    class _Post:
        id = 1
        text = "t"
        reply_to_id = ""
        tenant_id = "default"

    async def _inner(post):
        from kazma_core.tenant_context import get_current_tenant_id

        seen["tenant"] = get_current_tenant_id()

    monkeypatch.setattr(sf, "_fire_post_inner", _inner)
    asyncio.run(sf._fire_post(_Post()))
    assert seen["tenant"] == "default"


def test_vault_get_resolves_tenant_scoped_secrets(monkeypatch) -> None:
    """The credential ladder must see tenant-scoped vault rows from
    context-free callers: current tenant → 'default' → global."""
    from kazma_core.x_api import config as xc

    calls: list[object] = []

    class _Vault:
        def retrieve(self, name, tenant_id=None):
            calls.append(tenant_id)
            return "sekret" if tenant_id == "default" else None

    monkeypatch.setattr("kazma_core.security.vault.get_vault", lambda: _Vault())
    monkeypatch.setattr(
        "kazma_core.tenant_context.get_current_tenant_id", lambda: None
    )
    assert xc._vault_get("cfg:connectors.x.api_key") == "sekret"
    # Short-circuits on the first scope that answers — 'default' before
    # global.
    assert calls[0] == "default"
