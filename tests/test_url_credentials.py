"""A URL that carries a password is a credential, whatever its key is called.

Live 2026-09-25: ``memory.backends.state.url`` held the Postgres DSN, password
included, in plaintext in ``kazma_settings``, and ``GET
/api/settings/memory/backends`` returned it — and the ``vector.url`` Kazma
fills in from ``KAZMA_DATABASE_URL`` — to the browser unmasked. Every guard
decided by KEY name: ``is_sensitive_config_key`` knows ``*.database_url`` and
``*.postgres_url``, not ``state.url``; ``mask_backends_cfg`` masks
``api_key``/``password``/``token``/``secret``; ``settings.mask_deep`` asks the
same name rules; the ``Setting updated`` log line redacted by name too, and a
DSN is shorter than its 80-character cut.

The rule now: a password inside a URL is masked on every way out (API, log)
by VALUE; a masked URL posted back is refused like any masked secret; and the
value goes to the vault where every reader can find it there — names under
an install-scoped prefix (``cfg:memory.backends.``, AGENTS §38).
"""

from __future__ import annotations

import logging

import pytest
from kazma_core import config_store as cs_mod
from kazma_core.config_store import is_masked_secret_placeholder
from kazma_core.security import vault as vault_mod
from kazma_core.security.url_credentials import (
    mask_url_credentials,
    mask_urls_in_text,
    url_has_credentials,
    url_password_is_masked,
)

DSN = "postgresql://kazma:s3cret-pw@127.0.0.1:5432/kazma"
MASKED = "postgresql://kazma:****@127.0.0.1:5432/kazma"


# ── the value rule ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("value", "masked"),
    [
        (DSN, MASKED),
        ("postgresql://u:p%40ss@h/db", "postgresql://u:****@h/db"),
        # Not RFC (an '@' in a password must be encoded), but libpq takes the
        # last '@' of the authority, and so must the mask.
        ("postgresql://u:p@ss@h:5432/db", "postgresql://u:****@h:5432/db"),
        ("redis://:hunter2@cache:6379/0", "redis://:****@cache:6379/0"),
        ("postgresql://h/db?user=u&password=pw&sslmode=require",
         "postgresql://h/db?user=u&password=****&sslmode=require"),
        ("mongodb+srv://app:pw@cluster.example.net/db", "mongodb+srv://app:****@cluster.example.net/db"),
    ],
)
def test_the_password_in_a_url_is_masked(value, masked):
    assert url_has_credentials(value)
    assert mask_url_credentials(value) == masked
    assert "pw" not in mask_url_credentials(value).replace("password=", "")


@pytest.mark.parametrize(
    "value",
    [
        "postgresql://kazma@127.0.0.1/kazma",  # user, no password
        "http://qdrant:6333",
        "bolt://localhost:7687",
        "https://api.example.com/v1?page=2",
        "not a url at all",
        "",
        "user:pass@host",  # no scheme: not a URL we can read safely
    ],
)
def test_a_url_without_a_password_is_left_alone(value):
    assert not url_has_credentials(value)
    assert mask_url_credentials(value) == value


def test_non_strings_pass_through():
    for value in (None, 5, True, {"url": DSN}, [DSN]):
        assert mask_url_credentials(value) is value
        assert url_has_credentials(value) is False


def test_a_masked_url_is_a_placeholder_and_a_real_one_is_not():
    """ConfigStore refuses to write a placeholder over a stored secret."""
    assert is_masked_secret_placeholder(MASKED)
    assert is_masked_secret_placeholder("postgresql://kazma:***@127.0.0.1:5432/kazma")
    assert is_masked_secret_placeholder("postgresql://h/db?password=***")
    assert not is_masked_secret_placeholder(DSN)
    assert not is_masked_secret_placeholder("http://qdrant:6333")
    # A real password that merely contains stars is not a mask.
    assert not url_password_is_masked("postgresql://u:a***b@h/db")


def test_urls_inside_free_text_are_masked():
    """A repr, a JSON blob, a log line: the value is not always a bare URL."""
    text = repr({"state": {"url": DSN}, "other": "redis://:hunter2@cache:6379/0"})
    out = mask_urls_in_text(text)
    assert "s3cret-pw" not in out and "hunter2" not in out
    assert "127.0.0.1:5432/kazma" in out and "cache:6379" in out
    assert "password=****" in mask_urls_in_text("dsn=postgresql://h/db?password=pw x")
    assert mask_urls_in_text("no urls here") == "no urls here"
    assert mask_urls_in_text("https://host:443/path@x") == "https://host:443/path@x"


# ── every way out ─────────────────────────────────────────────────────


def test_the_setting_update_log_line_masks_the_url():
    line = cs_mod._redact_for_log("memory.backends.state.url", DSN)
    assert "s3cret-pw" not in line
    assert "127.0.0.1" in line  # the rest stays readable


def test_mask_backends_cfg_masks_urls_by_value():
    from kazma_core.memory.backends import mask_backends_cfg

    cfg = {
        "mode": "hybrid",
        "vector": {"provider": "pgvector", "url": DSN},
        "state": {"provider": "postgres", "url": DSN},
        "graph": {"provider": "neo4j", "url": "bolt://localhost:7687", "password": "neo"},
        "embedder": {"provider": "local"},
        "failover": {},
    }
    out = mask_backends_cfg(cfg)
    assert out["vector"]["url"] == MASKED
    assert out["state"]["url"] == MASKED
    assert out["graph"]["url"] == "bolt://localhost:7687"
    assert out["graph"]["password"] == "***"
    assert cfg["state"]["url"] == DSN  # the input is not modified


def test_mask_deep_masks_urls_by_value():
    """GET /api/settings: a DSN under a key no name rule knows."""
    import json

    from kazma_ui.settings import mask_deep

    out = mask_deep({
        "memory.backends.state.url": DSN,
        "nested": {"list": [DSN, "http://plain:1"]},
        "as_json": json.dumps({"url": DSN}),
    })
    assert out["memory.backends.state.url"] == MASKED
    assert out["nested"]["list"] == [MASKED, "http://plain:1"]
    assert "s3cret-pw" not in out["as_json"]


def test_the_memory_backends_route_never_returns_the_password(monkeypatch):
    from types import SimpleNamespace

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from kazma_core.memory import backends
    from kazma_ui.routes_direct.settings import register_settings_routes

    cfg = {
        "mode": "hybrid",
        "vector_auto": True,
        "vector": {"provider": "pgvector", "url": DSN, "collection": "kazma_memory"},
        "state": {"provider": "postgres", "url": DSN, "role": "mirror"},
        "graph": {"provider": "sqlite"},
        "embedder": {"provider": "local"},
        "failover": {"on_remote_error": "local"},
    }
    monkeypatch.setattr(backends, "get_backends_cfg", lambda: cfg)
    monkeypatch.setattr("kazma_core.memory.graph_backend.graph_capability", lambda c: {})
    monkeypatch.setattr("kazma_core.memory.state_backend.state_capability", lambda c: {})
    app = FastAPI()
    register_settings_routes(SimpleNamespace(app=app))
    body = TestClient(app).get("/api/settings/memory/backends").text
    assert "s3cret-pw" not in body
    assert "127.0.0.1" in body


# ── storage ───────────────────────────────────────────────────────────


@pytest.fixture
def vault(tmp_path, monkeypatch):
    monkeypatch.setenv("KAZMA_VAULT_KEY", "k" * 64)
    v = vault_mod.SecretVault(db_path=str(tmp_path / "vault.db"))
    monkeypatch.setattr(vault_mod, "get_vault", lambda: v)
    yield v
    v.close()


@pytest.fixture
def production(monkeypatch):
    """The live posture: the ``default`` rung is closed to context-less reads."""
    monkeypatch.setenv("KAZMA_PRODUCTION", "1")
    vault_mod.reset_posture_cache()
    yield
    vault_mod.reset_posture_cache()


@pytest.fixture
def store(tmp_path, vault):
    from kazma_core.config_store import ConfigStore

    s = ConfigStore(db_path=str(tmp_path / "settings.db"), yaml_path=str(tmp_path / "none.yaml"))
    yield s
    s.close()


def _raw(store, key):
    return store._stored_raw(key)


def test_a_dsn_saved_in_settings_goes_to_the_vault_for_every_reader(store, vault, production):
    """Saved through a request with a tenant; read by the memory worker without one."""
    from kazma_core.tenant_context import tenant_scope

    with tenant_scope("default"):
        store.set("memory.backends.state.url", DSN, category="memory")
    assert cs_mod.is_vault_ref(_raw(store, "memory.backends.state.url"))
    with tenant_scope(None):
        assert store.get("memory.backends.state.url") == DSN


def test_a_plaintext_dsn_already_stored_is_moved_on_first_read(store, vault, production):
    """The live row: written before this rule, sitting in plaintext."""
    store._write_db_value("memory.backends.state.url", DSN, category="memory")
    assert _raw(store, "memory.backends.state.url") == DSN
    assert store.get("memory.backends.state.url") == DSN
    assert cs_mod.is_vault_ref(_raw(store, "memory.backends.state.url"))
    assert store.get("memory.backends.state.url") == DSN


def test_a_url_without_a_password_stays_readable(store, vault):
    store.set("memory.backends.graph.url", "bolt://localhost:7687", category="memory")
    assert _raw(store, "memory.backends.graph.url") == "bolt://localhost:7687"


def test_a_masked_url_posted_back_keeps_the_stored_one(store, vault):
    store.set("memory.backends.state.url", DSN, category="memory")
    store.set("memory.backends.state.url", MASKED, category="memory")
    assert store.get("memory.backends.state.url") == DSN
    store.batch_set([("memory.backends.state.url", MASKED, "memory")])
    assert store.get("memory.backends.state.url") == DSN


def test_a_credential_url_outside_the_install_scope_is_not_moved(store, vault):
    """A per-tenant vault copy is invisible to background readers (§38), so a
    credential URL under a name that is not install-scoped stays put — it is
    still masked on every way out."""
    store.set("research.searx.url", "https://u:pw@searx.example/", category="research")
    assert _raw(store, "research.searx.url") == "https://u:pw@searx.example/"


def test_the_save_log_line_carries_no_password(store, vault, caplog):
    caplog.set_level(logging.INFO, logger="kazma_core.config_store")
    store.set("research.searx.url", "https://u:pw-123@searx.example/", category="research")
    assert not any("pw-123" in r.getMessage() for r in caplog.records)


def test_the_form_posted_back_saves_only_what_the_operator_changed(monkeypatch):
    """GET shows what Kazma filled in (pgvector picked from the DSN, the DSN it
    borrowed, the mode that goes with it). Posting the form back unchanged
    must not make those the operator's choices, nor copy the DSN."""
    from kazma_core.memory import backends

    # The live shape: the operator chose sqlite-vec and a Postgres state mirror.
    stored = {
        "memory.backends.mode": "local",
        "memory.backends.vector.provider": "sqlite_vec",
        "memory.backends.state.provider": "postgres",
        "memory.backends.state.url": DSN,
    }
    written: list[tuple[str, object]] = []

    class _Store:
        def get(self, key, default=None):
            return stored.get(key, default)

        def batch_set(self, items):
            written.extend((k, v) for k, v, _c in items)

    monkeypatch.setattr("kazma_core.config_store.get_config_store", lambda: _Store())
    monkeypatch.delenv("KAZMA_PGVECTOR", raising=False)
    shown = backends.mask_backends_cfg(backends.get_backends_cfg())
    assert shown["vector"]["provider"] == "pgvector" and shown["vector"]["url"] == MASKED
    assert shown["state"]["url"] == MASKED
    assert shown["mode"] == "hybrid"

    backends.save_backends_cfg({k: shown[k] for k in ("mode", "vector", "state", "failover")})
    keys = {k for k, _v in written}
    assert "memory.backends.vector.provider" not in keys
    assert "memory.backends.vector.url" not in keys
    assert "memory.backends.mode" not in keys
    assert "memory.backends.state.url" not in keys  # masked: the stored DSN stays
    assert not any("s3cret-pw" in str(v) or "****" in str(v) for _k, v in written)

    # A real change is saved.
    written.clear()
    changed = dict(shown, mode="remote")
    backends.save_backends_cfg({k: changed[k] for k in ("mode", "vector")})
    assert ("memory.backends.mode", "remote") in written


def test_memory_backend_secrets_are_install_scoped():
    """Every ``memory.backends`` secret is read by one process-wide backend."""
    for name in (
        "cfg:memory.backends.state.url",
        "cfg:memory.backends.vector.url",
        "cfg:memory.backends.graph.password",
        "cfg:memory.backends.embedder.api_key",
    ):
        assert vault_mod.is_install_scoped_secret(name), name


# ── the class: every masker that decides by key name ─────────────────
#
# Each one above was a correct rule for keys that missed the value. The
# census enumerates them from the product source, so a new masker cannot
# ship without a probe that feeds it a password inside a URL.

import ast
import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TOKEN_URL = "https://x-access-token:s3cret-pw@github.com/owner/repo.git"

_SECRET_WORDS = {
    "api_key", "apikey", "password", "secret", "token", "passphrase",
    "private_key", "authorization",
}
_KEY_CLASSIFIER = re.compile(r"^(is_sensitive\w*|key_is_secret|_is_secret_key)$")


def _decides_by_key_name(fn: ast.AST) -> bool:
    for node in ast.walk(fn):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value.lower() in _SECRET_WORDS:
                return True
        if isinstance(node, ast.Name) and re.search(r"SENSITIVE|REDACT_KEYS", node.id):
            return True
        if isinstance(node, ast.Call):
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", "")
            if _KEY_CLASSIFIER.match(name or ""):
                return True
    return False


def key_name_maskers(sources: dict[str, str]) -> set[str]:
    """``path::qualname`` of every function named mask*/redact* that decides by key."""
    found: set[str] = set()

    def visit(node: ast.AST, path: str, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qual = f"{prefix}.{child.name}" if prefix else child.name
                if re.search(r"mask|redact", child.name, re.I) and _decides_by_key_name(child):
                    found.add(f"{path}::{qual}")
                visit(child, path, qual)
            elif isinstance(child, ast.ClassDef):
                visit(child, path, f"{prefix}.{child.name}" if prefix else child.name)
            else:
                visit(child, path, prefix)

    for path, text in sources.items():
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        visit(tree, path, "")
    return found


def _product_sources() -> dict[str, str]:
    files = subprocess.run(
        ["git", "ls-files", "kazma-*/*.py", "kazma-*/**/*.py"],
        cwd=REPO, capture_output=True, text=True, check=True,
    ).stdout.split()
    return {
        f: (REPO / f).read_text(encoding="utf-8", errors="replace")
        for f in files
        if "/tests/" not in f and "_tests" not in f and (REPO / f).is_file()
    }


def _probe_mask_deep():
    from kazma_ui.settings import mask_deep

    return mask_deep({"cfg": {"endpoint": DSN, "cmd": [TOKEN_URL]}})


def _probe_mask_backends_cfg():
    from kazma_core.memory.backends import mask_backends_cfg

    return mask_backends_cfg({
        "mode": "hybrid", "vector": {"url": DSN}, "state": {"url": DSN},
        "graph": {"url": TOKEN_URL}, "embedder": {"base_url": TOKEN_URL}, "failover": {},
    })


def _probe_redact_for_log():
    return cs_mod._redact_for_log("x.endpoint", {"a": DSN, "b": TOKEN_URL})


def _probe_settings_export():
    from kazma_core.settings_manager import SettingsManager

    data = {"memory": {"memory.backends.state.url": DSN, "x.list": [TOKEN_URL]}}
    SettingsManager._mask_secrets_in_dict(data)
    return data


def _probe_config_export_command():
    from kazma_gateway.slash_commands import _redact_secrets

    return _redact_secrets({"database": {"url": DSN}, "cmd": ["git", "clone", TOKEN_URL]})


def _probe_approval_card():
    from kazma_gateway.agent_handler.hitl import _build_approval_prompt

    return _build_approval_prompt(
        {"tool": "shell_exec", "args": {"command": f"git clone {TOKEN_URL}", "dsn": DSN}},
        "thread-probe",
    )


def _probe_pg_argv():
    from kazma_core.migration.pg_bridge import _redact_cmd

    return _redact_cmd(["pg_dump", "--dbname", DSN])


def _probe_provider_entry():
    from kazma_ui.providers import _mask_provider_entry

    return _mask_provider_entry({"name": "p", "base_url": DSN, "api_key": "k-123456"})


def _probe_connector_entry():
    from kazma_ui.providers import _mask_connector_entry

    return _mask_connector_entry("slack", {"token": "xoxb-1", "webhook_url": DSN})


def _probe_profile():
    from kazma_core.model_registry import ModelRegistry

    return ModelRegistry._mask_profile(None, {"base_url": DSN, "api_key": "k"})  # type: ignore[arg-type]


PROBES = {
    "kazma-ui/kazma_ui/settings.py::mask_deep": _probe_mask_deep,
    "kazma-core/kazma_core/memory/backends.py::mask_backends_cfg": _probe_mask_backends_cfg,
    "kazma-core/kazma_core/config_store.py::_redact_for_log": _probe_redact_for_log,
    "kazma-core/kazma_core/settings_manager.py::SettingsManager._mask_secrets_in_dict": _probe_settings_export,
    "kazma-gateway/kazma_gateway/slash_commands.py::_redact_secrets": _probe_config_export_command,
    "kazma-gateway/kazma_gateway/agent_handler/hitl.py::_build_approval_prompt._redact": _probe_approval_card,
    "kazma-core/kazma_core/migration/pg_bridge.py::_redact_cmd": _probe_pg_argv,
    # Masked since 2026-09-26: their saves restore an unchanged masked URL and
    # refuse a changed one (tests/test_url_password_round_trip.py).
    "kazma-ui/kazma_ui/providers.py::_mask_provider_entry": _probe_provider_entry,
    "kazma-ui/kazma_ui/providers.py::_mask_connector_entry": _probe_connector_entry,
    "kazma-core/kazma_core/model_registry.py::ModelRegistry._mask_profile": _probe_profile,
}

#: Found by the census and deliberately not probed — each with its reason.
NOT_PROBED: dict[str, str] = {}


def test_every_key_name_masker_also_masks_url_passwords():
    found = key_name_maskers(_product_sources())
    unlisted = sorted(found - PROBES.keys() - NOT_PROBED.keys())
    assert not unlisted, (
        "A function masks by key name and has no probe. A key rule cannot see a "
        "password inside a URL value (state.url held the live DSN): mask the value "
        "with kazma_core.security.url_credentials and add a probe to PROBES:\n  "
        + "\n  ".join(unlisted)
    )
    stale = sorted((PROBES.keys() | NOT_PROBED.keys()) - found)
    assert not stale, f"listed but no longer found (renamed?): {stale}"
    leaking = [name for name, probe in PROBES.items() if "s3cret-pw" in str(probe())]
    assert not leaking, f"these still return a URL password: {leaking}"


def test_the_masker_census_sees_a_new_one():
    """Negative control (§28): a planted key-name masker is found."""
    planted = {
        "kazma-x/kazma_x/m.py": (
            "def mask_things(d):\n"
            "    return {k: '***' if k == 'api_key' else v for k, v in d.items()}\n"
            "def mask_via_classifier(d):\n"
            "    return {k: v for k, v in d.items() if not is_sensitive_config_key(k)}\n"
            "def unrelated_mask(d):\n"
            "    return d\n"
        )
    }
    assert key_name_maskers(planted) == {
        "kazma-x/kazma_x/m.py::mask_things",
        "kazma-x/kazma_x/m.py::mask_via_classifier",
    }


@pytest.mark.postgres
def test_the_live_shape_on_a_real_postgres(tmp_path, monkeypatch, vault, production):
    """The live row: a plaintext DSN in ``kazma_settings`` on Postgres, read
    first by a request and then by the memory worker with no tenant.
    Verified on a throwaway postgres:16. The table is shared by the whole
    job, so the key is unique and removed at the end."""
    import os
    import uuid

    if not (os.environ.get("KAZMA_DATABASE_URL") or "").strip():
        pytest.skip("no Postgres DSN (the CI Postgres job, or a throwaway container)")
    from kazma_core.config_store import ConfigStore
    from kazma_core.tenant_context import tenant_scope

    key = f"memory.backends.probe_{uuid.uuid4().hex[:8]}.url"
    s = ConfigStore(db_path=str(tmp_path / "settings.db"), yaml_path=str(tmp_path / "none.yaml"))
    try:
        assert s._use_postgres(), "the job's ConfigStore must be the Postgres one"
        s._write_db_value(key, DSN, category="memory")
        assert s._stored_raw(key) == DSN
        with tenant_scope("default"):
            assert s.get(key) == DSN  # the read that moves it
        s._cache.clear()
        assert cs_mod.is_vault_ref(s._stored_raw(key)), "the row must now be a pointer"
        with tenant_scope(None):
            assert s.get(key) == DSN  # the memory worker's read
    finally:
        s.delete(key)
        s.close()
