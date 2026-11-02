"""The agent cannot change config that would disarm it or run code
(audit 2026-09-30, AUD-007).

Two lists guarded this before, and neither covered ``agent.hooks.*`` (shell
commands run on every tool call) or ``mcp.servers`` (server commands spawned
by the MCP manager): the agent could write its own persistent command
execution through a config change. One list now, used by ``config_save`` and
the commitment ``config_change`` resolver.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from kazma_core.safety.protected_config import (
    PROTECTED_CONFIG_PREFIXES,
    is_protected_config_key,
)

from tests._tool_answer import answer_now

REPO = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("key", [
    "agent.hooks.pre_tool", "agent.hooks.post_tool", "agent.hooks.enabled",
    "mcp.servers", "mcp.oauth.github.client_secret",
    "safety.require_approval_for", "safety.hitl_enabled",
    "agent.commitment.mode", "yolo.window", "yolo",
    "security.secret", "kazma_secret", "vault.master",
    "notifications.lifecycle.events",
    # Where the universal backup (with .env and vault.db) goes and what it
    # holds (audit 2026-10-26 AUD-094).
    "backups.offsite.provider", "backups.offsite.webdav.url",
    "backups.offsite.s3.endpoint", "backups.offsite.ftp.host",
    "backups.offsite.enabled", "backups.extra_paths",
    "tenant.acme.backups.offsite.webdav.url",
    # Not settings at all (settings_restore.classify, 2026-10-01): approval
    # grants, sessions, the learned Soul.
    "task_grant.t1", "hitl_grant.t1.shell_exec", "path_grant.t1.g1",
    "web_session.abc", "account.password_hash", "self_improvement.agent_evolution",
    "long_task.t1", "platform.users",
    # A tenant's copy is the same key (tenant_isolation.tenant_key).
    "tenant.acme.mcp.servers", "tenant.acme.safety.hitl_enabled",
    # Where secrets and memory go, and who may give Kazma orders (2026-11,
    # FA-126): each key the audit's reproduction found open.
    "embedding.provider", "embedding.base_url", "embedding.api_key_env",
    "memory.backends.state.url", "providers.list", "registry.active_model",
    "registry.active_provider", "proxy.host", "proxy.provider",
    "documents.security.remote_parse", "documents.storage_root",
    "connectors.telegram.allowed_users", "connectors.discord.allowed_users",
    "connectors.slack.allowed_channels",
    # ...and by shape, whatever the section.
    "llm.base_url", "llm.gateway.url", "ui.host", "voice.livekit.url",
    "voice.stt_base_url", "time_travel.db_path", "memory.embedding.api_key_env",
])
def test_command_and_safety_keys_are_protected(key) -> None:
    assert is_protected_config_key(key) is True


@pytest.mark.parametrize("key", [
    "agent.personality", "agent.language", "agent.max_iterations",
    "agent.environment", "models.defaults.chat", "", "   ",
    # Backup settings that do not move or widen the archive stay writable.
    "backups.pg.retention",
])
def test_ordinary_keys_are_not_protected(key) -> None:
    assert is_protected_config_key(key) is False


def test_config_save_blocks_a_hook_and_an_mcp_server(monkeypatch) -> None:
    """The tool refuses the keys that carry commands, whatever it is handed."""
    from kazma_core.agent.tool_registry import LocalToolRegistry

    reg = LocalToolRegistry()
    from kazma_core.agent.tool_builtins.system import register_system_tools

    register_system_tools(reg)
    save = None
    for name in ("config_save",):
        save = reg._tools.get(name) if hasattr(reg, "_tools") else None
    assert save is not None, "config_save is registered"
    fn = save.func
    for key in ("agent.hooks.post_tool", "mcp.servers", "safety.hitl_enabled",
                "task_grant.t1", "tenant.acme.mcp.servers",
                "backups.offsite.webdav.url", "backups.extra_paths"):
        out = answer_now(fn, key=key, value='["echo x"]')
        assert out.startswith("Error: Cannot modify restricted key"), (key, out)
    # The value checks the Settings page applies (settings_validation).
    bad = fn(key="cron.timezone", value="Mars/Olympus")
    assert bad.startswith("Error: Invalid timezone"), bad
    ok = fn(key="agent.personality", value="cheerful")
    assert ok.startswith("Setting saved"), ok


def _string_prefix_tuples(path: str) -> list[tuple[str, ...]]:
    """Every tuple-of-string-constants literal in *path* (a hand-rolled
    protected/blocked list would be one)."""
    tree = ast.parse((REPO / path).read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Tuple) and len(node.elts) >= 3 and all(
            isinstance(e, ast.Constant) and isinstance(e.value, str) for e in node.elts
        ):
            found.append(tuple(e.value for e in node.elts))
    return found


def test_neither_call_site_keeps_its_own_list() -> None:
    """Both import the shared predicate; a re-introduced local list of these
    prefixes fails here."""
    for path in (
        "kazma-core/kazma_core/agent/tool_builtins/system.py",
        "kazma-core/kazma_core/safety/commitment/authorize.py",
    ):
        src = (REPO / path).read_text(encoding="utf-8")
        assert "is_protected_config_key" in src, path
        for tup in _string_prefix_tuples(path):
            overlap = {t.rstrip(".") for t in tup} & {p.rstrip(".") for p in PROTECTED_CONFIG_PREFIXES}
            assert len(overlap) < 3, f"{path} re-declares a protected-key list: {tup}"


def test_the_commitment_resolver_denies_a_hook_write() -> None:
    from kazma_core.safety.commitment import authorize

    key = "agent.hooks.post_tool"
    assert authorize.is_protected_config_key(key) is True


# ── The class (FA-126) ──────────────────────────────────────────────────

#: A settings key's LAST segment that says its value is a destination or a
#: gate. Written apart from the product's rule on purpose: the gate checks
#: the rule, it does not reuse it.
_SHAPE = __import__("re").compile(
    r"(^|_)(url|urls|base_url|uri|dsn|host|hosts|endpoint|env|allowlist|storage_root|db_path)$|^allowed_"
)
_URL_VALUE = __import__("re").compile(r"^(https?|wss?|postgres(ql)?|redis|bolt|neo4j)://")

#: Destination-shaped keys the agent may still write, with why. Empty: every
#: one is the operator's.
AGENT_MAY_WRITE: dict[str, str] = {}


def _known_setting_keys() -> dict[str, object]:
    """Every settings key Kazma ships (kazma.yaml) or a Settings control
    writes, with its shipped value when it has one."""
    from kazma_core.config_loader import load_merged_yaml
    from kazma_core.config_store import shipped_settings

    from tests.test_settings_keys_have_readers import _files, expand, written_keys

    keys: dict[str, object] = {k: v for k, v, _c in shipped_settings(load_merged_yaml(REPO / "kazma.yaml"))}
    for k in expand(written_keys(_files())):
        keys.setdefault(k.replace("{}", "x"), None)
    return keys


def test_every_destination_key_is_protected() -> None:
    keys = _known_setting_keys()
    assert len(keys) > 100  # the enumeration found the settings
    shaped = sorted(k for k, v in keys.items()
                    if _SHAPE.search(k.rsplit(".", 1)[-1])
                    or (isinstance(v, str) and _URL_VALUE.match(v)))
    assert shaped, "the shape found nothing: the enumeration is broken"
    open_keys = [k for k in shaped if not is_protected_config_key(k) and k not in AGENT_MAY_WRITE]
    assert not open_keys, (
        "keys whose value is a URL, host, variable name or allowlist that the agent may "
        "write (FA-126); protect them or declare them in AGENT_MAY_WRITE: " + ", ".join(open_keys))


def test_the_old_list_left_the_embedders_key_open() -> None:
    """Negative control: the prefixes before FA-126, with no shape rule."""
    from kazma_core.settings_validation import base_key

    old = ("safety.", "agent.commitment.", "agent.hooks.", "mcp.", "notifications.lifecycle.",
           "backups.offsite.", "backups.extra_paths", "security.", "vault.", "yolo.", "kazma_secret")

    def old_protected(key: str) -> bool:
        k = base_key(key)
        return any(k == p.rstrip(".") or k.startswith(p) for p in old)

    assert not old_protected("embedding.api_key_env")
    assert not old_protected("connectors.telegram.allowed_users")
    assert is_protected_config_key("embedding.api_key_env")


def test_the_embedders_key_never_names_kazmas_own_variables(monkeypatch) -> None:
    """Even a stored value from before (or an owner's typo): the embedder does
    not send ``KAZMA_VAULT_KEY`` anywhere, and the setting refuses it."""
    from kazma_core.memory import embedder
    from kazma_core.settings_validation import SettingRejected, validate_setting

    with pytest.raises(SettingRejected):
        validate_setting("embedding.api_key_env", "KAZMA_VAULT_KEY")
    assert validate_setting("embedding.api_key_env", "NVIDIA_API_KEY")[0] == "NVIDIA_API_KEY"
    assert validate_setting("embedding.api_key_env", "KAZMA_EMBED_API_KEY")[0] == "KAZMA_EMBED_API_KEY"
    assert embedder.embedding_key_env_allowed("KAZMA_DATABASE_URL") is False

