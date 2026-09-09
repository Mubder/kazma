"""Unit tests for telegram_callbacks.parse_callback_data."""

from __future__ import annotations

from kazma_gateway.adapters.telegram_callbacks import parse_callback_data


def test_hitl():
    a = parse_callback_data("hitl:approve:tid-1")
    assert a.kind == "hitl"
    assert a.text == "/hitl approve tid-1"


def test_swarm_in_process():
    a = parse_callback_data("swarm_approve_abc")
    assert a.kind == "swarm"
    # The raw callback data rides to the swarm bus (swarm_data), which each
    # adapter resolves in-process — the old handled_in_process flag was
    # removed from CallbackAction.
    assert a.swarm_data == "swarm_approve_abc"


def test_model_and_personality():
    assert parse_callback_data("personality:sage").text == "/personality sage"
    assert "openai" in parse_callback_data("model_provider:openai").text
    assert "gpt" in parse_callback_data("model_select:gpt-4o").text


def test_sys_install():
    a = parse_callback_data("sys_install:numpy")
    assert a.kind == "sys_install"
    assert a.package_name == "numpy"
    # sys_install is handled in-process by the adapter (admin-gated) — the
    # old handled_in_process flag was removed from CallbackAction.
    assert not a.text  # never forwarded as a synthetic message
