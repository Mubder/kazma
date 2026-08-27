"""MCP classifier hardening (audit MED-HIGH): mutator vocabulary blend.

Regression: ``_DANGER_KEYWORDS`` lacked set/save/send/add/apply/push/erase/
destroy/format/flush while ANY safe-token match won, so a blended name like
``mcp__kv__query_set`` classified SAFE and skipped the HITL gate. The danger
check now includes the canonical side_effects mutator vocabulary (whole-token
match) and DANGER WINS whenever any danger/mutator token appears.

These tests double as the cross-list parity gate: every ``side_effects``
mutator token must classify NON-SAFE through ``classify_mcp_tool``.
"""

from __future__ import annotations

from kazma_core.mcp.manager import classify_mcp_tool


def _tpl_names(token: str) -> list[str]:
    """Blends of the token with safe verbs/nouns — old code bled these SAFE."""
    return [
        f"mcp__kv__query_{token}",
        f"mcp__kv__{token}_record",
        f"mcp__kv__get_{token}_item",
        f"mcp__kv__list_{token}",
    ]


def test_mutator_parity_all_tokens_non_safe() -> None:
    """Parity: every side_effects._MUTATORS token classifies non-safe."""
    from kazma_core.safety.side_effects import _MUTATOR_TOKENS

    offenders: list[str] = []
    for token in sorted(_MUTATOR_TOKENS):
        for name in _tpl_names(token):
            if classify_mcp_tool(name) == "safe":
                offenders.append(name)
    assert not offenders, f"mutator tokens classified SAFE: {offenders}"


def test_manager_fallback_mirror_matches_canonical() -> None:
    """The import-degradation mirror must not drift from the SoT."""
    from kazma_core.mcp.manager import _MUTATOR_TOKENS_FALLBACK
    from kazma_core.safety.side_effects import _MUTATOR_TOKENS

    assert set(_MUTATOR_TOKENS_FALLBACK) == set(_MUTATOR_TOKENS)


def test_query_set_example_is_gated() -> None:
    """The audited example: kv.query_set must NOT classify safe."""
    assert classify_mcp_tool("mcp__kv__query_set") == "danger"
    # Whole-token matching: unrelated names containing similar substrings stay
    # out of the mutator path unless another danger signal exists.
    assert classify_mcp_tool("mcp__kv__asset_subset") != "danger"


def test_safe_tools_still_classify_safe() -> None:
    """No regression on pure read verbs (HITL stays off for them)."""
    for name in (
        "read_file",
        "list_tables",
        "search_docs",
        "get_status",
        "directory_tree",
        "mcp__fs__read_text",     # server slug never bleaches/dangers the leaf
        "mcp__svc__describe_table",
    ):
        assert classify_mcp_tool(name) == "safe", name


def test_unknown_still_fail_closed() -> None:
    """Unknown → 'unknown' (callers gate it); classification unchanged."""
    assert classify_mcp_tool("frobnicate") == "unknown"
    assert classify_mcp_tool("widget") == "unknown"


def test_secret_read_blend_still_danger() -> None:
    """Pre-existing H6 behavior: secret tokens win over safe verbs."""
    assert classify_mcp_tool("get_api_key") == "danger"
